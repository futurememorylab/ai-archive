import pytest

from backend.app.archive.model import SetField
from backend.app.models.annotation import Annotation, ReviewItem
from backend.app.models.prompt import TargetMap
from backend.app.repositories.annotations import AnnotationsRepo
from backend.app.repositories.pending_operations import PendingOperationsRepo
from backend.app.repositories.prompts import PromptsRepo
from backend.app.repositories.review_items import ReviewItemsRepo
from backend.app.services.write_queue import WriteQueue


async def _seed_annotation(conn) -> int:
    prompts = PromptsRepo()
    annotations = AnnotationsRepo()
    _, vid = await prompts.create_with_initial_version(
        conn,
        name="t",
        description=None,
        body="p",
        target_map={"genre": {"kind": "field", "identifier": "pragafilm.genre"}},
        output_schema={},
        model="m",
    )
    return await annotations.insert(
        conn,
        Annotation(
            catdv_clip_id=1,
            catdv_clip_name="Clip_1",
            prompt_version_id=vid,
            model="m",
            prompt_used="p",
            raw_response={},
            structured_output={},
            clip_snapshot={"ID": 1, "name": "Clip_1", "markers": [], "fields": {}},
        ),
    )


@pytest.mark.asyncio
async def test_enqueue_carries_clip_version_id_and_extra_ops(db):
    aid = await _seed_annotation(db)
    ri = ReviewItemsRepo()
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
    item = await ri.get(db, item.id)

    wq = WriteQueue(pending_ops_repo=PendingOperationsRepo(), review_items_repo=ri)
    op_ids = await wq.enqueue_apply_for_clip(
        db,
        clip_id=1,
        accepted=[item],
        target_map=TargetMap({}),
        expected_etag=None,
        annotation_id=None,
        fps=25.0,
        clip_version_id=42,
        extra_ops=[SetField(identifier="pragafilm.anno_version", value="#1 · you")],
    )
    assert len(op_ids) == 2  # the field op + the provenance op
    rows = await PendingOperationsRepo().list_pending_for_clip(
        db, provider_id="catdv", provider_clip_id="1"
    )
    assert all(r["origin_clip_version_id"] == 42 for r in rows)
    assert any(r["op_kind"] == "SetField" and "anno_version" in r["op_json"] for r in rows)
