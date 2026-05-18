import pytest

from backend.app.models.annotation import Annotation, ReviewItem
from backend.app.models.template import Template
from backend.app.repositories.annotations import AnnotationsRepo
from backend.app.repositories.review_items import ReviewItemsRepo
from backend.app.repositories.templates import TemplatesRepo


async def _seed_annotation(db):
    templates = TemplatesRepo()
    template_id = await templates.create(
        db,
        Template(
            name="t", prompt="p", output_schema={}, target_map={"x": {"kind": "markers"}}, model="m"
        ),
    )
    annotations = AnnotationsRepo()
    return await annotations.insert(
        db,
        Annotation(
            catdv_clip_id=1,
            catdv_clip_name="c",
            template_id=template_id,
            model="m",
            prompt_used="p",
            raw_response={},
            structured_output={},
            clip_snapshot={},
        ),
    )


@pytest.mark.asyncio
async def test_bulk_insert_and_list(db):
    annotation_id = await _seed_annotation(db)
    repo = ReviewItemsRepo()
    items = [
        ReviewItem(
            annotation_id=annotation_id,
            catdv_clip_id=1,
            kind="marker",
            proposed_value={"in": 0, "out": 5, "name": "scene-a"},
        ),
        ReviewItem(
            annotation_id=annotation_id,
            catdv_clip_id=1,
            kind="field",
            target_identifier="pragafilm.dekáda.natočení",
            proposed_value="30.léta",
        ),
    ]
    inserted = await repo.bulk_insert(db, items)
    assert len(inserted) == 2

    loaded = await repo.list_by_clip(db, 1, decision="pending")
    assert [it.kind for it in loaded] == ["marker", "field"]


@pytest.mark.asyncio
async def test_set_decision_and_edited_value(db):
    annotation_id = await _seed_annotation(db)
    repo = ReviewItemsRepo()
    inserted = await repo.bulk_insert(
        db,
        [
            ReviewItem(
                annotation_id=annotation_id,
                catdv_clip_id=1,
                kind="marker",
                proposed_value={"in": 0, "out": 5, "name": "scene-a"},
            ),
        ],
    )
    item_id = inserted[0].id
    assert item_id is not None
    await repo.set_decision(
        db, item_id, "accepted", edited_value={"in": 1, "out": 5, "name": "scene-a"}
    )
    refreshed = await repo.get(db, item_id)
    assert refreshed.decision == "accepted"
    assert refreshed.edited_value == {"in": 1, "out": 5, "name": "scene-a"}
