"""Durable journal of upstream writes.

One row per ChangeOp. The SyncEngine reads `status='pending'` rows in
enqueue order (with attempts-aware backoff) and transitions them through
`in_flight` to one of `applied`, `conflict`, `failed` — or back to
`pending` on a retryable error (with `attempts` incremented and
`last_error` populated).
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

import aiosqlite


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


_ROW_COLS = (
    "id",
    "provider_id",
    "provider_clip_id",
    "op_kind",
    "op_json",
    "origin_annotation_id",
    "origin_review_item_ids",
    "expected_etag",
    "status",
    "attempts",
    "last_error",
    "enqueued_at",
    "attempted_at",
    "applied_at",
)


def _row_to_dict(row: Iterable[Any]) -> dict[str, Any]:
    return dict(zip(_ROW_COLS, row, strict=False))


# Shared SET fragment for the three "retry" resets. A conflict means the clip
# changed upstream after the user reviewed it, so the stored expected_etag no
# longer matches live; replaying it would just re-conflict (the adapter rejects
# on etag mismatch). An explicit retry is the user choosing to apply anyway, so
# we drop the etag and let the change re-base on the current clip. FAILED rows
# (transport/unknown errors) keep their etag, so a genuine concurrent upstream
# change still surfaces as a conflict rather than being silently overwritten.
# `status` here is the PRE-update value — SQLite evaluates SET right-hand sides
# against the original row, even though the same statement also sets status.
# See ADR 0098.
_CONFLICT_RETRY_ETAG = (
    "expected_etag = CASE WHEN status = 'conflict' THEN NULL ELSE expected_etag END"
)


class PendingOperationsRepo:
    """DB-backed pending_operations journal."""

    async def insert_many(
        self,
        conn: aiosqlite.Connection,
        *,
        rows: list[dict[str, Any]],
        commit: bool = True,
    ) -> list[int]:
        """Insert one row per ChangeOp.

        Each `rows` entry must contain: provider_id, provider_clip_id,
        op_kind, op_json (str), origin_annotation_id (int|None),
        origin_review_item_ids (list[int]|None), expected_etag (str|None).
        Status is forced to 'pending', attempts to 0, enqueued_at to now.
        Returns the inserted op ids in order.
        """
        ids: list[int] = []
        now = _now_iso()
        for r in rows:
            origin_ids = r.get("origin_review_item_ids")
            origin_ids_json = (
                json.dumps(list(origin_ids), ensure_ascii=False) if origin_ids is not None else None
            )
            cur = await conn.execute(
                """
                INSERT INTO pending_operations
                  (provider_id, provider_clip_id, op_kind, op_json,
                   origin_annotation_id, origin_review_item_ids, expected_etag,
                   status, attempts, enqueued_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', 0, ?)
                """,
                (
                    r["provider_id"],
                    r["provider_clip_id"],
                    r["op_kind"],
                    r["op_json"],
                    r.get("origin_annotation_id"),
                    origin_ids_json,
                    r.get("expected_etag"),
                    now,
                ),
            )
            ids.append(cur.lastrowid)
        if commit:
            await conn.commit()
        return ids

    async def get(self, conn: aiosqlite.Connection, op_id: int) -> dict[str, Any] | None:
        cur = await conn.execute(
            f"SELECT {', '.join(_ROW_COLS)} FROM pending_operations WHERE id = ?",
            (op_id,),
        )
        row = await cur.fetchone()
        return _row_to_dict(row) if row is not None else None

    async def list_pending(
        self,
        conn: aiosqlite.Connection,
        *,
        status: str = "pending",
    ) -> list[dict[str, Any]]:
        cur = await conn.execute(
            f"SELECT {', '.join(_ROW_COLS)} FROM pending_operations "
            "WHERE status = ? ORDER BY enqueued_at, id",
            (status,),
        )
        return [_row_to_dict(r) for r in await cur.fetchall()]

    async def list_pending_for_clip(
        self,
        conn: aiosqlite.Connection,
        *,
        provider_id: str,
        provider_clip_id: str,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        if status is None:
            cur = await conn.execute(
                f"SELECT {', '.join(_ROW_COLS)} FROM pending_operations "
                "WHERE provider_id = ? AND provider_clip_id = ? "
                "ORDER BY enqueued_at, id",
                (provider_id, provider_clip_id),
            )
        else:
            cur = await conn.execute(
                f"SELECT {', '.join(_ROW_COLS)} FROM pending_operations "
                "WHERE provider_id = ? AND provider_clip_id = ? AND status = ? "
                "ORDER BY enqueued_at, id",
                (provider_id, provider_clip_id, status),
            )
        return [_row_to_dict(r) for r in await cur.fetchall()]

    async def mark_in_flight(self, conn: aiosqlite.Connection, op_ids: list[int]) -> None:
        if not op_ids:
            return
        now = _now_iso()
        await conn.executemany(
            "UPDATE pending_operations SET status = 'in_flight', attempted_at = ? WHERE id = ?",
            [(now, oid) for oid in op_ids],
        )
        await conn.commit()

    async def mark_applied(
        self,
        conn: aiosqlite.Connection,
        op_ids: list[int],
        *,
        applied_at: str | None = None,
    ) -> None:
        if not op_ids:
            return
        ts = applied_at or _now_iso()
        await conn.executemany(
            "UPDATE pending_operations "
            "SET status = 'applied', applied_at = ?, last_error = NULL "
            "WHERE id = ?",
            [(ts, oid) for oid in op_ids],
        )
        await conn.commit()

    async def mark_conflict(
        self,
        conn: aiosqlite.Connection,
        op_ids: list[int],
        *,
        conflict_detail: dict[str, Any] | None = None,
        attempted_at: str | None = None,
    ) -> None:
        if not op_ids:
            return
        ts = attempted_at or _now_iso()
        detail_json = (
            json.dumps(conflict_detail, ensure_ascii=False) if conflict_detail is not None else None
        )
        await conn.executemany(
            "UPDATE pending_operations "
            "SET status = 'conflict', attempted_at = ?, last_error = ? "
            "WHERE id = ?",
            [(ts, detail_json, oid) for oid in op_ids],
        )
        await conn.commit()

    async def mark_retryable(
        self,
        conn: aiosqlite.Connection,
        op_ids: list[int],
        *,
        error: str,
        attempted_at: str | None = None,
    ) -> None:
        """Bump attempts, leave status='pending'."""
        if not op_ids:
            return
        ts = attempted_at or _now_iso()
        await conn.executemany(
            "UPDATE pending_operations "
            "SET status = 'pending', attempts = attempts + 1, "
            "    attempted_at = ?, last_error = ? "
            "WHERE id = ?",
            [(ts, error, oid) for oid in op_ids],
        )
        await conn.commit()

    async def mark_failed(
        self,
        conn: aiosqlite.Connection,
        op_ids: list[int],
        *,
        error: str,
        bump_attempts: bool = False,
    ) -> None:
        """Set status='failed'. When `bump_attempts` is True, also
        increments `attempts` atomically in the same statement — used by
        SyncEngine when the unknown-exception ceiling is reached so the
        attempt count reflects the final try rather than the previous one.
        """
        if not op_ids:
            return
        attempts_expr = "attempts + 1" if bump_attempts else "attempts"
        await conn.executemany(
            f"UPDATE pending_operations "
            f"SET status = 'failed', attempts = {attempts_expr}, "
            f"    last_error = ?, attempted_at = ? "
            f"WHERE id = ?",
            [(error, _now_iso(), oid) for oid in op_ids],
        )
        await conn.commit()

    async def reset_in_flight_to_pending(self, conn: aiosqlite.Connection) -> int:
        """Crash-recovery: rows stuck in_flight become pending again.

        Returns the number of rows reset.
        """
        cur = await conn.execute(
            "UPDATE pending_operations "
            "SET status = 'pending', attempted_at = NULL "
            "WHERE status = 'in_flight'"
        )
        await conn.commit()
        return cur.rowcount or 0

    async def delete(self, conn: aiosqlite.Connection, op_id: int) -> int:
        """Discard a pending_operations row outright.

        Sync drawer "discard" action: the user gives up on this write.
        The originating `review_items.applied_at` is intentionally left
        as-is — re-applying the same items would no-op via the WriteQueue
        dedup.
        """
        cur = await conn.execute("DELETE FROM pending_operations WHERE id = ?", (op_id,))
        await conn.commit()
        return cur.rowcount or 0

    async def reset_for_retry(self, conn: aiosqlite.Connection, op_id: int) -> int:
        """Reset a row back to a fresh `pending` state for the sync drawer.

        Zeros attempts, clears last_error and attempted_at, regardless of
        the current status (including conflict / failed). A conflict row also
        has its stale expected_etag dropped so the retry re-bases on the live
        clip instead of re-conflicting (see _CONFLICT_RETRY_ETAG). Returns the
        number of rows updated (0 or 1).
        """
        cur = await conn.execute(
            f"""
            UPDATE pending_operations
               SET status = 'pending', attempts = 0,
                   last_error = NULL, attempted_at = NULL,
                   {_CONFLICT_RETRY_ETAG}
             WHERE id = ?
            """,
            (op_id,),
        )
        await conn.commit()
        return cur.rowcount or 0

    async def count_pending_by_clip(
        self,
        conn: aiosqlite.Connection,
        *,
        provider_id: str,
    ) -> dict[str, dict[str, int]]:
        """Per-clip queued / conflict counts for the badge.

        Returns `{provider_clip_id: {"pending": N, "conflict": M}}`. Only
        clips with at least one non-terminal row appear.
        """
        cur = await conn.execute(
            """
            SELECT provider_clip_id, status, COUNT(*)
              FROM pending_operations
             WHERE provider_id = ?
               AND status IN ('pending', 'in_flight', 'conflict')
             GROUP BY provider_clip_id, status
            """,
            (provider_id,),
        )
        out: dict[str, dict[str, int]] = {}
        for clip_id, status, n in await cur.fetchall():
            bucket = out.setdefault(clip_id, {"pending": 0, "conflict": 0})
            if status == "conflict":
                bucket["conflict"] += n
            else:  # pending + in_flight both count as "queued"
                bucket["pending"] += n
        return out

    async def reset_all_for_retry(self, conn: aiosqlite.Connection) -> int:
        """Bulk 'Retry all': reset every failed/conflict row to a fresh pending
        state (attempts, last_error, attempted_at cleared) so the SyncEngine
        re-attempts them. Pending / in_flight rows are left untouched. Returns
        the number of rows reset."""
        cur = await conn.execute(
            f"""
            UPDATE pending_operations
               SET status = 'pending', attempts = 0,
                   last_error = NULL, attempted_at = NULL,
                   {_CONFLICT_RETRY_ETAG}
             WHERE status IN ('failed', 'conflict')
            """
        )
        await conn.commit()
        return cur.rowcount or 0

    async def reset_clip_for_retry(
        self, conn: aiosqlite.Connection, *, provider_id: str, provider_clip_id: str
    ) -> int:
        """Retry one clip (grouped drawer): reset its failed/conflict ops to a
        fresh pending state. Pending / in_flight ops are left alone. Returns
        rows reset."""
        cur = await conn.execute(
            f"""
            UPDATE pending_operations
               SET status = 'pending', attempts = 0,
                   last_error = NULL, attempted_at = NULL,
                   {_CONFLICT_RETRY_ETAG}
             WHERE provider_id = ? AND provider_clip_id = ?
               AND status IN ('failed', 'conflict')
            """,
            (provider_id, provider_clip_id),
        )
        await conn.commit()
        return cur.rowcount or 0

    async def delete_clip_pending(
        self, conn: aiosqlite.Connection, *, provider_id: str, provider_clip_id: str
    ) -> int:
        """Discard one clip (grouped drawer): delete all its non-applied ops.
        The originating review_items.applied_at is left as-is (matching the
        single-op `delete`). Returns rows deleted."""
        cur = await conn.execute(
            "DELETE FROM pending_operations "
            "WHERE provider_id = ? AND provider_clip_id = ? AND status != 'applied'",
            (provider_id, provider_clip_id),
        )
        await conn.commit()
        return cur.rowcount or 0

    async def count_actionable(self, conn: aiosqlite.Connection) -> dict[str, int]:
        """Global counts behind the topbar sync indicator: `queued`
        (pending + in_flight — work in motion) and `problems`
        (failed + conflict — needs the user). Provider-agnostic; one grouped
        query. Lets a write-back that exhausted its retries (or hit a conflict)
        show up app-wide, not only on its clip's draft page. See ADR 0091.
        """
        cur = await conn.execute(
            "SELECT status, COUNT(*) FROM pending_operations "
            "WHERE status IN ('pending', 'in_flight', 'failed', 'conflict') "
            "GROUP BY status"
        )
        rows = {status: n for status, n in await cur.fetchall()}
        return {
            "queued": rows.get("pending", 0) + rows.get("in_flight", 0),
            "problems": rows.get("failed", 0) + rows.get("conflict", 0),
        }

    async def status_counts_for_clip(
        self,
        conn: aiosqlite.Connection,
        *,
        provider_id: str,
        provider_clip_id: str,
    ) -> dict[str, int]:
        """Per-status row counts for one clip's writeback ops.

        Returns `{status: count}` for every status that has at least one row
        (e.g. `{"pending": 1, "failed": 2, "applied": 8}`). Backs the
        per-clip sync-status poll the draft UI uses to learn when a writeback
        has finished (or failed) without a page reload. One grouped query —
        bounded by the handful of ops a clip can have.
        """
        cur = await conn.execute(
            """
            SELECT status, COUNT(*)
              FROM pending_operations
             WHERE provider_id = ? AND provider_clip_id = ?
             GROUP BY status
            """,
            (provider_id, provider_clip_id),
        )
        return {status: n for status, n in await cur.fetchall()}

    async def list_with_clip_names(
        self,
        conn: aiosqlite.Connection,
        *,
        statuses: tuple[str, ...] = ("pending", "in_flight", "conflict", "failed"),
    ) -> list[dict[str, Any]]:
        """Sync drawer rows: pending_operations joined with clip name."""
        placeholders = ",".join("?" for _ in statuses)
        cur = await conn.execute(
            f"""
            SELECT po.id, po.provider_id, po.provider_clip_id, po.op_kind,
                   po.status, po.attempts, po.last_error,
                   po.enqueued_at, po.attempted_at, po.applied_at,
                   cc.name
              FROM pending_operations po
              LEFT JOIN clip_cache cc
                ON cc.provider_id = po.provider_id
               AND cc.provider_clip_id = po.provider_clip_id
             WHERE po.status IN ({placeholders})
             ORDER BY po.enqueued_at, po.id
            """,
            statuses,
        )
        cols = (
            "id",
            "provider_id",
            "provider_clip_id",
            "op_kind",
            "status",
            "attempts",
            "last_error",
            "enqueued_at",
            "attempted_at",
            "applied_at",
            "clip_name",
        )
        return [dict(zip(cols, r, strict=True)) for r in await cur.fetchall()]
