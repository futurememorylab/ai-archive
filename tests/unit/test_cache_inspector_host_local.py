from pathlib import Path

import pytest
import pytest_asyncio

from backend.app.archive.model import ClipKey
from backend.app.db import open_db
from backend.app.migrations_runner import apply_migrations
from backend.app.services.cache_inspector import CacheInspector

MIGRATIONS = Path(__file__).resolve().parents[2] / "backend" / "migrations"


@pytest_asyncio.fixture
async def memdb(tmp_path: Path):
    db_path = tmp_path / "test.db"
    async with open_db(db_path) as conn:
        await apply_migrations(conn, MIGRATIONS)
        yield conn


@pytest.mark.asyncio
async def test_media_local_layer_synthetic_when_host_local(memdb):
    """In host-local mode, every clip reports media-local as present + non-evictable
    without any proxy_cache row."""
    inspector = CacheInspector(
        db_provider=lambda: memdb,
        media_cache_cap_bytes=0,
        provider=None,
        host_local_proxies=True,
    )
    rows = await inspector.status_for_clips([ClipKey(("catdv", "42"))])
    layer = next(layer for layer in rows[0].layers if layer.layer == "media-local")
    assert layer.present is True
    assert layer.evictable is False
    assert layer.location == "host:filesystem"


@pytest.mark.asyncio
async def test_media_local_layer_normal_when_not_host_local(memdb):
    """In rest mode, an absent proxy_cache row means present=False (existing behaviour)."""
    inspector = CacheInspector(
        db_provider=lambda: memdb,
        media_cache_cap_bytes=0,
        provider=None,
        host_local_proxies=False,
    )
    rows = await inspector.status_for_clips([ClipKey(("catdv", "42"))])
    layer = next(layer for layer in rows[0].layers if layer.layer == "media-local")
    assert layer.present is False
    assert layer.evictable is False
