"""StudioSetsRepo — flat, single-source sets of clips for the studio.

A *set* is one level deep (no nesting) and belongs to exactly one `source`
('archive' | 'uploaded'). Set names are unique per source
(`UNIQUE(source, name)`). Removing a set cascades to its clip memberships
via ON DELETE CASCADE.
"""

from datetime import UTC, datetime
from typing import Any

import aiosqlite

DEFAULT_UPLOADED_SET_NAME = "Uploads"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class StudioSetsRepo:
    async def create_set(
        self, conn: aiosqlite.Connection, *, name: str, source: str = "archive"
    ) -> int:
        cur = await conn.execute(
            "INSERT INTO studio_set(name, source, created_at) VALUES (?, ?, ?)",
            (name, source, _now_iso()),
        )
        sid = cur.lastrowid
        assert sid is not None
        await conn.commit()
        return sid

    async def get_or_create_default_uploaded_set(
        self, conn: aiosqlite.Connection
    ) -> int:
        """Return the id of the well-known uploaded 'Uploads' set, creating
        it on first use. Lets a user drop a file before making a set."""
        cur = await conn.execute(
            "SELECT id FROM studio_set WHERE source='uploaded' AND name=? LIMIT 1",
            (DEFAULT_UPLOADED_SET_NAME,),
        )
        row = await cur.fetchone()
        if row is not None:
            return int(row[0])
        return await self.create_set(
            conn, name=DEFAULT_UPLOADED_SET_NAME, source="uploaded"
        )

    async def rename_set(
        self, conn: aiosqlite.Connection, set_id: int, *, name: str
    ) -> None:
        await conn.execute(
            "UPDATE studio_set SET name = ? WHERE id = ?", (name, set_id)
        )
        await conn.commit()

    async def delete_set(self, conn: aiosqlite.Connection, set_id: int) -> None:
        await conn.execute("DELETE FROM studio_set WHERE id = ?", (set_id,))
        await conn.commit()

    async def list_sets_with_counts(
        self, conn: aiosqlite.Connection, *, source: str = "archive"
    ) -> list[dict[str, Any]]:
        cur = await conn.execute(
            """
            SELECT s.id, s.name, s.source, s.created_at,
                   COALESCE(COUNT(sc.clip_id), 0) AS clip_count
            FROM studio_set s
            LEFT JOIN studio_set_clip sc ON sc.set_id = s.id
            WHERE s.source = ?
            GROUP BY s.id
            ORDER BY s.name
            """,
            (source,),
        )
        return [
            {
                "id": r[0],
                "name": r[1],
                "source": r[2],
                "created_at": r[3],
                "clip_count": r[4],
            }
            for r in await cur.fetchall()
        ]

    async def clip_total_for_source(
        self, conn: aiosqlite.Connection, *, source: str = "archive"
    ) -> int:
        cur = await conn.execute(
            """
            SELECT COUNT(*)
            FROM studio_set_clip sc
            JOIN studio_set s ON s.id = sc.set_id
            WHERE s.source = ?
            """,
            (source,),
        )
        return int((await cur.fetchone())[0])

    async def add_clips(
        self, conn: aiosqlite.Connection, set_id: int, *, clip_ids: list[int]
    ) -> int:
        """Add clip_ids to set. Returns count of newly added (dedupes)."""
        now = _now_iso()
        added = 0
        for cid in set(clip_ids):
            cur = await conn.execute(
                "INSERT OR IGNORE INTO studio_set_clip(set_id, clip_id, added_at) "
                "VALUES (?, ?, ?)",
                (set_id, cid, now),
            )
            if cur.rowcount:
                added += 1
        await conn.commit()
        return added

    async def remove_clip(
        self, conn: aiosqlite.Connection, set_id: int, *, clip_id: int
    ) -> None:
        await conn.execute(
            "DELETE FROM studio_set_clip WHERE set_id = ? AND clip_id = ?",
            (set_id, clip_id),
        )
        await conn.commit()

    async def count_sets_for_clip(
        self, conn: aiosqlite.Connection, clip_id: int
    ) -> int:
        """How many sets currently reference `clip_id`.

        Used by the upload orphan-GC: an uploaded clip is only safe to
        delete once it is referenced by zero sets.
        """
        cur = await conn.execute(
            "SELECT COUNT(*) FROM studio_set_clip WHERE clip_id = ?", (clip_id,)
        )
        return int((await cur.fetchone())[0])

    async def list_clips(
        self, conn: aiosqlite.Connection, set_id: int
    ) -> list[dict[str, Any]]:
        cur = await conn.execute(
            "SELECT clip_id, added_at FROM studio_set_clip "
            "WHERE set_id = ? ORDER BY added_at DESC",
            (set_id,),
        )
        return [{"clip_id": r[0], "added_at": r[1]} for r in await cur.fetchall()]

    async def source_for_set(
        self, conn: aiosqlite.Connection, set_id: int
    ) -> str | None:
        """The `source` ('archive' | 'uploaded') of a set, or None if missing."""
        cur = await conn.execute(
            "SELECT source FROM studio_set WHERE id = ?", (set_id,)
        )
        row = await cur.fetchone()
        return str(row[0]) if row is not None else None

    async def set_id_for_clip(
        self, conn: aiosqlite.Connection, clip_id: int
    ) -> int | None:
        """Lowest set_id containing `clip_id`, or None if not in any set.

        A clip can live in multiple sets; callers that need "the" set
        (e.g. studio page auto-expand) accept the deterministic-but-arbitrary
        pick.
        """
        cur = await conn.execute(
            "SELECT set_id FROM studio_set_clip "
            "WHERE clip_id = ? ORDER BY set_id LIMIT 1",
            (clip_id,),
        )
        row = await cur.fetchone()
        return int(row[0]) if row is not None else None
