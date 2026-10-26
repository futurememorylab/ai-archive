"""ReviewItemsRepo — persists / reads `review_items`; the per-clip
human-review queue produced by the annotator. Called by the annotator
service and the review route."""

import base64
import json
from datetime import UTC, datetime
from typing import Literal

import aiosqlite

from backend.app.archive.model import AI_OWNED_FIELDS
from backend.app.models.annotation import ReviewItem


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


# A "suggestion" is a field/note item whose target the app does not own (#123):
# it is never written to the archive, only offered for a human to copy. So it
# must NOT keep a clip in the pending-review count — a draft with only
# suggestions left counts as fully applied. Markers (target NULL) and writes to
# an owned field are real pending work. Built from AI_OWNED_FIELDS (code-only
# constants, so the inlined list is safe) with a column-prefix so it can be
# dropped into both aliased (`ri.`) and un-aliased queries.
_OWNED_FIELDS_SQL = ", ".join("'" + f.replace("'", "''") + "'" for f in AI_OWNED_FIELDS)


def _not_suggestion(prefix: str = "") -> str:
    kind = f"{prefix}kind"
    tid = f"{prefix}target_identifier"
    return f"NOT ({kind} IN ('field', 'note') AND COALESCE({tid}, '') NOT IN ({_OWNED_FIELDS_SQL}))"


# The "awaiting review" predicate (a WHERE body): clips whose LATEST annotation
# still has an undecided proposal — applied_at NULL, not rejected, not a
# suggestion, newest annotation only. Single source of truth so the
# /?anno=for_review filter (clip_list_filters), the topbar count
# (count_clips_for_review), the full-render sync context
# (routes/pages/templates.py), the Batches "Awaiting review" status
# (JobsRepo.list_batches) and the batch review walk (pending_clip_ids_for_jobs)
# can never drift apart. Guarded by tests/integration/test_awaiting_review_single_source.py.
def for_review_where(alias: str = "") -> str:
    """FOR_REVIEW_WHERE for a `review_items` table referenced as `alias`."""
    col = f"{alias}." if alias else ""
    table = alias or "review_items"
    return (
        f"{col}applied_at IS NULL AND {col}decision != 'rejected' "
        f"AND {_not_suggestion(col)} "
        f"AND {col}annotation_id = (SELECT MAX(a_latest.id) FROM annotations a_latest "
        f"WHERE a_latest.catdv_clip_id = {table}.catdv_clip_id)"
    )


