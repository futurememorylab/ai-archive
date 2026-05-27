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
                  (annotation_id, catdv_clip_id, kind, target_identifier,
                   proposed_value, edited_value, decision)
                VALUES (?, ?, ?, ?, ?, NULL, 'pending')
                """,
                (
                    it.annotation_id,
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
            SELECT id, annotation_id, catdv_clip_id, kind, target_identifier,
                   proposed_value, edited_value, decision, applied_at
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
                SELECT id, annotation_id, catdv_clip_id, kind, target_identifier,
                       proposed_value, edited_value, decision, applied_at
                FROM review_items WHERE catdv_clip_id = ? AND decision = ?
                ORDER BY id
                """,
                (clip_id, decision),
            )
        else:
            cur = await conn.execute(
                """
                SELECT id, annotation_id, catdv_clip_id, kind, target_identifier,
                       proposed_value, edited_value, decision, applied_at
                FROM review_items WHERE catdv_clip_id = ?
                ORDER BY id
                """,
                (clip_id,),
            )
        return [self._row(r) for r in await cur.fetchall()]

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

    async def list_pending_clips(
        self,
        conn: aiosqlite.Connection,
        *,
        job_id: int | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict]:
        """One row per clip with un-applied review items, newest first.

        Counts are over items with applied_at IS NULL. Clip metadata comes
        from the most-recent annotation owning those items (MAX(annotation_id)).
        """
        params: list = []
        job_clause = ""
        if job_id is not None:
            job_clause = "AND a.job_id = ?"
            params.append(job_id)
        sql = f"""
            SELECT
              ri.catdv_clip_id                                   AS catdv_clip_id,
              MAX(ri.annotation_id)                              AS annotation_id,
              SUM(CASE WHEN ri.kind = 'marker' THEN 1 ELSE 0 END) AS marker_count,
              SUM(CASE WHEN ri.kind = 'field'  THEN 1 ELSE 0 END) AS field_count,
              SUM(CASE WHEN ri.kind = 'note'   THEN 1 ELSE 0 END) AS note_count,
              a.catdv_clip_name                                  AS catdv_clip_name,
              a.job_id                                           AS job_id,
              a.prompt_version_id                                AS prompt_version_id,
              a.created_at                                       AS created_at
            FROM review_items ri
            JOIN annotations a
              ON a.id = (
                SELECT MAX(ri2.annotation_id)
                FROM review_items ri2
                WHERE ri2.catdv_clip_id = ri.catdv_clip_id
                  AND ri2.applied_at IS NULL
              )
            WHERE ri.applied_at IS NULL {job_clause}
            GROUP BY ri.catdv_clip_id
            ORDER BY a.created_at DESC, ri.catdv_clip_id DESC
            LIMIT ? OFFSET ?
        """
        params.extend([limit, offset])
        cur = await conn.execute(sql, tuple(params))
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, row, strict=True)) for row in await cur.fetchall()]

    async def count_pending_clips(
        self, conn: aiosqlite.Connection, *, job_id: int | None = None
    ) -> int:
        params: list = []
        job_clause = ""
        if job_id is not None:
            job_clause = (
                "AND ri.annotation_id IN "
                "(SELECT id FROM annotations WHERE job_id = ?)"
            )
            params.append(job_id)
        cur = await conn.execute(
            f"""
            SELECT COUNT(DISTINCT ri.catdv_clip_id)
            FROM review_items ri
            WHERE ri.applied_at IS NULL {job_clause}
            """,
            tuple(params),
        )
        row = await cur.fetchone()
        return int(row[0]) if row else 0

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
            applied_at=row[8] if len(row) > 8 else None,
        )
