"""ProxyCacheRepo — persists / reads `proxy_cache`; the registry of
on-disk proxy files. Called by RestProxyResolver and CacheInspector."""

from datetime import UTC, datetime
from typing import Any

import aiosqlite


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class ProxyCacheRepo:
    async def record(
        self,
        conn: aiosqlite.Connection,
        *,
        clip_id: int,
        file_path: str,
        size_bytes: int,
        etag: str | None,
        provider_id: str = "catdv",
        provider_clip_id: str | None = None,
    ) -> None:
        now = _now_iso()
        pcid = provider_clip_id if provider_clip_id is not None else str(clip_id)
        await conn.execute(
            """
            INSERT INTO proxy_cache
              (catdv_clip_id, provider_id, provider_clip_id,
               file_path, size_bytes, etag, downloaded_at, last_used_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(catdv_clip_id) DO UPDATE SET
              provider_id = excluded.provider_id,
              provider_clip_id = excluded.provider_clip_id,
              file_path = excluded.file_path,
              size_bytes = excluded.size_bytes,
              etag = excluded.etag,
              downloaded_at = excluded.downloaded_at,
              last_used_at = excluded.last_used_at
            """,
            (clip_id, provider_id, pcid, file_path, size_bytes, etag, now, now),
        )
        await conn.commit()

    async def get(self, conn: aiosqlite.Connection, clip_id: int) -> dict[str, Any] | None:
        cur = await conn.execute(
            """
            SELECT catdv_clip_id, provider_id, provider_clip_id,
                   file_path, size_bytes, etag, downloaded_at, last_used_at
            FROM proxy_cache WHERE catdv_clip_id = ?
            """,
            (clip_id,),
        )
        row = await cur.fetchone()
        if row is None:
            return None
        return dict(
            zip(
                (
                    "catdv_clip_id",
                    "provider_id",
                    "provider_clip_id",
                    "file_path",
                    "size_bytes",
                    "etag",
                    "downloaded_at",
                    "last_used_at",
                ),
                row,
                strict=True,
            )
        )

    async def touch(self, conn: aiosqlite.Connection, clip_id: int) -> None:
        await conn.execute(
            "UPDATE proxy_cache SET last_used_at = ? WHERE catdv_clip_id = ?",
            (_now_iso(), clip_id),
        )
        await conn.commit()

    async def total_size_bytes(self, conn: aiosqlite.Connection) -> int:
        cur = await conn.execute("SELECT COALESCE(SUM(size_bytes), 0) FROM proxy_cache")
        return int((await cur.fetchone())[0])

    async def lru_candidates(
        self, conn: aiosqlite.Connection, max_bytes: int
    ) -> list[dict[str, Any]]:
        """Return rows ordered oldest-first totalling at least max_bytes."""
        cur = await conn.execute(
            """
            SELECT catdv_clip_id, file_path, size_bytes, last_used_at
            FROM proxy_cache
            ORDER BY last_used_at ASC
            """
        )
        victims: list[dict[str, Any]] = []
        accum = 0
        for row in await cur.fetchall():
            victims.append(
                dict(
                    zip(
                        ("catdv_clip_id", "file_path", "size_bytes", "last_used_at"),
                        row,
                        strict=True,
                    )
                )
            )
            accum += row[2]
            if accum >= max_bytes:
                break
        return victims

    async def delete(self, conn: aiosqlite.Connection, clip_id: int) -> None:
        await conn.execute("DELETE FROM proxy_cache WHERE catdv_clip_id = ?", (clip_id,))
        await conn.commit()
