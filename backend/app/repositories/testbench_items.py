"""TestbenchItemsRepo — CRUD + tree-ordered iteration for Studio testbench items."""
import json
from datetime import UTC, datetime

import aiosqlite

from backend.app.models.studio import TestbenchItem


def _now() -> str:
    return datetime.now(UTC).isoformat()


_COLS = (
    "id, folder_id, source_kind, upload_path, upload_orig_name, "
    "catdv_provider_clip_id, display_name, gold_json, sort_index, created_at"
)


def _item(row) -> TestbenchItem:
    return TestbenchItem(
        id=row[0], folder_id=row[1], source_kind=row[2],
        upload_path=row[3], upload_orig_name=row[4],
        catdv_provider_clip_id=row[5], display_name=row[6],
        gold_json=row[7], sort_index=row[8], created_at=row[9],
    )


class TestbenchItemsRepo:
    async def add_upload(
        self, conn: aiosqlite.Connection,
        *, folder_id: int, upload_path: str, original_name: str,
    ) -> int:
        cur = await conn.execute(
            "INSERT INTO testbench_items "
            "(folder_id, source_kind, upload_path, upload_orig_name, display_name, sort_index, created_at) "
            "VALUES (?, 'upload', ?, ?, ?, 0, ?)",
            (folder_id, upload_path, original_name, original_name, _now()),
        )
        await conn.commit()
        assert cur.lastrowid is not None
        return cur.lastrowid

    async def add_catdv(
        self, conn: aiosqlite.Connection,
        *, folder_id: int, provider_clip_id: str, name: str,
    ) -> int:
        cur = await conn.execute(
            "INSERT INTO testbench_items "
            "(folder_id, source_kind, catdv_provider_clip_id, display_name, sort_index, created_at) "
            "VALUES (?, 'catdv_clip', ?, ?, 0, ?)",
            (folder_id, provider_clip_id, name, _now()),
        )
        await conn.commit()
        assert cur.lastrowid is not None
        return cur.lastrowid

    async def list_for_folder(
        self, conn: aiosqlite.Connection, folder_id: int
    ) -> list[TestbenchItem]:
        cur = await conn.execute(
            f"SELECT {_COLS} FROM testbench_items WHERE folder_id=? ORDER BY sort_index, id",
            (folder_id,),
        )
        return [_item(r) for r in await cur.fetchall()]

    async def list_for_testbench(
        self, conn: aiosqlite.Connection, testbench_id: int
    ) -> list[TestbenchItem]:
        """DFS by folder tree, sort_index within folder. Deterministic order
        the run worker iterates in."""
        cur = await conn.execute(
            """
            WITH RECURSIVE tree(id, parent_id, depth, path) AS (
                SELECT id, parent_id, 0, printf('%010d', sort_index)
                FROM testbench_folders WHERE testbench_id=? AND parent_id IS NULL
                UNION ALL
                SELECT f.id, f.parent_id, t.depth+1, t.path || '/' || printf('%010d', f.sort_index)
                FROM testbench_folders f JOIN tree t ON f.parent_id = t.id
            )
            SELECT i.id, i.folder_id, i.source_kind, i.upload_path, i.upload_orig_name,
                   i.catdv_provider_clip_id, i.display_name, i.gold_json, i.sort_index, i.created_at
            FROM testbench_items i
            JOIN tree t ON i.folder_id = t.id
            ORDER BY t.path, i.sort_index, i.id
            """,
            (testbench_id,),
        )
        return [_item(r) for r in await cur.fetchall()]

    async def set_gold(
        self, conn: aiosqlite.Connection, id: int, gold: dict | None
    ) -> None:
        payload = json.dumps(gold, ensure_ascii=False) if gold else None
        await conn.execute("UPDATE testbench_items SET gold_json=? WHERE id=?", (payload, id))
        await conn.commit()

    async def remove(self, conn: aiosqlite.Connection, id: int) -> None:
        await conn.execute("DELETE FROM testbench_items WHERE id=?", (id,))
        await conn.commit()
