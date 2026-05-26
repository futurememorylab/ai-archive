"""TestbenchesRepo — testbench + folder CRUD for Prompt Studio."""
from datetime import UTC, datetime

import aiosqlite

from backend.app.models.studio import Testbench, TestbenchFolder


def _now() -> str:
    return datetime.now(UTC).isoformat()


_TB_COLS = "id, name, description, archived, created_at, updated_at"
_F_COLS = "id, testbench_id, parent_id, name, sort_index, created_at"


def _tb(row) -> Testbench:
    return Testbench(
        id=row[0], name=row[1], description=row[2],
        archived=bool(row[3]), created_at=row[4], updated_at=row[5],
    )


def _f(row) -> TestbenchFolder:
    return TestbenchFolder(
        id=row[0], testbench_id=row[1], parent_id=row[2],
        name=row[3], sort_index=row[4], created_at=row[5],
    )


class TestbenchesRepo:
    async def create(
        self, conn: aiosqlite.Connection, *, name: str, description: str | None
    ) -> int:
        now = _now()
        cur = await conn.execute(
            "INSERT INTO testbenches (name, description, archived, created_at, updated_at) "
            "VALUES (?, ?, 0, ?, ?)",
            (name, description, now, now),
        )
        await conn.commit()
        assert cur.lastrowid is not None
        return cur.lastrowid

    async def get(self, conn: aiosqlite.Connection, id: int) -> Testbench:
        cur = await conn.execute(f"SELECT {_TB_COLS} FROM testbenches WHERE id=?", (id,))
        row = await cur.fetchone()
        if row is None:
            raise LookupError(f"testbench {id} not found")
        return _tb(row)

    async def list_active(self, conn: aiosqlite.Connection) -> list[Testbench]:
        cur = await conn.execute(
            f"SELECT {_TB_COLS} FROM testbenches WHERE archived=0 ORDER BY name"
        )
        return [_tb(r) for r in await cur.fetchall()]

    async def rename(self, conn: aiosqlite.Connection, id: int, name: str) -> None:
        await conn.execute(
            "UPDATE testbenches SET name=?, updated_at=? WHERE id=?",
            (name, _now(), id),
        )
        await conn.commit()

    async def archive(self, conn: aiosqlite.Connection, id: int) -> None:
        await conn.execute(
            "UPDATE testbenches SET archived=1, updated_at=? WHERE id=?",
            (_now(), id),
        )
        await conn.commit()

    async def list_folders(
        self, conn: aiosqlite.Connection, testbench_id: int
    ) -> list[TestbenchFolder]:
        cur = await conn.execute(
            f"SELECT {_F_COLS} FROM testbench_folders WHERE testbench_id=? "
            "ORDER BY sort_index, name",
            (testbench_id,),
        )
        return [_f(r) for r in await cur.fetchall()]

    async def create_folder(
        self, conn: aiosqlite.Connection,
        *, testbench_id: int, parent_id: int | None, name: str,
    ) -> int:
        cur = await conn.execute(
            "INSERT INTO testbench_folders (testbench_id, parent_id, name, sort_index, created_at) "
            "VALUES (?, ?, ?, 0, ?)",
            (testbench_id, parent_id, name, _now()),
        )
        await conn.commit()
        assert cur.lastrowid is not None
        return cur.lastrowid

    async def rename_folder(self, conn: aiosqlite.Connection, id: int, name: str) -> None:
        await conn.execute("UPDATE testbench_folders SET name=? WHERE id=?", (name, id))
        await conn.commit()

    async def delete_folder(self, conn: aiosqlite.Connection, id: int) -> None:
        cur = await conn.execute(
            "SELECT (SELECT COUNT(*) FROM testbench_folders WHERE parent_id=?) "
            "+ (SELECT COUNT(*) FROM testbench_items WHERE folder_id=?)",
            (id, id),
        )
        row = await cur.fetchone()
        assert row is not None
        if row[0]:
            raise ValueError("folder not empty; cannot delete")
        await conn.execute("DELETE FROM testbench_folders WHERE id=?", (id,))
        await conn.commit()
