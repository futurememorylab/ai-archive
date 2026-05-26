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
async def test_new_prompt_defaults_to_any(tmp_path: Path):
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
async def test_backfill_sets_existing_prompts_to_video(tmp_path: Path):
    from backend.app.migrations_runner import META_TABLE_SQL

    db = tmp_path / "t.db"
    async with open_db(db) as conn:
        # Pre-mark 0011 as applied so the first pass runs only 0001..0010.
        await conn.executescript(META_TABLE_SQL)
        await conn.execute(
            "INSERT INTO schema_migrations(name) VALUES ('0011_prompt_media_kind.sql')"
        )
        await conn.commit()
        await apply_migrations(conn, MIGRATIONS)  # applies 0001..0010 only

        # A prompt that existed before 0011 (schema has no media_kind column yet).
        await conn.execute(
            "INSERT INTO prompts(name, description, archived, created_at, updated_at) "
            "VALUES ('legacy', NULL, 0, '2026-01-01', '2026-01-01')"
        )
        await conn.commit()

        # Now run 0011's SQL (it was pre-marked, so apply it directly).
        sql = (MIGRATIONS / "0011_prompt_media_kind.sql").read_text()
        await conn.executescript(sql)
        await conn.commit()

        cur = await conn.execute("SELECT media_kind FROM prompts WHERE name = 'legacy'")
        assert (await cur.fetchone())[0] == "video"


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
