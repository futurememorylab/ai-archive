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
                target_identifier="pragafilm.popis.ai",
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
    assert cv.snapshot["fields"]["pragafilm.popis.ai"] == "thriller"

    rows = await PendingOperationsRepo().list_pending_for_clip(
        db, provider_id="catdv", provider_clip_id="1"
    )
    assert rows, "ops enqueued"
    assert all(r["origin_clip_version_id"] == version_id for r in rows)
    # The accepted field change is the only op — no pragafilm.anno_version
    # provenance write (it 500'd CatDV; dropped per the publishing audit).
    assert any("pragafilm.popis.ai" in r["op_json"] for r in rows), "owned field op enqueued"
    assert not any("anno_version" in r["op_json"] for r in rows), "no provenance op"


@pytest.mark.asyncio
async def test_publish_noop_when_nothing_accepted(db):
    svc = _svc()
    assert await svc.publish(db, clip_id=999, author=None) is None


# --- #123 review fixes: the AI marker set is ONE rule for CatDV + snapshot ---


async def _seed_annotation(db, vid) -> int:
    return await AnnotationsRepo().insert(
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


async def _accept_marker(db, aid, name, secs):
    [item] = await ReviewItemsRepo().bulk_insert(
        db,
        [
            ReviewItem(
                annotation_id=aid,
                studio_run_id=None,
                catdv_clip_id=1,
                kind="marker",
                target_identifier=None,
                proposed_value={"name": name, "in": {"secs": secs}},
            )
        ],
    )
    await ReviewItemsRepo().set_decision(db, item.id, "accepted")


async def _last_replace_ai_markers(db):
    from backend.app.archive.change_set_json import change_op_from_json
    from backend.app.archive.model import ReplaceAIMarkers

    rows = await PendingOperationsRepo().list_pending_for_clip(
        db, provider_id="catdv", provider_clip_id="1"
    )
    ops = [change_op_from_json(r["op_json"]) for r in rows]
    return [o for o in ops if isinstance(o, ReplaceAIMarkers)][-1]


@pytest.mark.asyncio
async def test_rerun_publish_snapshot_holds_only_the_new_ai_set(db):
    """Fix 1: a re-run (new annotation) replaces the AI set in CatDV, so the
    version snapshot must hold ONLY the new markers — never parent + new, or
    Make live on it would resurrect the old AI markers."""
    vid = await _seed_prompt_version(db)
    svc = _svc()
    a1 = await _seed_annotation(db, vid)
    await _accept_marker(db, a1, "old", 1.0)
    v1 = await svc.publish(db, clip_id=1, author=None)
    await ClipVersionsRepo().mark_live(db, v1)

    a2 = await _seed_annotation(db, vid)
    await _accept_marker(db, a2, "new", 2.0)
    v2 = await svc.publish(db, clip_id=1, author=None)

    snap = (await ClipVersionsRepo().get(db, v2)).snapshot
    assert [m["name"] for m in snap["markers"]] == ["new"]
    op = await _last_replace_ai_markers(db)
    assert [m.name for m in op.desired] == ["new"]


@pytest.mark.asyncio
async def test_partial_second_apply_keeps_markers_from_first_apply(db):
    """Fix 2: applying a draft in two steps must not drop the markers applied
    in the first step — the AI set is every accepted marker of the annotation."""
    vid = await _seed_prompt_version(db)
    svc = _svc()
    aid = await _seed_annotation(db, vid)
    await _accept_marker(db, aid, "a", 1.0)
    v1 = await svc.publish(db, clip_id=1, author=None)
    await ClipVersionsRepo().mark_live(db, v1)

    await _accept_marker(db, aid, "b", 2.0)
    v2 = await svc.publish(db, clip_id=1, author=None)

    op = await _last_replace_ai_markers(db)
    assert sorted(m.name for m in op.desired) == ["a", "b"]
    snap = (await ClipVersionsRepo().get(db, v2)).snapshot
    assert sorted(m["name"] for m in snap["markers"]) == ["a", "b"]


@pytest.mark.asyncio
async def test_fields_only_publish_keeps_parent_markers_in_snapshot(db):
    """A draft with no marker items emits no marker op, so CatDV keeps its AI
    markers — and the snapshot must keep the parent's markers to match."""
    vid = await _seed_prompt_version(db)
    svc = _svc()
    aid = await _seed_annotation(db, vid)
    await _accept_marker(db, aid, "a", 1.0)
    v1 = await svc.publish(db, clip_id=1, author=None)
    await ClipVersionsRepo().mark_live(db, v1)

    a2 = await _seed_annotation(db, vid)
    [item] = await ReviewItemsRepo().bulk_insert(
        db,
        [
            ReviewItem(
                annotation_id=a2,
                studio_run_id=None,
                catdv_clip_id=1,
                kind="field",
                target_identifier="pragafilm.popis.ai",
                proposed_value="summary",
            )
        ],
    )
    await ReviewItemsRepo().set_decision(db, item.id, "accepted")
    v2 = await svc.publish(db, clip_id=1, author=None)

    snap = (await ClipVersionsRepo().get(db, v2)).snapshot
    assert [m["name"] for m in snap["markers"]] == ["a"]
