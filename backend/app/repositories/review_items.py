import json
from datetime import datetime, timezone
from typing import Literal

import aiosqlite

from backend.app.models.annotation import ReviewItem


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ReviewItemsRepo:
    async def bulk_insert(self, conn: aiosqlite.Connection,
                           items: list[ReviewItem]) -> list[ReviewItem]:
        inserted: list[ReviewItem] = []
        for it in items:
            cur = await conn.execute(
                """
                INSERT INTO review_items
                  (annotation_id, catdv_clip_id, kind, target_identifier,
                   proposed_value, edited_value, decision)
                VALUES (?, ?, ?, ?, ?, NULL, 'pending')
                """,
                (
                    it.annotation_id, it.catdv_clip_id, it.kind, it.target_identifier,
                    json.dumps(it.proposed_value, ensure_ascii=False),
                ),
            )
            it.id = cur.lastrowid
            it.decision = "pending"
            inserted.append(it)
        await conn.commit()
        return inserted

    async def get(self, conn: aiosqlite.Connection, item_id: int) -> ReviewItem:
        cur = await conn.execute(
            """
            SELECT id, annotation_id, catdv_clip_id, kind, target_identifier,
                   proposed_value, edited_value, decision
            FROM review_items WHERE id = ?
            """,
            (item_id,),
        )
        row = await cur.fetchone()
        if row is None:
            raise LookupError(f"review_item {item_id} not found")
        return self._row(row)

    async def list_by_clip(self, conn: aiosqlite.Connection, clip_id: int,
                            *, decision: str | None = None) -> list[ReviewItem]:
        if decision is not None:
            cur = await conn.execute(
                """
                SELECT id, annotation_id, catdv_clip_id, kind, target_identifier,
                       proposed_value, edited_value, decision
                FROM review_items WHERE catdv_clip_id = ? AND decision = ?
                ORDER BY id
                """,
                (clip_id, decision),
            )
        else:
            cur = await conn.execute(
                """
                SELECT id, annotation_id, catdv_clip_id, kind, target_identifier,
                       proposed_value, edited_value, decision
                FROM review_items WHERE catdv_clip_id = ?
                ORDER BY id
                """,
                (clip_id,),
            )
        return [self._row(r) for r in await cur.fetchall()]

    async def set_decision(self, conn: aiosqlite.Connection, item_id: int,
                            decision: Literal["pending", "accepted", "rejected"],
                            *, edited_value=None) -> None:
        edited_json = json.dumps(edited_value, ensure_ascii=False) if edited_value is not None else None
        await conn.execute(
            """
            UPDATE review_items
            SET decision = ?, edited_value = COALESCE(?, edited_value),
                decided_at = ?
            WHERE id = ?
            """,
            (decision, edited_json, _now_iso(), item_id),
        )
        await conn.commit()

    async def mark_applied(self, conn: aiosqlite.Connection, item_ids: list[int]) -> None:
        await conn.executemany(
            "UPDATE review_items SET applied_at = ? WHERE id = ?",
            [(_now_iso(), i) for i in item_ids],
        )
        await conn.commit()

    @staticmethod
    def _row(row) -> ReviewItem:
        return ReviewItem(
            id=row[0],
            annotation_id=row[1],
            catdv_clip_id=row[2],
            kind=row[3],
            target_identifier=row[4],
            proposed_value=json.loads(row[5]),
            edited_value=json.loads(row[6]) if row[6] is not None else None,
            decision=row[7],
        )
