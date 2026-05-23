"""PrefetchQueueRepo — persists / reads `prefetch_queue`; queued and
in-flight proxy downloads. Called by MediaPrefetcher."""

from datetime import UTC, datetime
from typing import Any

import aiosqlite

from backend.app.archive.model import ClipKey

ACTIVE_STATUSES = ("queued", "downloading")
TERMINAL_STATUSES = ("done", "error", "cancelled")


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _row_to_dict(row) -> dict[str, Any]:
    keys = (
        "id",
        "provider_id",
        "provider_clip_id",
        "status",
        "requested_by",
        "requested_at",
        "started_at",
        "finished_at",
        "error",
        "bytes_downloaded",
    )
    return dict(zip(keys, row, strict=False))


def _row_to_dict_with_name(row) -> dict[str, Any]:
    keys = (
        "id",
        "provider_id",
        "provider_clip_id",
        "status",
        "requested_by",
        "requested_at",
        "started_at",
        "finished_at",
        "error",
        "bytes_downloaded",
        "clip_name",
    )
    return dict(zip(keys, row, strict=False))


_LIST_COLUMNS_WITH_NAME = """
    q.id, q.provider_id, q.provider_clip_id, q.status,
    q.requested_by, q.requested_at, q.started_at, q.finished_at,
    q.error, q.bytes_downloaded,
    cc.name AS clip_name
"""


class PrefetchQueueRepo:
    async def enqueue(
        self,
        conn: aiosqlite.Connection,
        *,
        key: ClipKey,
        who: str,
    ) -> int:
        """Enqueue a prefetch. If an active row already exists for the
        clip, return its id (idempotent)."""
        cur = await conn.execute(
            """
            SELECT id FROM prefetch_queue
             WHERE provider_id = ? AND provider_clip_id = ?
               AND status IN ('queued', 'downloading')
             LIMIT 1
            """,
            (key[0], key[1]),
        )
        existing = await cur.fetchone()
        if existing is not None:
            return int(existing[0])
        cur = await conn.execute(
            """
            INSERT INTO prefetch_queue
              (provider_id, provider_clip_id, status,
               requested_by, requested_at, bytes_downloaded)
            VALUES (?, ?, 'queued', ?, ?, 0)
            """,
            (key[0], key[1], who, _now_iso()),
        )
        await conn.commit()
        assert cur.lastrowid is not None
        return int(cur.lastrowid)

    async def claim_next(self, conn: aiosqlite.Connection) -> dict[str, Any] | None:
        """Atomically take the oldest queued row and mark it downloading.

        Returns the claimed row (with status now `downloading`) or None
        if the queue is empty.
        """
        await conn.execute("BEGIN IMMEDIATE")
        try:
            cur = await conn.execute(
                """
                SELECT id, provider_id, provider_clip_id, status,
                       requested_by, requested_at, started_at, finished_at,
                       error, bytes_downloaded
                  FROM prefetch_queue
                 WHERE status = 'queued'
                 ORDER BY requested_at ASC
                 LIMIT 1
                """
            )
            row = await cur.fetchone()
            if row is None:
                await conn.commit()
                return None
            rid = int(row[0])
            now = _now_iso()
            await conn.execute(
                "UPDATE prefetch_queue "
                "   SET status='downloading', started_at=? "
                " WHERE id=? AND status='queued'",
                (now, rid),
            )
            await conn.commit()
        except Exception:
            await conn.rollback()
            raise
        # Re-read so callers see the updated status/started_at.
        return await self.get(conn, rid)

    async def get(self, conn: aiosqlite.Connection, rid: int) -> dict[str, Any] | None:
        cur = await conn.execute(
            """
            SELECT id, provider_id, provider_clip_id, status,
                   requested_by, requested_at, started_at, finished_at,
                   error, bytes_downloaded
              FROM prefetch_queue WHERE id = ?
            """,
            (rid,),
        )
        row = await cur.fetchone()
        return _row_to_dict(row) if row is not None else None

    async def mark_done(
        self,
        conn: aiosqlite.Connection,
        rid: int,
        *,
        bytes_downloaded: int,
    ) -> None:
        await conn.execute(
            "UPDATE prefetch_queue "
            "   SET status='done', finished_at=?, bytes_downloaded=? "
            " WHERE id=?",
            (_now_iso(), int(bytes_downloaded), rid),
        )
        await conn.commit()

    async def mark_error(
        self,
        conn: aiosqlite.Connection,
        rid: int,
        message: str,
    ) -> None:
        await conn.execute(
            "UPDATE prefetch_queue    SET status='error', finished_at=?, error=?  WHERE id=?",
            (_now_iso(), message[:500], rid),
        )
        await conn.commit()

    async def mark_cancelled(self, conn: aiosqlite.Connection, rid: int) -> bool:
        """Cancel a queued/error row. Returns False (without mutating)
        if the row is `downloading` or already terminal."""
        cur = await conn.execute(
            "UPDATE prefetch_queue "
            "   SET status='cancelled', finished_at=? "
            " WHERE id=? AND status IN ('queued', 'error')",
            (_now_iso(), rid),
        )
        await conn.commit()
        return cur.rowcount > 0

    async def list_active(self, conn: aiosqlite.Connection) -> list[dict[str, Any]]:
        cur = await conn.execute(
            f"""
            SELECT {_LIST_COLUMNS_WITH_NAME}
              FROM prefetch_queue q
              LEFT JOIN clip_cache cc
                ON cc.provider_id = q.provider_id
               AND cc.provider_clip_id = q.provider_clip_id
             WHERE q.status IN ('queued', 'downloading')
             ORDER BY q.requested_at ASC
            """
        )
        return [_row_to_dict_with_name(r) for r in await cur.fetchall()]

    async def list_recent(
        self,
        conn: aiosqlite.Connection,
        *,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        cur = await conn.execute(
            f"""
            SELECT {_LIST_COLUMNS_WITH_NAME}
              FROM prefetch_queue q
              LEFT JOIN clip_cache cc
                ON cc.provider_id = q.provider_id
               AND cc.provider_clip_id = q.provider_clip_id
             ORDER BY q.requested_at DESC
             LIMIT ?
            """,
            (limit,),
        )
        return [_row_to_dict_with_name(r) for r in await cur.fetchall()]

    async def count_by_status(self, conn: aiosqlite.Connection) -> dict[str, int]:
        cur = await conn.execute("SELECT status, COUNT(*) FROM prefetch_queue GROUP BY status")
        return {row[0]: int(row[1]) for row in await cur.fetchall()}