FOR_REVIEW_WHERE = for_review_where()


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
                   target_identifier, proposed_value, edited_value, decision, applied_at, synced_at
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
                       target_identifier, proposed_value, edited_value, decision,
                       applied_at, synced_at
                FROM review_items WHERE catdv_clip_id = ? AND decision = ?
                ORDER BY id
                """,
                (clip_id, decision),
            )
        else:
            cur = await conn.execute(
                """
                SELECT id, annotation_id, studio_run_id, catdv_clip_id, kind,
                       target_identifier, proposed_value, edited_value, decision,
                       applied_at, synced_at
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
                   target_identifier, proposed_value, edited_value, decision, applied_at, synced_at
            FROM review_items WHERE studio_run_id = ?
            ORDER BY id
            """,
            (studio_run_id,),
        )
        return [self._row(r) for r in await cur.fetchall()]

    async def clear_unapplied_for_clip(self, conn: aiosqlite.Connection, clip_id: int) -> int:
        """Delete all review_items for a clip that have not yet been applied.

        Used by RestoreService to clear the working draft before re-seeding it
        from a published version's snapshot. Applied items are preserved as a
        historical record.
        """
        cur = await conn.execute(
            "DELETE FROM review_items WHERE catdv_clip_id = ? AND applied_at IS NULL",
            (clip_id,),
        )
        await conn.commit()
        return cur.rowcount or 0

    async def discard_draft_for_clip(
        self, conn: aiosqlite.Connection, clip_id: int, *, commit: bool = True
    ) -> int:
        """Delete a clip's annotation draft: every annotation-bound item not
        yet applied (undecided, accepted-not-applied or rejected).

        Used when the clip's AI annotations are removed from the archive so it
        can be annotated again from scratch. Applied items stay as the record of
        what was written; Studio rows (no annotation) are never touched.
        """
        cur = await conn.execute(
            "DELETE FROM review_items WHERE catdv_clip_id = ? "
            "AND annotation_id IS NOT NULL AND applied_at IS NULL",
            (clip_id,),
        )
        if commit:
            await conn.commit()
        return cur.rowcount or 0

    async def delete_for_studio_run(self, conn: aiosqlite.Connection, *, studio_run_id: int) -> int:
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
        author: str | None = None,
        action: Literal["decision", "bulk_accept"] = "decision",
    ) -> None:
        """Set an item's decision (and optionally its edited value), and append
        a review_edit_log row when anything actually changed — in the same
        commit, so a human correction is never recorded in the draft without
        also landing in the durable log (spec 2026-10-17)."""
        edited_json = (
            json.dumps(edited_value, ensure_ascii=False, default=_json_default)
            if edited_value is not None
            else None
        )
        cur = await conn.execute(
            """
            SELECT ri.catdv_clip_id, ri.annotation_id, ri.studio_run_id, ri.kind,
                   ri.target_identifier, ri.proposed_value, ri.decision, ri.edited_value,
                   COALESCE(a.prompt_version_id, sr.prompt_version_id),
                   COALESCE(a.model, sr.model)
            FROM review_items ri
            LEFT JOIN annotations a ON a.id = ri.annotation_id
            LEFT JOIN studio_run sr ON sr.id = ri.studio_run_id
            WHERE ri.id = ?
            """,
            (item_id,),
        )
        row = await cur.fetchone()
        now = _now_iso()
        if row is not None:
            edited_after = edited_json if edited_json is not None else row[7]
            if decision != row[6] or edited_after != row[7]:
                await conn.execute(
                    """
                    INSERT INTO review_edit_log
                      (occurred_at, author, action, review_item_id, catdv_clip_id,
                       annotation_id, studio_run_id, prompt_version_id, model, kind,
                       target_identifier, proposed_value, decision_before, decision_after,
                       edited_before, edited_after)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        now,
                        author,
                        action,
                        item_id,
                        row[0],
                        row[1],
                        row[2],
                        row[8],
                        row[9],
                        row[3],
                        row[4],
                        row[5],
                        row[6],
                        decision,
                        row[7],
                        edited_after,
                    ),
                )
        await conn.execute(
            """
            UPDATE review_items
            SET decision = ?, edited_value = COALESCE(?, edited_value),
                decided_at = ?
            WHERE id = ?
            """,
            (decision, edited_json, now, item_id),
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

    async def mark_synced(
        self,
        conn: aiosqlite.Connection,
        item_ids: list[int],
        *,
        commit: bool = True,
    ) -> None:
        """Stamp synced_at = now on the given items — the SyncEngine calls this
        once a clip's write-back actually lands on CatDV, so the UI can tell
        'confirmed upstream' from merely 'enqueued' (applied_at)."""
        if not item_ids:
            return
        now = _now_iso()
        await conn.executemany(
            "UPDATE review_items SET synced_at = ? WHERE id = ?",
            [(now, i) for i in item_ids],
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

        When `job_id` is given, qualification, counts, and display metadata
        are all scoped to items belonging to that job's annotations, so this
        stays consistent with `count_pending_clips(job_id=...)`. Metadata is
        taken from the newest matching annotation (MAX annotation_id; ids are
        autoincrement so higher == later).
        """
        params: list = []
        job_clause = ""
        if job_id is not None:
            job_clause = "AND a.job_id = ?"
            params.append(job_id)
        sql = f"""
            WITH pending AS (
              SELECT ri.catdv_clip_id AS catdv_clip_id,
                     ri.kind          AS kind,
                     ri.annotation_id AS annotation_id
              FROM review_items ri
              JOIN annotations a ON a.id = ri.annotation_id
              WHERE ri.applied_at IS NULL AND {_not_suggestion("ri.")} {job_clause}
            )
            SELECT
              p.catdv_clip_id                                    AS catdv_clip_id,
              MAX(p.annotation_id)                               AS annotation_id,
              SUM(CASE WHEN p.kind = 'marker' THEN 1 ELSE 0 END) AS marker_count,
              SUM(CASE WHEN p.kind = 'field'  THEN 1 ELSE 0 END) AS field_count,
              SUM(CASE WHEN p.kind = 'note'   THEN 1 ELSE 0 END) AS note_count,
              a.catdv_clip_name                                  AS catdv_clip_name,
              a.job_id                                           AS job_id,
              a.prompt_version_id                                AS prompt_version_id,
              a.created_at                                       AS created_at
            FROM pending p
            JOIN annotations a ON a.id = (
              SELECT MAX(p2.annotation_id) FROM pending p2
              WHERE p2.catdv_clip_id = p.catdv_clip_id
            )
            GROUP BY p.catdv_clip_id
            ORDER BY a.created_at DESC, p.catdv_clip_id DESC
            LIMIT ? OFFSET ?
        """
        params.extend([limit, offset])
        cur = await conn.execute(sql, tuple(params))
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, row, strict=True)) for row in await cur.fetchall()]

    async def pending_clip_ids_for_jobs(
        self, conn: aiosqlite.Connection, job_ids: list[int]
    ) -> list[int]:
        """Ordered distinct clip ids with un-applied review items across the
        given jobs (a batch's member jobs), newest annotation first — the
        review-walk queue for a batch. `job_ids` is bounded by the batch, so a
        single IN clause is safe."""
        if not job_ids:
            return []
        placeholders = ",".join("?" * len(job_ids))
        cur = await conn.execute(
            f"""
            SELECT ri.catdv_clip_id AS clip_id, MAX(a.created_at) AS created_at
            FROM review_items ri
            JOIN annotations a ON a.id = ri.annotation_id
            WHERE {for_review_where("ri")}
              AND a.job_id IN ({placeholders})
            GROUP BY ri.catdv_clip_id
            ORDER BY created_at DESC, ri.catdv_clip_id DESC
            """,
            tuple(job_ids),
        )
        return [int(r[0]) for r in await cur.fetchall()]

    async def count_pending_clips(
        self, conn: aiosqlite.Connection, *, job_id: int | None = None
    ) -> int:
        params: list = []
        job_clause = ""
        if job_id is not None:
            job_clause = "AND a.job_id = ?"
            params.append(job_id)
        cur = await conn.execute(
            f"""
            SELECT COUNT(DISTINCT ri.catdv_clip_id)
            FROM review_items ri
            JOIN annotations a ON a.id = ri.annotation_id
            WHERE ri.applied_at IS NULL AND {_not_suggestion("ri.")} {job_clause}
            """,
            tuple(params),
        )
        row = await cur.fetchone()
        return int(row[0]) if row else 0

    async def count_clips_for_review(self, conn: aiosqlite.Connection) -> int:
        """Clips whose LATEST annotation still has an undecided proposal — the
        "N to review" topbar count and the /?anno=for_review list. Excludes
        rejected items (decided) and items from a superseded older annotation
        (the draft panel shows only the latest annotation). Backs the
        /ui/review-pill refresh poll; shares FOR_REVIEW_WHERE with the
        clips-list filter and the full-render sync context."""
        cur = await conn.execute(
            f"SELECT COUNT(DISTINCT catdv_clip_id) FROM review_items WHERE {FOR_REVIEW_WHERE}"
        )
        row = await cur.fetchone()
        return int(row[0]) if row else 0

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
            synced_at=row[10] if len(row) > 10 else None,
        )
