"""review_edit_log: every human decision / edit on a draft item is appended
to a durable log with its prompt + model context, and survives the draft rows
being deleted by a re-run (spec 2026-10-17-human-edit-and-cost-logging)."""

import json

import pytest

from backend.app.models.annotation import ReviewItem
from backend.app.repositories.review_items import ReviewItemsRepo
from backend.app.repositories.studio_runs import StudioRunsRepo
from tests.integration.test_review_items_repo import (  # type: ignore
    _get_or_create_prompt_version,
    _seed_annotation,
)

_COLS = (
    "author, action, review_item_id, catdv_clip_id, annotation_id, studio_run_id, "
    "prompt_version_id, model, kind, target_identifier, proposed_value, "
    "decision_before, decision_after, edited_before, edited_after"
)


async def _log(db) -> list[dict]:
    cur = await db.execute(f"SELECT {_COLS} FROM review_edit_log ORDER BY id")
    names = [c.strip() for c in _COLS.split(",")]
    return [dict(zip(names, r, strict=True)) for r in await cur.fetchall()]


async def _seed_field(db) -> tuple[int, int]:
    aid = await _seed_annotation(db)
    [item] = await ReviewItemsRepo().bulk_insert(
        db,
        [
            ReviewItem(
                annotation_id=aid,
                catdv_clip_id=1,
                kind="field",
                target_identifier="pragafilm.popis.ai",
                proposed_value="Muž v klobouku",
            )
        ],
    )
    return aid, item.id


@pytest.mark.asyncio
async def test_edit_and_accept_are_logged_with_context(db):
    aid, item_id = await _seed_field(db)
    repo = ReviewItemsRepo()

    await repo.set_decision(
        db, item_id, "accepted", edited_value="Muž v buřince", author="ed@example.org"
    )

    [row] = await _log(db)
    assert row["author"] == "ed@example.org"
    assert row["action"] == "decision"
    assert (row["review_item_id"], row["catdv_clip_id"], row["annotation_id"]) == (item_id, 1, aid)
    assert row["prompt_version_id"] is not None and row["model"] == "m"
    assert (row["kind"], row["target_identifier"]) == ("field", "pragafilm.popis.ai")
    assert json.loads(row["proposed_value"]) == "Muž v klobouku"
    assert (row["decision_before"], row["decision_after"]) == ("pending", "accepted")
    assert row["edited_before"] is None
    assert json.loads(row["edited_after"]) == "Muž v buřince"


@pytest.mark.asyncio
async def test_unchanged_decision_is_not_logged_twice(db):
    _, item_id = await _seed_field(db)
    repo = ReviewItemsRepo()
    await repo.set_decision(db, item_id, "accepted")
    await repo.set_decision(db, item_id, "accepted")  # e.g. Accept all re-sending
    assert len(await _log(db)) == 1


@pytest.mark.asyncio
async def test_each_step_of_a_correction_is_kept(db):
    _, item_id = await _seed_field(db)
    repo = ReviewItemsRepo()
    await repo.set_decision(db, item_id, "pending", edited_value="draft 1")
    await repo.set_decision(db, item_id, "pending", edited_value="draft 2")
    await repo.set_decision(db, item_id, "rejected")

    rows = await _log(db)
    assert [(r["decision_after"], r["edited_after"]) for r in rows] == [
        ("pending", json.dumps("draft 1")),
        ("pending", json.dumps("draft 2")),
        ("rejected", json.dumps("draft 2")),
    ]
    assert rows[1]["edited_before"] == json.dumps("draft 1")


@pytest.mark.asyncio
async def test_log_survives_rerun_clearing_the_draft(db):
    _, item_id = await _seed_field(db)
    repo = ReviewItemsRepo()
    await repo.set_decision(db, item_id, "rejected", author="ed@example.org")

    # A re-run (or restore) replaces the un-applied draft.
    assert await repo.clear_unapplied_for_clip(db, 1) == 1

    [row] = await _log(db)
    assert row["review_item_id"] == item_id and row["decision_after"] == "rejected"


@pytest.mark.asyncio
async def test_studio_item_takes_context_from_studio_run(db):
    vid = await _get_or_create_prompt_version(db)
    run_id = await StudioRunsRepo().create_pending(
        db, prompt_version_id=vid, clip_id=7, model="gemini-x"
    )
    [item] = await ReviewItemsRepo().bulk_insert(
        db,
        [
            ReviewItem(
                studio_run_id=run_id,
                catdv_clip_id=7,
                kind="note",
                target_identifier="notes",
                proposed_value="poznámka",
            )
        ],
    )
    await ReviewItemsRepo().set_decision(db, item.id, "accepted", action="bulk_accept")

    [row] = await _log(db)
    assert (row["studio_run_id"], row["annotation_id"]) == (run_id, None)
    assert (row["prompt_version_id"], row["model"]) == (vid, "gemini-x")
    assert row["action"] == "bulk_accept"
