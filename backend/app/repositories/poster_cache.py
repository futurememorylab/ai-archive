"""PosterCacheRepo — persists / reads `poster_cache`, a lightweight
clip→posterID index populated when listing clips. The thumbnail path uses
it to fetch a listed clip's poster without a full get_clip (see ADR 0072)."""

from datetime import UTC, datetime

import aiosqlite


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class PosterCacheRepo:
    async def upsert_many(
        self,
        conn: aiosqlite.Connection,
        *,
        provider_id: str,
        entries: list[tuple[int, int]],
    ) -> None:
        """Upsert (clip_id, poster_id) pairs for one provider. No-op on []."""
        if not entries:
            return
        now = _now_iso()
        await conn.executemany(
            """
            INSERT INTO poster_cache
              (provider_id, provider_clip_id, poster_id, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(provider_id, provider_clip_id) DO UPDATE SET
              poster_id = excluded.poster_id,
              updated_at = excluded.updated_at
            """,
            [(provider_id, str(cid), int(pid), now) for cid, pid in entries],
        )
        await conn.commit()

    async def get_poster_id(
        self,
        conn: aiosqlite.Connection,
        *,
        provider_id: str,
        provider_clip_id: str,
    ) -> int | None:
        async with conn.execute(
            "SELECT poster_id FROM poster_cache "
            "WHERE provider_id = ? AND provider_clip_id = ?",
            (provider_id, provider_clip_id),
        ) as cur:
            row = await cur.fetchone()
        return int(row[0]) if row is not None else None
