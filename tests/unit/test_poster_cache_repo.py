from pathlib import Path

import aiosqlite
import pytest

from backend.app.migrations_runner import apply_migrations
from backend.app.repositories.poster_cache import PosterCacheRepo

MIGRATIONS = Path(__file__).resolve().parents[2] / "backend" / "migrations"


async def _db() -> aiosqlite.Connection:
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    await apply_migrations(conn, MIGRATIONS)
    return conn


@pytest.mark.asyncio
async def test_upsert_many_then_get():
    conn = await _db()
    repo = PosterCacheRepo()
    await repo.upsert_many(conn, provider_id="catdv", entries=[(888700, 882119), (888709, 882156)])
    assert await repo.get_poster_id(conn, provider_id="catdv", provider_clip_id="888709") == 882156
    assert await repo.get_poster_id(conn, provider_id="catdv", provider_clip_id="888700") == 882119
    await conn.close()


@pytest.mark.asyncio
async def test_get_missing_returns_none():
    conn = await _db()
    repo = PosterCacheRepo()
    assert await repo.get_poster_id(conn, provider_id="catdv", provider_clip_id="999") is None
    await conn.close()


@pytest.mark.asyncio
async def test_upsert_overwrites():
    conn = await _db()
    repo = PosterCacheRepo()
    await repo.upsert_many(conn, provider_id="catdv", entries=[(888700, 1)])
    await repo.upsert_many(conn, provider_id="catdv", entries=[(888700, 2)])
    assert await repo.get_poster_id(conn, provider_id="catdv", provider_clip_id="888700") == 2
    await conn.close()


@pytest.mark.asyncio
async def test_upsert_many_empty_is_noop():
    conn = await _db()
    repo = PosterCacheRepo()
    await repo.upsert_many(conn, provider_id="catdv", entries=[])  # must not error
    await conn.close()
