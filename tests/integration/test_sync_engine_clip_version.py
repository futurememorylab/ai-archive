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
    return await PendingOperationsRepo().insert_many(
        db,
        rows=[
            {
                "provider_id": "catdv",
                "provider_clip_id": "1",
                "op_kind": "SetField",
                "op_json": '{"kind":"SetField","identifier":"pragafilm.genre","value":"x"}',
                "origin_annotation_id": None,
                "origin_review_item_ids": None,
                "expected_etag": None,
                "origin_clip_version_id": version_id,
            }
        ],
    )


def _engine(db, status):
    return SyncEngine(
        provider=_Provider(status),
        pending_ops_repo=PendingOperationsRepo(),
        write_log_repo=_NoopLog(),
        connection_monitor=None,
        db_provider=lambda: db,
        review_items_repo=None,
        clip_versions_repo=ClipVersionsRepo(),
    )


def _v(num, state):
    return ClipVersion(
        catdv_clip_id=1,
        version_num=num,
        snapshot={"markers": [], "fields": {}, "notes": None},
        origin="publish",
        publish_state=state,
    )


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


class _RaisingProvider:
    id = "catdv"

    async def apply_changes(self, change_set):
        from backend.app.archive.errors import FatalProviderError

        raise FatalProviderError("CatDV HTTP 500: Data too long for column 'name'")


@pytest.mark.asyncio
async def test_merged_conflict_marks_every_version_not_just_freshest(db):
    """When several publishes for one clip merge into one PUT and it conflicts,
    EVERY merged version must flip to 'conflict' — not only the freshest. Marking
    just max(version_id) left the older sibling stuck on 'publishing' forever
    (it has no mark_live-style supersede fan-out). Finding #1."""
    repo = ClipVersionsRepo()
    v1 = await repo.insert(db, _v(1, "publishing"))
    v2 = await repo.insert(db, _v(2, "publishing"))
    await _enqueue_for_version(db, v1)
    await _enqueue_for_version(db, v2)
    await _engine(db, "conflict").drain_once()
    assert (await repo.get(db, v1)).publish_state == "conflict"
    assert (await repo.get(db, v2)).publish_state == "conflict"


@pytest.mark.asyncio
async def test_merged_fatal_marks_every_version_not_just_freshest(db):
    """Same stranding for a fatal result: both merged versions must flip to
    'failed', not just the freshest. Finding #1."""
    repo = ClipVersionsRepo()
    v1 = await repo.insert(db, _v(1, "publishing"))
    v2 = await repo.insert(db, _v(2, "publishing"))
    await _enqueue_for_version(db, v1)
    await _enqueue_for_version(db, v2)
    await _engine(db, "fatal").drain_once()
    assert (await repo.get(db, v1)).publish_state == "failed"
    assert (await repo.get(db, v2)).publish_state == "failed"


@pytest.mark.asyncio
async def test_merged_ok_makes_freshest_live_and_supersedes_older(db):
    """Non-regression: when two publishes merge and the PUT lands, the freshest
    version goes live and the older merged sibling is superseded (not left on
    'publishing', not co-live). mark_live already owns this fan-out (A4)."""
    repo = ClipVersionsRepo()
    v1 = await repo.insert(db, _v(1, "publishing"))
    v2 = await repo.insert(db, _v(2, "publishing"))
    await _enqueue_for_version(db, v1)
    await _enqueue_for_version(db, v2)
    await _engine(db, "ok").drain_once()
    assert (await repo.get(db, v2)).publish_state == "live"
    assert (await repo.get(db, v1)).publish_state == "superseded"


@pytest.mark.asyncio
async def test_raised_fatal_error_marks_version_failed(db):
    """When apply_changes RAISES a fatal error (e.g. a CatDV 500), the version
    must flip to 'failed' — otherwise it stays 'publishing' and the clips-list
    badge / headline read 'Publishing…' for a write that actually failed.
    Publishing audit, A9."""
    repo = ClipVersionsRepo()
    v = await repo.insert(db, _v(1, "publishing"))
    await _enqueue_for_version(db, v)
    engine = SyncEngine(
        provider=_RaisingProvider(),
        pending_ops_repo=PendingOperationsRepo(),
        write_log_repo=_NoopLog(),
        connection_monitor=None,
        db_provider=lambda: db,
        review_items_repo=None,
        clip_versions_repo=ClipVersionsRepo(),
    )
    await engine.drain_once()
    assert (await repo.get(db, v)).publish_state == "failed"
