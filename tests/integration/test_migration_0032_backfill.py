"""0032 backfills review_edit_log from draft items a human already touched,
so decisions made before the log existed survive the next re-run."""

import json
from pathlib import Path

import pytest

from backend.app.db import open_db
from backend.app.migrations_runner import apply_migrations
from backend.app.models.annotation import ReviewItem
from backend.app.repositories.review_items import ReviewItemsRepo
from tests.integration.test_review_items_repo import _seed_annotation  # type: ignore

MIGRATIONS = Path(__file__).resolve().parents[2] / "backend" / "migrations"


@pytest.mark.asyncio
async def test_backfill_copies_touched_items_only(tmp_path):
    staged = tmp_path / "migrations"
    staged.mkdir()
    for p in sorted(MIGRATIONS.glob("*")):
        if p.name < "0032":
            (staged / p.name).write_text(p.read_text())

    async with open_db(tmp_path / "t.db") as db:
        await apply_migrations(db, staged)
        aid = await _seed_annotation(db)
        items = await ReviewItemsRepo().bulk_insert(
            db,
            [
                ReviewItem(annotation_id=aid, catdv_clip_id=1, kind="note", proposed_value=v)
                for v in ("untouched", "accepted", "edited")
            ],
        )
        # Pre-0032 state, written the way the old set_decision did.
        await db.execute(
            "UPDATE review_items SET decision='accepted', decided_at='2026-10-01T10:00:00' "
            "WHERE id=?",
            (items[1].id,),
        )
        await db.execute(
            "UPDATE review_items SET edited_value=? WHERE id=?",
            (json.dumps("fixed"), items[2].id),
        )
        await db.commit()

        await apply_migrations(db, MIGRATIONS)

        cur = await db.execute(
            "SELECT review_item_id, action, occurred_at, decision_after, edited_after, model "
            "FROM review_edit_log ORDER BY review_item_id"
        )
        rows = await cur.fetchall()
    assert [r[0] for r in rows] == [items[1].id, items[2].id]
    assert rows[0][1:4] == ("backfill", "2026-10-01T10:00:00", "accepted")
    assert rows[1][4] == json.dumps("fixed") and rows[1][5] == "m"
