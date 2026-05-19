"""Workspaces + workspace_clips persistence.

A workspace is a named pinned subset of an archive's clips. Membership is
stored in `workspace_clips`; the lifecycle column on `clip_cache`
(`pinned_to_workspace_id`) tracks the *primary* pin (last-set-wins) for
the FK invariant — but the source of truth for "is this clip pinned?" is
`workspace_clips`.

This repo is raw-SQL over `aiosqlite`. Higher-level lifecycle lives in
`services/workspace_manager.py`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import aiosqlite

from backend.app.archive.model import ClipKey


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


_WS_COLS = ("id", "name", "provider_id", "catalog_id", "created_at", "description")
_WC_COLS = ("workspace_id", "provider_id", "provider_clip_id",
            "added_at", "cache_state", "cache_error")


class WorkspacesRepo:
    """DB-backed workspaces + workspace_clips."""

    # --- workspaces ---------------------------------------------------

    async def create(
        self,
        conn: aiosqlite.Connection,
        *,
        name: str,
        provider_id: str,
        catalog_id: str,
        description: str | None = None,
    ) -> int:
        cur = await conn.execute(
            """
            INSERT INTO workspaces (name, provider_id, catalog_id,
                                    created_at, description)
            VALUES (?, ?, ?, ?, ?)
            """,
            (name, provider_id, catalog_id, _now_iso(), description),
        )
        await conn.commit()
        return cur.lastrowid

    async def get(
        self, conn: aiosqlite.Connection, ws_id: int
    ) -> dict[str, Any] | None:
        cur = await conn.execute(
            f"SELECT {', '.join(_WS_COLS)} FROM workspaces WHERE id = ?",
            (ws_id,),
        )
        row = await cur.fetchone()
        return dict(zip(_WS_COLS, row, strict=True)) if row else None

    async def list(self, conn: aiosqlite.Connection) -> list[dict[str, Any]]:
        cur = await conn.execute(
            f"SELECT {', '.join(_WS_COLS)} FROM workspaces ORDER BY id"
        )
        return [dict(zip(_WS_COLS, r, strict=True)) for r in await cur.fetchall()]

    async def delete(self, conn: aiosqlite.Connection, ws_id: int) -> None:
        # workspace_clips rows go via ON DELETE CASCADE.
        await conn.execute("DELETE FROM workspaces WHERE id = ?", (ws_id,))
        await conn.commit()

    # --- workspace_clips ----------------------------------------------

    async def add_clips(
        self,
        conn: aiosqlite.Connection,
        ws_id: int,
        clip_keys: list[ClipKey],
        *,
        initial_state: str = "pending",
    ) -> int:
        """Upsert workspace_clips rows. Returns the count of *new* rows.

        Existing rows are left untouched (their cache_state is preserved
        so resumable prep just picks up where it left off).
        """
        added = 0
        now = _now_iso()
        for provider_id, provider_clip_id in clip_keys:
            cur = await conn.execute(
                """
                INSERT OR IGNORE INTO workspace_clips
                  (workspace_id, provider_id, provider_clip_id,
                   added_at, cache_state, cache_error)
                VALUES (?, ?, ?, ?, ?, NULL)
                """,
                (ws_id, provider_id, provider_clip_id, now, initial_state),
            )
            added += cur.rowcount or 0
        await conn.commit()
        return added

    async def remove_clips(
        self,
        conn: aiosqlite.Connection,
        ws_id: int,
        clip_keys: list[ClipKey],
    ) -> int:
        removed = 0
        for provider_id, provider_clip_id in clip_keys:
            cur = await conn.execute(
                """
                DELETE FROM workspace_clips
                 WHERE workspace_id = ?
                   AND provider_id = ?
                   AND provider_clip_id = ?
                """,
                (ws_id, provider_id, provider_clip_id),
            )
            removed += cur.rowcount or 0
        await conn.commit()
        return removed

    async def list_clips(
        self, conn: aiosqlite.Connection, ws_id: int
    ) -> list[dict[str, Any]]:
        cur = await conn.execute(
            f"SELECT {', '.join(_WC_COLS)} FROM workspace_clips "
            "WHERE workspace_id = ? ORDER BY added_at, provider_clip_id",
            (ws_id,),
        )
        return [dict(zip(_WC_COLS, r, strict=True)) for r in await cur.fetchall()]

    async def set_cache_state(
        self,
        conn: aiosqlite.Connection,
        ws_id: int,
        clip_key: ClipKey,
        state: str,
        *,
        error: str | None = None,
    ) -> None:
        await conn.execute(
            """
            UPDATE workspace_clips
               SET cache_state = ?, cache_error = ?
             WHERE workspace_id = ?
               AND provider_id = ?
               AND provider_clip_id = ?
            """,
            (state, error, ws_id, clip_key[0], clip_key[1]),
        )
        await conn.commit()

    # --- views the cache layer cares about ----------------------------

    async def pinned_clip_keys(
        self, conn: aiosqlite.Connection, ws_id: int
    ) -> list[ClipKey]:
        cur = await conn.execute(
            "SELECT provider_id, provider_clip_id FROM workspace_clips "
            "WHERE workspace_id = ?",
            (ws_id,),
        )
        return [(r[0], r[1]) for r in await cur.fetchall()]

    async def workspaces_pinning(
        self, conn: aiosqlite.Connection, clip_key: ClipKey
    ) -> list[int]:
        """Workspace IDs that include this clip in their workspace_clips."""
        cur = await conn.execute(
            """
            SELECT workspace_id FROM workspace_clips
             WHERE provider_id = ? AND provider_clip_id = ?
             ORDER BY workspace_id
            """,
            (clip_key[0], clip_key[1]),
        )
        return [r[0] for r in await cur.fetchall()]

    # --- primary-pin maintenance --------------------------------------

    async def set_primary_pin(
        self,
        conn: aiosqlite.Connection,
        clip_key: ClipKey,
        ws_id: int | None,
    ) -> None:
        """Update clip_cache.pinned_to_workspace_id for `clip_key`.

        The row must already exist (write-through from `provider.get_clip()`).
        """
        await conn.execute(
            """
            UPDATE clip_cache
               SET pinned_to_workspace_id = ?
             WHERE provider_id = ? AND provider_clip_id = ?
            """,
            (ws_id, clip_key[0], clip_key[1]),
        )
        await conn.commit()
