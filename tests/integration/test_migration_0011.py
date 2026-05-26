from pathlib import Path

import pytest

from backend.app.db import open_db
from backend.app.migrations_runner import apply_migrations

MIGRATIONS = Path(__file__).resolve().parents[2] / "backend" / "migrations"


async def _columns(conn, table: str) -> dict[str, dict]:
    cur = await conn.execute(f"PRAGMA table_info({table})")
    rows = await cur.fetchall()
    return {r[1]: {"type": r[2], "notnull": r[3], "dflt": r[4]} for r in rows}


@pytest.mark.asyncio
async def test_prompts_has_media_kind_column(tmp_path: Path):
    db = tmp_path / "t.db"
    async with open_db(db) as conn:
        await apply_migrations(conn, MIGRATIONS)
        cols = await _columns(conn, "prompts")
    assert "media_kind" in cols
    assert cols["media_kind"]["notnull"] == 1


@pytest.mark.asyncio
async def test_existing_prompts_backfilled_to_video(tmp_path: Path):
    db = tmp_path / "t.db"
    async with open_db(db) as conn:
        await apply_migrations(conn, MIGRATIONS)
        await conn.execute(
            "INSERT INTO prompts(name, description, archived, created_at, updated_at) "
            "VALUES ('p', NULL, 0, '2026-01-01', '2026-01-01')"
        )
        await conn.commit()
        cur = await conn.execute("SELECT media_kind FROM prompts WHERE name='p'")
        row = await cur.fetchone()
        assert row is not None
        assert row[0] == "any"


@pytest.mark.asyncio
async def test_media_kind_check_rejects_invalid(tmp_path: Path):
    import aiosqlite

    db = tmp_path / "t.db"
    async with open_db(db) as conn:
        await apply_migrations(conn, MIGRATIONS)
        with pytest.raises(aiosqlite.IntegrityError):
            await conn.execute(
                "INSERT INTO prompts(name, archived, media_kind, created_at, updated_at) "
                "VALUES ('bad', 0, 'audio', '2026-01-01', '2026-01-01')"
            )
            await conn.commit()
