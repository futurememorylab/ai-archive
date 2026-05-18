from pathlib import Path

import pytest

from backend.app.db import open_db
from backend.app.migrations_runner import apply_migrations


@pytest.mark.asyncio
async def test_apply_migrations_creates_meta_table(tmp_path: Path):
    db_path = tmp_path / "test.db"
    migrations_dir = tmp_path / "migs"
    migrations_dir.mkdir()
    (migrations_dir / "0001_init.sql").write_text(
        "CREATE TABLE thing (id INTEGER PRIMARY KEY, name TEXT NOT NULL);"
    )

    async with open_db(db_path) as conn:
        await apply_migrations(conn, migrations_dir)

    async with open_db(db_path) as conn:
        cur = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        names = [row[0] for row in await cur.fetchall()]
    assert "schema_migrations" in names
    assert "thing" in names


@pytest.mark.asyncio
async def test_apply_migrations_is_idempotent(tmp_path: Path):
    db_path = tmp_path / "test.db"
    migrations_dir = tmp_path / "migs"
    migrations_dir.mkdir()
    (migrations_dir / "0001_init.sql").write_text(
        "CREATE TABLE thing (id INTEGER PRIMARY KEY);"
    )

    async with open_db(db_path) as conn:
        await apply_migrations(conn, migrations_dir)
        await apply_migrations(conn, migrations_dir)  # second run must not error
        cur = await conn.execute("SELECT count(*) FROM schema_migrations")
        n = (await cur.fetchone())[0]
    assert n == 1
