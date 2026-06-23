import pytest

from backend.app.models.annotation import Annotation, ReviewItem
from backend.app.repositories.annotations import AnnotationsRepo
from backend.app.repositories.clip_versions import ClipVersionsRepo
from backend.app.repositories.prompts import PromptsRepo
from backend.app.repositories.review_items import ReviewItemsRepo
from backend.app.services.clip_versions_backfill import backfill_clip_versions
from tests._helpers.query_count import assert_query_count


async def _seed_synced_field_clip(db, version_id: int, clip_id: int) -> None:
    ar, ri = AnnotationsRepo(), ReviewItemsRepo()
    aid = await ar.insert(
        db,
        Annotation(
            catdv_clip_id=clip_id,
            catdv_clip_name=f"C{clip_id}",
            prompt_version_id=version_id,
            job_id=None,
            model="m",
            prompt_used="p",
            raw_response={},
            structured_output=None,
            clip_snapshot={},
        ),
    )
    [it] = await ri.bulk_insert(
        db,
        [
            ReviewItem(
                annotation_id=aid,
                studio_run_id=None,
                catdv_clip_id=clip_id,
                kind="field",
                target_identifier="pragafilm.genre",
                proposed_value="drama",
            )
        ],
    )
    await ri.mark_applied(db, [it.id])
    await ri.mark_synced(db, [it.id])


@pytest.mark.asyncio
async def test_backfill_creates_one_live_v1_for_synced_clip(db):
    # Seed a real prompt + version to satisfy the FK on annotations.prompt_version_id
    _, version_id = await PromptsRepo().create_with_initial_version(
        db,
        name="t",
        description=None,
        body="p",
        target_map={},
        output_schema={},
        model="m",
    )

    ar, ri = AnnotationsRepo(), ReviewItemsRepo()
    aid = await ar.insert(
        db,
        Annotation(
            catdv_clip_id=5,
            catdv_clip_name="C5",
            prompt_version_id=version_id,
            job_id=None,
            model="m",
            prompt_used="p",
            raw_response={},
            structured_output=None,
            clip_snapshot={},
        ),
    )
    [it] = await ri.bulk_insert(
        db,
        [
            ReviewItem(
                annotation_id=aid,
                studio_run_id=None,
                catdv_clip_id=5,
                kind="field",
                target_identifier="pragafilm.genre",
                proposed_value="drama",
            )
        ],
    )
    await ri.mark_applied(db, [it.id])
    await ri.mark_synced(db, [it.id])

    created = await backfill_clip_versions(db, ClipVersionsRepo())
    assert created == 1
    versions = await ClipVersionsRepo().list_by_clip(db, 5)
    assert len(versions) == 1
    assert versions[0].publish_state == "live"
    assert versions[0].author == "—"

    # idempotent
    assert await backfill_clip_versions(db, ClipVersionsRepo()) == 0


@pytest.mark.asyncio
async def test_backfill_is_not_n_plus_1(db):
    """Backfill must not issue a SELECT per clip: one grouped read + the N
    inserts in a single transaction, not 2N+1 round-trips. Review finding #9
    (boot-path N+1 — runs synchronously before the app serves)."""
    _, version_id = await PromptsRepo().create_with_initial_version(
        db, name="t", description=None, body="p", target_map={}, output_schema={}, model="m"
    )
    clip_ids = [101, 102, 103, 104]
    for cid in clip_ids:
        await _seed_synced_field_clip(db, version_id, cid)

    # One combined SELECT + one INSERT per clip = len+1; allow +1 slack. The old
    # per-clip SELECT loop would run 2*len+1 and blow this bound.
    async with assert_query_count(db, max_n=len(clip_ids) + 2):
        created = await backfill_clip_versions(db, ClipVersionsRepo())
    assert created == len(clip_ids)
    for cid in clip_ids:
        assert len(await ClipVersionsRepo().list_by_clip(db, cid)) == 1
