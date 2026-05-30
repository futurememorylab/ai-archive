"""apply_migrations refuses to apply a .sql file whose numeric prefix
collides with a .txt sentinel — this catches the future-PR-claiming-
0011 case. Sentinels document deliberately-reserved numbers."""

from pathlib import Path

import pytest

from backend.app.db import open_db
from backend.app.migrations_runner import apply_migrations


@pytest.mark.asyncio
async def test_collision_with_sentinel_raises(tmp_path: Path):
    db_path = tmp_path / "test.db"
    migs = tmp_path / "migs"
    migs.mkdir()
    (migs / "0001_init.sql").write_text("CREATE TABLE a (id INTEGER PRIMARY KEY);")
    (migs / "0011_REVERTED.txt").write_text(
        "0011 was reverted; do not reuse this number."
    )
    (migs / "0011_thing.sql").write_text("CREATE TABLE b (id INTEGER PRIMARY KEY);")

    async with open_db(db_path) as conn:
        with pytest.raises(RuntimeError, match="0011"):
            await apply_migrations(conn, migs)


@pytest.mark.asyncio
async def test_sentinel_without_collision_is_ignored(tmp_path: Path):
    db_path = tmp_path / "test.db"
    migs = tmp_path / "migs"
    migs.mkdir()
    (migs / "0001_init.sql").write_text("CREATE TABLE a (id INTEGER PRIMARY KEY);")
    (migs / "0011_REVERTED.txt").write_text("reserved")
    (migs / "0012_thing.sql").write_text("CREATE TABLE b (id INTEGER PRIMARY KEY);")

    async with open_db(db_path) as conn:
        applied = await apply_migrations(conn, migs)
        assert applied == ["0001_init.sql", "0012_thing.sql"]
