import pytest

from backend.app.archive.model import WriteResult
from backend.app.models.annotation import ClipVersion
from backend.app.repositories.clip_versions import ClipVersionsRepo
from backend.app.repositories.pending_operations import PendingOperationsRepo
from backend.app.services.sync_engine import SyncEngine


class _Provider:
    id = "catdv"

    def __init__(self, status):
        self._status = status

    async def apply_changes(self, change_set):
        return WriteResult(status=self._status, upstream_response={}, conflict_detail=None)


class _NoopLog:
    async def record(self, *a, **k):
        pass


async def _enqueue_for_version(db, version_id):
    return await PendingOperationsRepo().insert_many(db, rows=[{
        "provider_id": "catdv", "provider_clip_id": "1", "op_kind": "SetField",
        "op_json": '{"kind":"SetField","identifier":"pragafilm.genre","value":"x"}',
        "origin_annotation_id": None, "origin_review_item_ids": None,
        "expected_etag": None, "origin_clip_version_id": version_id,
    }])


def _engine(db, status):
    return SyncEngine(
        provider=_Provider(status), pending_ops_repo=PendingOperationsRepo(),
        write_log_repo=_NoopLog(), connection_monitor=None, db_provider=lambda: db,
        review_items_repo=None, clip_versions_repo=ClipVersionsRepo(),
    )


def _v(num, state):
    return ClipVersion(
        catdv_clip_id=1, version_num=num, snapshot={"markers": [], "fields": {}, "notes": None},
        origin="publish", publish_state=state)


@pytest.mark.asyncio
async def test_ok_flips_version_live_and_supersedes_prior(db):
    repo = ClipVersionsRepo()
    v1 = await repo.insert(db, _v(1, "live"))
    v2 = await repo.insert(db, _v(2, "publishing"))
    await _enqueue_for_version(db, v2)
    await _engine(db, "ok").drain_once()
    assert (await repo.get(db, v2)).publish_state == "live"
    assert (await repo.get(db, v1)).publish_state == "superseded"


@pytest.mark.asyncio
async def test_conflict_marks_version_conflict_and_keeps_prior_live(db):
    repo = ClipVersionsRepo()
    v1 = await repo.insert(db, _v(1, "live"))
    v2 = await repo.insert(db, _v(2, "publishing"))
    await _enqueue_for_version(db, v2)
    await _engine(db, "conflict").drain_once()
    assert (await repo.get(db, v2)).publish_state == "conflict"
    assert (await repo.get(db, v1)).publish_state == "live"
