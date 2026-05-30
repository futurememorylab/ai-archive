"""list_orphans(deep=True) must NOT treat transient provider errors as
evidence a clip is gone. Doing so means a VPN flap could mark hundreds
of legitimately-cached clips as orphans, and the next 'Evict orphans'
action would wipe the data."""

from pathlib import Path

import httpx
import pytest

from backend.app.archive.errors import NotFoundError
from backend.app.db import open_db
from backend.app.migrations_runner import apply_migrations
from backend.app.services.cache_inspector import CacheInspector

MIGRATIONS = Path(__file__).resolve().parents[2] / "backend" / "migrations"


class _RaisingProvider:
    """Stub provider that raises whatever exception was configured."""

    def __init__(self, exc: BaseException):
        self._exc = exc

    async def get_clip(self, pcid: str):
        raise self._exc


async def _seed_one_clip(conn):
    await conn.execute(
        "INSERT INTO clip_cache"
        "(provider_id, provider_clip_id, name, catalog_id, duration_secs, fps, canonical_json, fetched_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))",
        ("catdv", "42", "clip 42", "1", 1.0, 25.0, '{"id": 42}'),
    )
    await conn.commit()


@pytest.mark.asyncio
async def test_deep_orphan_check_does_not_orphan_on_transient_error(tmp_path):
    db_path = tmp_path / "test.db"
    async with open_db(db_path) as conn:
        await apply_migrations(conn, MIGRATIONS)
        await _seed_one_clip(conn)

        # Provider raises a transport-style error (NOT a NotFoundError).
        request = httpx.Request("GET", "http://example/x")
        response = httpx.Response(500, request=request)
        provider = _RaisingProvider(
            httpx.HTTPStatusError("flaky", request=request, response=response)
        )

        inspector = CacheInspector(
            db_provider=lambda: conn,
            provider=provider,
        )
        orphans = await inspector.list_orphans(deep=True)
        assert orphans == [], (
            f"clip 42 should NOT be orphaned by a transient 500; got {orphans}"
        )


@pytest.mark.asyncio
async def test_deep_orphan_check_orphans_on_not_found(tmp_path):
    db_path = tmp_path / "test.db"
    async with open_db(db_path) as conn:
        await apply_migrations(conn, MIGRATIONS)
        await _seed_one_clip(conn)

        provider = _RaisingProvider(NotFoundError("clip 42 absent upstream"))

        inspector = CacheInspector(
            db_provider=lambda: conn,
            provider=provider,
        )
        orphans = await inspector.list_orphans(deep=True)
        assert len(orphans) == 1
        assert orphans[0].clip_key == ("catdv", "42")
