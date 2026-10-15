"""Guard: every "awaiting review" surface agrees on which clips are awaiting.

The Batches page status, the topbar "N to review" count, the
/?anno=for_review clip filter and the batch review walk all answer the same
question. When one of them grew its own copy of the predicate, it drifted:
after the #123 writeback redesign left suggestions (field/note items targeting
a field the app does not own) un-applied by design, the Batches page kept
applied jobs in "Awaiting review" while the topbar said nothing was left.

The rule: build the predicate from ``review_items.for_review_where`` (or
``FOR_REVIEW_WHERE``); never re-spell it. This test seeds one clip per edge
case and asserts all four surfaces return the same set.
"""

from pathlib import Path

import pytest

from backend.app.archive.model import AI_OWNED_FIELDS
from backend.app.repositories.jobs import JobsRepo
from backend.app.repositories.prompts import PromptsRepo
from backend.app.repositories.review_items import ReviewItemsRepo
from backend.app.services.clip_list_filters import _ids_with_annotation_review_state

APPLIED = "2026-06-02T01:00:00"


async def _annotation(db, *, job_id, clip_id, vid) -> int:
    cur = await db.execute(
        "INSERT INTO annotations "
        "(catdv_clip_id, catdv_clip_name, prompt_version_id, job_id, model, "
        " prompt_used, raw_response, structured_output, clip_snapshot, created_at) "
        "VALUES (?, ?, ?, ?, 'm', 'p', '{}', '{}', '{}', '2026-06-02T00:00:00')",
        (clip_id, f"Clip_{clip_id}", vid, job_id),
    )
    return cur.lastrowid


async def _item(db, ann, clip_id, *, kind="marker", target=None, decision="pending", applied=None):
    await db.execute(
        "INSERT INTO review_items "
        "(annotation_id, studio_run_id, catdv_clip_id, kind, target_identifier, "
        " proposed_value, edited_value, decision, applied_at) "
        "VALUES (?, NULL, ?, ?, ?, '{}', NULL, ?, ?)",
        (ann, clip_id, kind, target, decision, applied),
    )


@pytest.mark.asyncio
async def test_all_awaiting_review_surfaces_agree(db):
    _, vid = await PromptsRepo().create_with_initial_version(
        db,
        name="p",
        description=None,
        body="p",
        target_map={},
        output_schema={},
        model="m",
    )
    jobs = JobsRepo()
    old = await jobs.create_job(db, prompt_version_id=vid, clip_ids=[1, 2, 3, 4, 5, 6])
    new = await jobs.create_job(db, prompt_version_id=vid, clip_ids=[4])
    owned = AI_OWNED_FIELDS[0]

    # 1: undecided marker → awaiting
    a = await _annotation(db, job_id=old, clip_id=1, vid=vid)
    await _item(db, a, 1)
    # 2: applied marker + accepted and pending suggestions → done
    a = await _annotation(db, job_id=old, clip_id=2, vid=vid)
    await _item(db, a, 2, decision="accepted", applied=APPLIED)
    await _item(db, a, 2, kind="field", target="decade", decision="accepted")
    await _item(db, a, 2, kind="note", target="notes")
    # 3: everything rejected → done
    a = await _annotation(db, job_id=old, clip_id=3, vid=vid)
    await _item(db, a, 3, decision="rejected")
    # 4: superseded draft left undecided; the newer draft is applied → done
    a = await _annotation(db, job_id=old, clip_id=4, vid=vid)
    await _item(db, a, 4)
    a = await _annotation(db, job_id=new, clip_id=4, vid=vid)
    await _item(db, a, 4, decision="accepted", applied=APPLIED)
    # 5: undecided write to an owned field → awaiting
    a = await _annotation(db, job_id=old, clip_id=5, vid=vid)
    await _item(db, a, 5, kind="field", target=owned)
    # 6: only an undecided suggestion → done
    a = await _annotation(db, job_id=old, clip_id=6, vid=vid)
    await _item(db, a, 6, kind="field", target="year")
    await db.commit()

    expected = {1, 5}
    batches = await jobs.list_batches(db, limit=50)
    review = ReviewItemsRepo()

    assert sum(r["awaiting_clips"] for r in batches) == len(expected)
    assert await review.count_clips_for_review(db) == len(expected)
    assert await _ids_with_annotation_review_state(db, applied=False) == expected
    assert set(await review.pending_clip_ids_for_jobs(db, [old, new])) == expected


def test_awaiting_predicate_is_not_respelled():
    """A second hand-written copy of the predicate is how the Batches page
    drifted. Only review_items.py may spell it out."""
    root = Path(__file__).resolve().parents[2] / "backend" / "app"
    offenders = [
        str(p.relative_to(root))
        for p in root.rglob("*.py")
        if "migrations" not in p.parts
        and p.name != "review_items.py"
        and "decision != 'rejected'" in p.read_text(encoding="utf-8")
    ]
    assert offenders == [], f"use review_items.for_review_where instead: {offenders}"
