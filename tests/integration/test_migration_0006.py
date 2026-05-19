from pathlib import Path

import pytest

from backend.app.db import open_db
from backend.app.migrations_runner import apply_migrations

MIGRATIONS = Path(__file__).resolve().parents[2] / "backend" / "migrations"


async def _columns(conn, table: str) -> dict[str, dict]:
    cur = await conn.execute(f"PRAGMA table_info({table})")
    rows = await cur.fetchall()
    # row: (cid, name, type, notnull, dflt_value, pk)
    return {r[1]: {"type": r[2], "notnull": r[3], "pk": r[5]} for r in rows}


@pytest.mark.asyncio
async def test_cache_actions_log_columns(tmp_path: Path):
    db = tmp_path / "t.db"
    async with open_db(db) as conn:
        await apply_migrations(conn, MIGRATIONS)
        cols = await _columns(conn, "cache_actions_log")
    assert set(cols) == {
        "id", "who", "action", "clip_keys", "result",
        "detail", "bytes_freed", "at",
    }
    assert cols["id"]["pk"] == 1
    assert cols["who"]["notnull"] == 1
    assert cols["action"]["notnull"] == 1
    assert cols["clip_keys"]["notnull"] == 1
    assert cols["result"]["notnull"] == 1
    assert cols["bytes_freed"]["notnull"] == 1
    assert cols["at"]["notnull"] == 1


@pytest.mark.asyncio
async def test_cache_actions_log_index_on_at(tmp_path: Path):
    db = tmp_path / "t.db"
    async with open_db(db) as conn:
        await apply_migrations(conn, MIGRATIONS)
        cur = await conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='index' AND tbl_name='cache_actions_log'"
        )
        names = {r[0] for r in await cur.fetchall()}
    assert "idx_cache_actions_log_at" in names


@pytest.mark.asyncio
async def test_cache_actions_log_insert_round_trip(tmp_path: Path):
    db = tmp_path / "t.db"
    async with open_db(db) as conn:
        await apply_migrations(conn, MIGRATIONS)
        await conn.execute(
            """
            INSERT INTO cache_actions_log
              (who, action, clip_keys, result, detail, bytes_freed, at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            ("system", "lru_evict", '[["catdv", "1"]]', "ok", None, 123, "2026-05-19"),
        )
        await conn.commit()
        cur = await conn.execute(
            "SELECT who, action, clip_keys, result, bytes_freed FROM cache_actions_log"
        )
        row = await cur.fetchone()
    assert row == ("system", "lru_evict", '[["catdv", "1"]]', "ok", 123)
