# tests/integration/test_publish_service.py
import pytest

from backend.app.models.annotation import Annotation, ReviewItem
from backend.app.models.prompt import TargetMap
from backend.app.repositories.annotations import AnnotationsRepo
from backend.app.repositories.clip_versions import ClipVersionsRepo
from backend.app.repositories.pending_operations import PendingOperationsRepo
from backend.app.repositories.prompts import PromptsRepo
from backend.app.repositories.review_items import ReviewItemsRepo
from backend.app.services.publish_service import PublishService, build_provenance_value
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
    assert any("pragafilm.anno_version" in r["op_json"] for r in rows), "provenance op present"


@pytest.mark.asyncio
async def test_publish_noop_when_nothing_accepted(db):
    svc = _svc()
    assert await svc.publish(db, clip_id=999, author=None) is None


def test_provenance_value_shape():
    s = build_provenance_value(
        version_num=3, author="you", model="gemini-2.5-flash", ts="2026-06-17T10:44:00Z"
    )
    assert s.startswith("#3 · you · ")
    assert "gemini-2.5-flash" in s
