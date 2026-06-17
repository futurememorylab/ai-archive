# tests/integration/test_publish_service.py
import pytest

from backend.app.models.annotation import Annotation, ReviewItem
from backend.app.models.prompt import TargetMap
from backend.app.repositories.annotations import AnnotationsRepo
from backend.app.repositories.clip_versions import ClipVersionsRepo
from backend.app.repositories.pending_operations import PendingOperationsRepo
from backend.app.repositories.prompts import PromptsRepo
from backend.app.repositories.review_items import ReviewItemsRepo
from backend.app.services.publish_service import PublishService
from backend.app.services.write_queue import WriteQueue


class _StubVersion:
    target_map = TargetMap({})
    prompt_version_id = 0


class _StubPrompts:
    async def get_version(self, conn, vid):
        return _StubVersion()


async def _stub_loader(conn, clip_id):
    return {
        "markers": [],
        "fields": {},
        "notes": None,
        "bigNotes": None,
        "fps": 25.0,
        "modifyDate": None,
    }


def _svc():
    return PublishService(
        annotations_repo=AnnotationsRepo(),
        review_items_repo=ReviewItemsRepo(),
        clip_versions_repo=ClipVersionsRepo(),
        write_queue=WriteQueue(
            pending_ops_repo=PendingOperationsRepo(), review_items_repo=ReviewItemsRepo()
        ),
        prompts_repo=_StubPrompts(),
        live_snapshot_loader=_stub_loader,
    )


async def _seed_prompt_version(db) -> int:
    _, vid = await PromptsRepo().create_with_initial_version(
        db,
        name="t",
        description=None,
        body="p",
        target_map={},
        output_schema={},
        model="gemini-2.5-flash",
    )
    return vid


async def _seed_accepted_field(db):
    vid = await _seed_prompt_version(db)
    ar, ri = AnnotationsRepo(), ReviewItemsRepo()
    aid = await ar.insert(
        db,
        Annotation(
            catdv_clip_id=1,
            catdv_clip_name="Clip_1",
            prompt_version_id=vid,
            job_id=None,
            model="gemini-2.5-flash",
            prompt_used="p",
            raw_response={},
            structured_output=None,
            clip_snapshot={"modifyDate": "2026-06-17T00:00:00Z", "fps": 25.0},
        ),
    )
    [item] = await ri.bulk_insert(
        db,
        [
            ReviewItem(
                annotation_id=aid,
                studio_run_id=None,
                catdv_clip_id=1,
                kind="field",
                target_identifier="pragafilm.genre",
                proposed_value="thriller",
            )
        ],
    )
    await ri.set_decision(db, item.id, "accepted")
    return aid


@pytest.mark.asyncio
async def test_publish_creates_publishing_version_and_enqueues(db):
    await _seed_accepted_field(db)
    svc = _svc()
    version_id = await svc.publish(db, clip_id=1, author="anna@example.com")
    assert version_id is not None

    cv = await ClipVersionsRepo().get(db, version_id)
    assert cv.publish_state == "publishing"
    assert cv.version_num == 1
    assert cv.author == "anna@example.com"
    assert cv.snapshot["fields"]["pragafilm.genre"] == "thriller"

    rows = await PendingOperationsRepo().list_pending_for_clip(
        db, provider_id="catdv", provider_clip_id="1"
    )
    assert rows, "ops enqueued"
    assert all(r["origin_clip_version_id"] == version_id for r in rows)
    # The accepted field change is the only op — no pragafilm.anno_version
    # provenance write (it 500'd CatDV; dropped per the publishing audit).
    assert any("pragafilm.genre" in r["op_json"] for r in rows), "field op enqueued"
    assert not any("anno_version" in r["op_json"] for r in rows), "no provenance op"


@pytest.mark.asyncio
async def test_reactivate_enqueues_snapshot_and_creates_no_new_version(db):
    """reactivate() re-PUTs a version's snapshot and marks it publishing,
    WITHOUT inserting a new clip_versions row. Publishing audit, A3."""
    from backend.app.models.annotation import ClipVersion

    repo = ClipVersionsRepo()
    v1 = await repo.insert(
        db,
        ClipVersion(
            catdv_clip_id=1,
            version_num=1,
            snapshot={"markers": [], "fields": {"pragafilm.genre": "drama"}, "notes": None},
            origin="publish",
            publish_state="superseded",
        ),
    )
    await repo.insert(
        db,
        ClipVersion(
            catdv_clip_id=1,
            version_num=2,
            snapshot={"markers": [], "fields": {"pragafilm.genre": "thriller"}, "notes": None},
            origin="publish",
            publish_state="live",
        ),
    )

    rid = await _svc().reactivate(db, clip_id=1, version_num=1)
    assert rid == v1
    assert len(await repo.list_by_clip(db, 1)) == 2  # no new version
    assert (await repo.get(db, v1)).publish_state == "publishing"

    rows = await PendingOperationsRepo().list_pending_for_clip(
        db, provider_id="catdv", provider_clip_id="1"
    )
    assert any(
        r["origin_clip_version_id"] == v1 and "pragafilm.genre" in r["op_json"] for r in rows
    ), "v1's snapshot re-enqueued under its own version id"


@pytest.mark.asyncio
async def test_reactivate_reconciles_markers_drops_later_versions(db):
    """Switching to v1 emits a ReconcileMarkers asserting v1's markers and
    dropping the markers only later versions added — no new version row."""
    from backend.app.archive.change_set_json import change_op_from_json
    from backend.app.archive.model import ReconcileMarkers
    from backend.app.models.annotation import ClipVersion

    repo = ClipVersionsRepo()
    v1 = await repo.insert(
        db,
        ClipVersion(
            catdv_clip_id=1, version_num=1,
            snapshot={"markers": [{"name": "A", "in": {"secs": 4.0}}], "fields": {}, "notes": None},
            origin="publish", publish_state="superseded",
        ),
    )
    await repo.insert(
        db,
        ClipVersion(
            catdv_clip_id=1, version_num=2,
            snapshot={
                "markers": [{"name": "A", "in": {"secs": 4.0}}, {"name": "B", "in": {"secs": 8.0}}],
                "fields": {}, "notes": None,
            },
            origin="publish", publish_state="live",
        ),
    )

    rid = await _svc().reactivate(db, clip_id=1, version_num=1)
    assert rid == v1
    assert len(await repo.list_by_clip(db, 1)) == 2  # no new version

    rows = await PendingOperationsRepo().list_pending_for_clip(
        db, provider_id="catdv", provider_clip_id="1"
    )
    recon = [
        o
        for o in (change_op_from_json(r["op_json"]) for r in rows)
        if isinstance(o, ReconcileMarkers)
    ]
    assert len(recon) == 1
    assert {m.name for m in recon[0].desired} == {"A"}
    assert set(recon[0].drop_secs) == {8.0}  # B (added in v2) is dropped


@pytest.mark.asyncio
async def test_publish_noop_when_nothing_accepted(db):
    svc = _svc()
    assert await svc.publish(db, clip_id=999, author=None) is None
