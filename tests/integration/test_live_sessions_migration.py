from pathlib import Path

import aiosqlite
import pytest

from backend.app.migrations_runner import apply_migrations

MIGRATIONS = Path(__file__).resolve().parents[2] / "backend" / "migrations"


@pytest.mark.asyncio
async def test_live_sessions_table_exists_after_migrations(tmp_path):
    db = tmp_path / "t.db"
    async with aiosqlite.connect(db) as conn:
        await apply_migrations(conn, MIGRATIONS)
        cur = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='live_sessions'"
        )
        assert await cur.fetchone() is not None


@pytest.mark.asyncio
async def test_live_sessions_columns(tmp_path):
    db = tmp_path / "t.db"
    async with aiosqlite.connect(db) as conn:
        await apply_migrations(conn, MIGRATIONS)
        cur = await conn.execute("PRAGMA table_info(live_sessions)")
        cols = {row[1] for row in await cur.fetchall()}
    assert cols == {
        "id",
        "clip_id",
        "prompt_version",
        "state",
        "started_at",
        "ended_at",
        "end_reason",
        "transcript_json",
        "summary_cs",
        "frame_count",
        "created_at",
    }  # search_calls dropped in 0027 (ADR 0109)


@pytest.mark.asyncio
async def test_live_sessions_index_present(tmp_path):
    db = tmp_path / "t.db"
    async with aiosqlite.connect(db) as conn:
        await apply_migrations(conn, MIGRATIONS)
        cur = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='ix_live_sessions_clip'"
        )
        assert await cur.fetchone() is not None
