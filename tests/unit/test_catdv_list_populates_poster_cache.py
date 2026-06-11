"""list_clips must write posterID data into poster_cache when the raw CatDV
payload carries a posterID. Items without a posterID must not be written."""

from pathlib import Path

import aiosqlite
import pytest

from backend.app.archive.providers.catdv.adapter import CatdvArchiveAdapter
from backend.app.archive.model import ClipQuery
from backend.app.migrations_runner import apply_migrations
from backend.app.repositories.poster_cache import PosterCacheRepo

MIGRATIONS = Path(__file__).resolve().parents[2] / "backend" / "migrations"


async def _make_db() -> aiosqlite.Connection:
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    await apply_migrations(conn, MIGRATIONS)
    return conn


class _FakeClient:
    """Minimal fake CatDV client that returns a canned list payload."""

    def __init__(self, payload: dict):
        self._payload = payload

    async def list_clips(self, catalog_id, *, offset=0, limit=50, q=None):
        return self._payload


def _make_adapter(db: aiosqlite.Connection, poster_cache_repo: PosterCacheRepo) -> CatdvArchiveAdapter:
    payload = {
        "items": [
            {"ID": 888700, "posterID": 882119, "name": "clip-a"},
            {"ID": 888709, "posterID": 882156, "name": "clip-b"},
            {"ID": 888711, "name": "clip-c"},            # no posterID → must not be written
        ],
        "totalItems": 3,
    }
    return CatdvArchiveAdapter(
        client=_FakeClient(payload),
        poster_cache_repo=poster_cache_repo,
        db_provider=lambda: db,
        is_online_provider=lambda: True,
    )


@pytest.mark.asyncio
async def test_list_clips_populates_poster_cache():
    db = await _make_db()
    repo = PosterCacheRepo()
    adapter = _make_adapter(db, repo)

    await adapter.list_clips("1", ClipQuery(offset=0, limit=50))

    assert await repo.get_poster_id(db, provider_id="catdv", provider_clip_id="888700") == 882119
    assert await repo.get_poster_id(db, provider_id="catdv", provider_clip_id="888709") == 882156
    # clip with no posterID in payload must NOT produce a row
    assert await repo.get_poster_id(db, provider_id="catdv", provider_clip_id="888711") is None

    await db.close()


@pytest.mark.asyncio
async def test_list_clips_no_poster_cache_repo_is_noop():
    """Adapter without a poster_cache_repo must still list clips fine."""
    db = await _make_db()
    payload = {
        "items": [{"ID": 888700, "posterID": 882119, "name": "clip-a"}],
        "totalItems": 1,
    }
    adapter = CatdvArchiveAdapter(
        client=_FakeClient(payload),
        db_provider=lambda: db,
        is_online_provider=lambda: True,
    )
    page = await adapter.list_clips("1", ClipQuery(offset=0, limit=50))
    assert page.total == 1

    await db.close()
