"""LiveSessionsRepo — CRUD + state machine for the `live_sessions` table.

State transitions:
  pending  ──(mark_active)──▶  active  ──(mark_ended)──▶  ended
                  └────────(mark_ended)────────────────▶  ended  (mic denied, etc.)

`set_summary` is idempotent — once non-null it never overwrites.
"""
from datetime import datetime, timedelta, timezone

import aiosqlite

from backend.app.models.live_session import LiveSession


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


_COLS = (
    "id, clip_id, prompt_version, state, started_at, ended_at, end_reason, "
    "transcript_json, summary_cs, frame_count, search_calls, created_at"
)


def _row(r) -> LiveSession:
    return LiveSession(
        id=r[0], clip_id=r[1], prompt_version=r[2], state=r[3],
        started_at=r[4], ended_at=r[5], end_reason=r[6],
        transcript_json=r[7], summary_cs=r[8],
        frame_count=r[9], search_calls=r[10], created_at=r[11],
    )


class LiveSessionsRepo:
    async def insert_pending(
        self, conn: aiosqlite.Connection,
        *, id: str, clip_id: int, prompt_version: int | None,
    ) -> None:
        await conn.execute(
            "INSERT INTO live_sessions (id, clip_id, prompt_version, state, created_at) "
            "VALUES (?, ?, ?, 'pending', ?)",
            (id, clip_id, prompt_version, _now_iso()),
        )
        await conn.commit()

    async def mark_active(self, conn: aiosqlite.Connection, id: str) -> None:
        await conn.execute(
            "UPDATE live_sessions SET state='active', started_at=? WHERE id=?",
            (_now_iso(), id),
        )
        await conn.commit()

    async def mark_ended(
        self, conn: aiosqlite.Connection, id: str,
        *, end_reason: str, transcript_json: str,
        frame_count: int = 0, search_calls: int = 0,
    ) -> None:
        await conn.execute(
            "UPDATE live_sessions SET state='ended', ended_at=?, end_reason=?, "
            "transcript_json=?, frame_count=?, search_calls=? WHERE id=?",
            (_now_iso(), end_reason, transcript_json, frame_count, search_calls, id),
        )
        await conn.commit()

    async def set_summary(self, conn: aiosqlite.Connection, id: str, summary: str) -> bool:
        """Idempotent — only writes when `summary_cs` is currently NULL. Returns True if written."""
        cur = await conn.execute(
            "UPDATE live_sessions SET summary_cs=? WHERE id=? AND summary_cs IS NULL",
            (summary, id),
        )
        await conn.commit()
        return cur.rowcount > 0

    async def get(self, conn: aiosqlite.Connection, id: str) -> LiveSession:
        cur = await conn.execute(f"SELECT {_COLS} FROM live_sessions WHERE id=?", (id,))
        row = await cur.fetchone()
        if row is None:
            raise LookupError(f"live_session {id} not found")
        return _row(row)

    async def list_by_clip(
        self, conn: aiosqlite.Connection, clip_id: int,
    ) -> list[LiveSession]:
        cur = await conn.execute(
            f"SELECT {_COLS} FROM live_sessions WHERE clip_id=? "
            "ORDER BY created_at DESC",
            (clip_id,),
        )
        return [_row(r) for r in await cur.fetchall()]

    async def cleanup_stale_pending(
        self, conn: aiosqlite.Connection, older_than_hours: int = 1,
    ) -> int:
        """Delete pending rows older than `older_than_hours`. Returns rows deleted."""
        cutoff_iso = (
            datetime.now(timezone.utc) - timedelta(hours=older_than_hours)
        ).isoformat()
        cur = await conn.execute(
            "DELETE FROM live_sessions WHERE state='pending' AND created_at < ?",
            (cutoff_iso,),
        )
        await conn.commit()
        return cur.rowcount
