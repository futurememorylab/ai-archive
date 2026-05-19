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
                json.dumps(list(origin_ids), ensure_ascii=False)
                if origin_ids is not None
                else None
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

    async def get(
        self, conn: aiosqlite.Connection, op_id: int
    ) -> dict[str, Any] | None:
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

    async def mark_in_flight(
        self, conn: aiosqlite.Connection, op_ids: list[int]
    ) -> None:
        if not op_ids:
            return
        now = _now_iso()
        await conn.executemany(
            "UPDATE pending_operations "
            "SET status = 'in_flight', attempted_at = ? "
            "WHERE id = ?",
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
            json.dumps(conflict_detail, ensure_ascii=False)
            if conflict_detail is not None
            else None
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
    ) -> None:
        if not op_ids:
            return
        await conn.executemany(
            "UPDATE pending_operations "
            "SET status = 'failed', last_error = ?, attempted_at = ? "
            "WHERE id = ?",
            [(error, _now_iso(), oid) for oid in op_ids],
        )
        await conn.commit()

    async def reset_in_flight_to_pending(
        self, conn: aiosqlite.Connection
    ) -> int:
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
