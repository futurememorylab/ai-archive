import pytest

from backend.app.models.annotation import Annotation, ReviewItem
from backend.app.repositories.annotations import AnnotationsRepo
from backend.app.repositories.jobs import JobsRepo
from backend.app.repositories.prompts import PromptsRepo
from backend.app.repositories.review_items import ReviewItemsRepo


async def _get_or_create_prompt_version(db) -> int:
    prompts = PromptsRepo()
    _, vid = await prompts.create_with_initial_version(
        db,
        name="t",
        description=None,
        body="p",
        target_map={"x": {"kind": "markers"}},
        output_schema={},
        model="m",
    )
    return vid


async def _seed_annotation(db):
    vid = await _get_or_create_prompt_version(db)
    annotations = AnnotationsRepo()
    return await annotations.insert(
        db,
        Annotation(
            catdv_clip_id=1,
            catdv_clip_name="c",
            prompt_version_id=vid,
            model="m",
            prompt_used="p",
            raw_response={},
            structured_output={},
            clip_snapshot={},
        ),
    )


async def _make_job(db, vid: int) -> int:
    """Create a real job row (needed because annotations.job_id is a FK)."""
    jobs = JobsRepo()
    return await jobs.create_job(db, prompt_version_id=vid, clip_ids=[1])


async def _seed_annotation_for(
    db,
    *,
    catdv_clip_id: int,
    catdv_clip_name: str,
    job_id: int | None,
    vid: int,
) -> int:
    """Seed an annotation for an arbitrary clip, reusing an existing prompt version."""
    annotations = AnnotationsRepo()
    return await annotations.insert(
        db,
        Annotation(
            catdv_clip_id=catdv_clip_id,
            catdv_clip_name=catdv_clip_name,
            prompt_version_id=vid,
            job_id=job_id,
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


@pytest.mark.asyncio
async def test_list_pending_clips_groups_and_counts(db):
    vid = await _get_or_create_prompt_version(db)
    job_id = await _make_job(db, vid)
    annotation_id = await _seed_annotation_for(
        db, catdv_clip_id=42, catdv_clip_name="Clip_42", job_id=job_id, vid=vid
    )
    repo = ReviewItemsRepo()
    await repo.bulk_insert(
        db,
        [
            ReviewItem(
                annotation_id=annotation_id,
                catdv_clip_id=42,
                kind="marker",
                proposed_value={"in": 0, "out": 5, "name": "m1"},
            ),
            ReviewItem(
                annotation_id=annotation_id,
                catdv_clip_id=42,
                kind="marker",
                proposed_value={"in": 10, "out": 20, "name": "m2"},
            ),
            ReviewItem(
                annotation_id=annotation_id,
                catdv_clip_id=42,
                kind="field",
                target_identifier="genre",
                proposed_value="drama",
            ),
        ],
    )
    rows = await repo.list_pending_clips(db, limit=50, offset=0)
    assert len(rows) == 1
    row = rows[0]
    assert row["catdv_clip_id"] == 42
    assert row["catdv_clip_name"] == "Clip_42"
    assert row["job_id"] == job_id
    assert row["marker_count"] == 2
    assert row["field_count"] == 1
    assert row["note_count"] == 0


@pytest.mark.asyncio
async def test_list_pending_clips_excludes_applied(db):
    vid = await _get_or_create_prompt_version(db)
    job_id = await _make_job(db, vid)
    annotation_id = await _seed_annotation_for(
        db, catdv_clip_id=42, catdv_clip_name="Clip_42", job_id=job_id, vid=vid
    )
    repo = ReviewItemsRepo()
    inserted = await repo.bulk_insert(
        db,
        [
            ReviewItem(
                annotation_id=annotation_id,
                catdv_clip_id=42,
                kind="field",
                target_identifier="genre",
                proposed_value="drama",
            ),
        ],
    )
    item_id = inserted[0].id
    await repo.mark_applied(db, [item_id])
    rows = await repo.list_pending_clips(db, limit=50, offset=0)
    assert rows == []


@pytest.mark.asyncio
async def test_list_pending_clips_job_filter(db):
    vid = await _get_or_create_prompt_version(db)
    job_id_a = await _make_job(db, vid)
    job_id_b = await _make_job(db, vid)
    ann1 = await _seed_annotation_for(
        db, catdv_clip_id=1, catdv_clip_name="Clip_1", job_id=job_id_a, vid=vid
    )
    ann2 = await _seed_annotation_for(
        db, catdv_clip_id=2, catdv_clip_name="Clip_2", job_id=job_id_b, vid=vid
    )
    repo = ReviewItemsRepo()
    await repo.bulk_insert(
        db,
        [
            ReviewItem(
                annotation_id=ann1,
                catdv_clip_id=1,
                kind="field",
                proposed_value="val1",
            ),
            ReviewItem(
                annotation_id=ann2,
                catdv_clip_id=2,
                kind="field",
                proposed_value="val2",
            ),
        ],
    )
    rows = await repo.list_pending_clips(db, job_id=job_id_a, limit=50, offset=0)
    assert len(rows) == 1
    assert rows[0]["catdv_clip_id"] == 1


@pytest.mark.asyncio
async def test_count_pending_clips(db):
    vid = await _get_or_create_prompt_version(db)
    job_id = await _make_job(db, vid)
    annotation_id = await _seed_annotation_for(
        db, catdv_clip_id=42, catdv_clip_name="Clip_42", job_id=job_id, vid=vid
    )
    repo = ReviewItemsRepo()
    await repo.bulk_insert(
        db,
        [
            ReviewItem(
                annotation_id=annotation_id,
                catdv_clip_id=42,
                kind="field",
                proposed_value="val",
            ),
        ],
    )
    count = await repo.count_pending_clips(db)
    assert count == 1
