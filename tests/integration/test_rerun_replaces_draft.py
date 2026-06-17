"""Integration test: clear_unapplied_for_clip preserves applied items and
drops unapplied ones — the behaviour the annotator relies on to replace the
working draft on re-run without losing published history."""

import pytest

from backend.app.repositories.review_items import ReviewItemsRepo


async def _seed_annotation(db) -> tuple[int, int]:
    """Insert the minimal prompt/annotation rows needed to satisfy the FK
    constraint on review_items.annotation_id.  Returns (prompt_version_id,
    annotation_id)."""
    cur = await db.execute(
        "INSERT INTO prompts (name, description, archived, created_at, updated_at) "
        "VALUES ('test-prompt', NULL, 0, '2026-01-01T00:00:00', '2026-01-01T00:00:00')"
    )
    prompt_id = cur.lastrowid
    cur = await db.execute(
        "INSERT INTO prompt_versions "
        "  (prompt_id, version_num, state, body, target_map, output_schema, model, "
        "   created_at, updated_at) "
        "VALUES (?, 1, 'production', 'p', '{}', '{}', 'gemini', "
        "        '2026-01-01T00:00:00', '2026-01-01T00:00:00')",
        (prompt_id,),
    )
    pv_id = cur.lastrowid
    cur = await db.execute(
        "INSERT INTO annotations "
        "  (catdv_clip_id, catdv_clip_name, prompt_version_id, job_id, model, "
        "   prompt_used, raw_response, structured_output, clip_snapshot, created_at) "
        "VALUES (1, 'clip-1', ?, NULL, 'gemini', 'p', '{}', '{}', '{}', "
        "        '2026-01-01T00:00:00')",
        (pv_id,),
    )
    annotation_id = cur.lastrowid
    await db.commit()
    return pv_id, annotation_id


@pytest.mark.asyncio
async def test_clear_unapplied_for_clip_only_drops_unapplied(db):
    """Applied items survive; un-applied items are deleted; return value equals
    the count of deleted rows."""
    _, annotation_id = await _seed_annotation(db)
    ri = ReviewItemsRepo()

    # Insert two items for clip 1 — both start un-applied (applied_at IS NULL)
    a, b = await ri.bulk_insert(
        db,
        [
            # item that will remain un-applied (the "old draft")
            _make_item(annotation_id, clip_id=1, value="old"),
            # item that will be marked applied (simulates a published result)
            _make_item(annotation_id, clip_id=1, value="kept"),
        ],
    )

    # Simulate the "apply" step — b is now part of the published history
    await ri.mark_applied(db, [b.id])

    # clear_unapplied_for_clip should delete only the un-applied item (a)
    dropped = await ri.clear_unapplied_for_clip(db, 1)

    assert dropped == 1, f"expected 1 row deleted, got {dropped}"

    remaining = [it.id for it in await ri.list_by_clip(db, 1)]
    assert remaining == [b.id], (
        f"only the applied item should survive; got ids {remaining}"
    )


def _make_item(annotation_id: int, *, clip_id: int, value: str):
    """Return a ReviewItem suitable for bulk_insert."""
    from backend.app.models.annotation import ReviewItem

    return ReviewItem(
        annotation_id=annotation_id,
        studio_run_id=None,
        catdv_clip_id=clip_id,
        kind="note",
        target_identifier="notes",
        proposed_value=value,
    )
