"""Audit log for cache evictions and LRU sweeps.

One row per CacheActions call (including skips), plus one per LRU
eviction. The `who` column is "system" for LRU and "request" for
user-driven routes; auth-aware identities can replace the latter when
introduced — the column is plain TEXT.

`clip_keys` is a JSON array of [provider_id, provider_clip_id] pairs.
Single-clip actions still serialise a one-element array so the shape is
stable across single / bulk calls.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import aiosqlite

from backend.app.archive.model import ClipKey


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


_ROW_COLS = (
    "id",
    "who",
    "action",
    "clip_keys",
    "result",
    "detail",
    "bytes_freed",
    "at",
)


class CacheActionsLogRepo:
    async def append(
        self,
        conn: aiosqlite.Connection,
        *,
        who: str,
        action: str,
        clip_keys: Sequence[ClipKey],
        result: str,
        detail: str | None = None,
        bytes_freed: int = 0,
        at: str | None = None,
    ) -> int:
        ts = at or _now_iso()
        payload = json.dumps([[k[0], k[1]] for k in clip_keys], ensure_ascii=False)
        cur = await conn.execute(
            """
            INSERT INTO cache_actions_log
              (who, action, clip_keys, result, detail, bytes_freed, at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (who, action, payload, result, detail, bytes_freed, ts),
        )
        await conn.commit()
        return cur.lastrowid

    async def list_recent(
        self, conn: aiosqlite.Connection, *, limit: int = 100
    ) -> list[dict[str, Any]]:
        cur = await conn.execute(
            f"SELECT {', '.join(_ROW_COLS)} FROM cache_actions_log ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        rows = await cur.fetchall()
        return [dict(zip(_ROW_COLS, r, strict=True)) for r in rows]
