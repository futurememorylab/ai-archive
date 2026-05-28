"""ReviewItemsRepo — persists / reads `review_items`; the per-clip
human-review queue produced by the annotator. Called by the annotator
service and the review route."""

import base64
import json
from datetime import UTC, datetime
from typing import Literal

import aiosqlite

from backend.app.models.annotation import ReviewItem


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _json_default(obj):
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, (bytes, bytearray)):
        return base64.b64encode(bytes(obj)).decode("ascii")
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


class ReviewItemsRepo:
    async def bulk_insert(
        self, conn: aiosqlite.Connection, items: list[ReviewItem]
    ) -> list[ReviewItem]:
        inserted: list[ReviewItem] = []
        for it in items:
            cur = await conn.execute(
                """
                INSERT INTO review_items
                  (annotation_id, studio_run_id, catdv_clip_id, kind,
                   target_identifier, proposed_value, edited_value, decision)
                VALUES (?, ?, ?, ?, ?, ?, NULL, 'pending')
                """,
                (
                    it.annotation_id,
                    it.studio_run_id,
                    it.catdv_clip_id,
                    it.kind,
                    it.target_identifier,
                    json.dumps(it.proposed_value, ensure_ascii=False, default=_json_default),
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
            SELECT id, annotation_id, studio_run_id, catdv_clip_id, kind,
                   target_identifier, proposed_value, edited_value, decision, applied_at
            FROM review_items WHERE id = ?
            """,
            (item_id,),
        )
        row = await cur.fetchone()
        if row is None:
            raise LookupError(f"review_item {item_id} not found")
        return self._row(row)

    async def list_by_clip(
        self, conn: aiosqlite.Connection, clip_id: int, *, decision: str | None = None
    ) -> list[ReviewItem]:
        if decision is not None:
            cur = await conn.execute(
                """
                SELECT id, annotation_id, studio_run_id, catdv_clip_id, kind,
                       target_identifier, proposed_value, edited_value, decision, applied_at
                FROM review_items WHERE catdv_clip_id = ? AND decision = ?
                ORDER BY id
                """,
                (clip_id, decision),
            )
        else:
            cur = await conn.execute(
                """
                SELECT id, annotation_id, studio_run_id, catdv_clip_id, kind,
                       target_identifier, proposed_value, edited_value, decision, applied_at
                FROM review_items WHERE catdv_clip_id = ?
                ORDER BY id
                """,
                (clip_id,),
            )
        return [self._row(r) for r in await cur.fetchall()]

    async def list_by_studio_run(
        self, conn: aiosqlite.Connection, studio_run_id: int
    ) -> list[ReviewItem]:
        cur = await conn.execute(
            """
            SELECT id, annotation_id, studio_run_id, catdv_clip_id, kind,
                   target_identifier, proposed_value, edited_value, decision, applied_at
            FROM review_items WHERE studio_run_id = ?
            ORDER BY id
            """,
            (studio_run_id,),
        )
        return [self._row(r) for r in await cur.fetchall()]

    async def delete_for_studio_run(
        self, conn: aiosqlite.Connection, *, studio_run_id: int
    ) -> int:
        """Delete all review_items linked to a studio_run. Used by the
        annotator's studio finalize to ensure a retry doesn't accumulate
        duplicate markers/fields on the same run_id. Safe on empty sets.
        """
        cur = await conn.execute(
            "DELETE FROM review_items WHERE studio_run_id = ?",
            (studio_run_id,),
        )
        await conn.commit()
        return cur.rowcount or 0

    async def set_decision(
        self,
        conn: aiosqlite.Connection,
        item_id: int,
        decision: Literal["pending", "accepted", "rejected"],
        *,
        edited_value=None,
    ) -> None:
        edited_json = (
            json.dumps(edited_value, ensure_ascii=False, default=_json_default)
            if edited_value is not None
            else None
        )
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

    async def mark_applied(
        self,
        conn: aiosqlite.Connection,
        item_ids: list[int],
        *,
        commit: bool = True,
    ) -> None:
        if not item_ids:
            return
        await conn.executemany(
            "UPDATE review_items SET applied_at = ? WHERE id = ?",
            [(_now_iso(), i) for i in item_ids],
        )
        if commit:
            await conn.commit()

    @staticmethod
    def _row(row) -> ReviewItem:
        return ReviewItem(
            id=row[0],
            annotation_id=row[1],
            studio_run_id=row[2],
            catdv_clip_id=row[3],
            kind=row[4],
            target_identifier=row[5],
            proposed_value=json.loads(row[6]),
            edited_value=json.loads(row[7]) if row[7] is not None else None,
            decision=row[8],
            applied_at=row[9] if len(row) > 9 else None,
        )
