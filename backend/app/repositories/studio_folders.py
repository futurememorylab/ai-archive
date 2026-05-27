"""StudioFoldersRepo — flat folders of archive-picked clips for the studio.

No nested folders, no per-prompt scoping. Folder names are globally unique
(enforced by `studio_folder.name UNIQUE`). Removing a folder cascades to
its clip memberships via ON DELETE CASCADE.
"""

from datetime import UTC, datetime
from typing import Any

import aiosqlite


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class StudioFoldersRepo:
    async def create_folder(self, conn: aiosqlite.Connection, *, name: str) -> int:
        cur = await conn.execute(
            "INSERT INTO studio_folder(name, created_at) VALUES (?, ?)",
            (name, _now_iso()),
        )
        fid = cur.lastrowid
        assert fid is not None
        await conn.commit()
        return fid

    async def rename_folder(
        self, conn: aiosqlite.Connection, folder_id: int, *, name: str
    ) -> None:
        await conn.execute(
            "UPDATE studio_folder SET name = ? WHERE id = ?", (name, folder_id)
        )
        await conn.commit()

    async def delete_folder(self, conn: aiosqlite.Connection, folder_id: int) -> None:
        await conn.execute("DELETE FROM studio_folder WHERE id = ?", (folder_id,))
        await conn.commit()

    async def list_folders_with_counts(
        self, conn: aiosqlite.Connection
    ) -> list[dict[str, Any]]:
        cur = await conn.execute(
            """
            SELECT f.id, f.name, f.created_at,
                   COALESCE(COUNT(fc.clip_id), 0) AS clip_count
            FROM studio_folder f
            LEFT JOIN studio_folder_clip fc ON fc.folder_id = f.id
            GROUP BY f.id
            ORDER BY f.name
            """
        )
        return [
            {"id": r[0], "name": r[1], "created_at": r[2], "clip_count": r[3]}
            for r in await cur.fetchall()
        ]

    async def add_clips(
        self, conn: aiosqlite.Connection, folder_id: int, *, clip_ids: list[int]
    ) -> int:
        """Add clip_ids to folder. Returns count of newly added (dedupes)."""
        now = _now_iso()
        added = 0
        for cid in set(clip_ids):
            cur = await conn.execute(
                "INSERT OR IGNORE INTO studio_folder_clip(folder_id, clip_id, added_at) "
                "VALUES (?, ?, ?)",
                (folder_id, cid, now),
            )
            if cur.rowcount:
                added += 1
        await conn.commit()
        return added

    async def remove_clip(
        self, conn: aiosqlite.Connection, folder_id: int, *, clip_id: int
    ) -> None:
        await conn.execute(
            "DELETE FROM studio_folder_clip WHERE folder_id = ? AND clip_id = ?",
            (folder_id, clip_id),
        )
        await conn.commit()

    async def list_clips(
        self, conn: aiosqlite.Connection, folder_id: int
    ) -> list[dict[str, Any]]:
        cur = await conn.execute(
            "SELECT clip_id, added_at FROM studio_folder_clip "
            "WHERE folder_id = ? ORDER BY added_at DESC",
            (folder_id,),
        )
        return [{"clip_id": r[0], "added_at": r[1]} for r in await cur.fetchall()]

    async def folder_id_for_clip(
        self, conn: aiosqlite.Connection, clip_id: int
    ) -> int | None:
        """Lowest folder_id containing `clip_id`, or None if not in any folder.

        A clip can live in multiple folders; callers that need "the" folder
        (e.g. studio page auto-expand) accept the deterministic-but-arbitrary
        pick.
        """
        cur = await conn.execute(
            "SELECT folder_id FROM studio_folder_clip "
            "WHERE clip_id = ? ORDER BY folder_id LIMIT 1",
            (clip_id,),
        )
        row = await cur.fetchone()
        return int(row[0]) if row is not None else None
