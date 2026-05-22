from pathlib import Path

import pytest

from backend.app.repositories.proxy_cache import ProxyCacheRepo
from backend.app.services.proxy_resolver import (
    LocalCacheOnlyResolver,
    ProxyNotFound,
    build_resolver,
)


@pytest.mark.asyncio
async def test_returns_path_when_file_on_disk(db, tmp_path):
    repo = ProxyCacheRepo()
    f = tmp_path / "42.mov"
    f.write_bytes(b"x")
    await repo.record(
        db,
        clip_id=42,
        file_path=str(f),
        size_bytes=1,
        etag=None,
        provider_id="catdv",
        provider_clip_id="42",
    )
    r = LocalCacheOnlyResolver(repo=repo, db_provider=lambda: db)
    assert await r.path_for_clip_id(42) == f


@pytest.mark.asyncio
async def test_raises_when_file_missing(db, tmp_path):
    repo = ProxyCacheRepo()
    f = tmp_path / "ghost.mov"
    await repo.record(
        db,
        clip_id=99,
        file_path=str(f),
        size_bytes=0,
        etag=None,
        provider_id="catdv",
        provider_clip_id="99",
    )
    r = LocalCacheOnlyResolver(repo=repo, db_provider=lambda: db)
    with pytest.raises(ProxyNotFound):
        await r.path_for_clip_id(99)


@pytest.mark.asyncio
async def test_raises_when_no_db_row(db):
    repo = ProxyCacheRepo()
    r = LocalCacheOnlyResolver(repo=repo, db_provider=lambda: db)
    with pytest.raises(ProxyNotFound):
        await r.path_for_clip_id(1234)


def test_build_resolver_returns_local_cache_only_for_cache_source():
    r = build_resolver(
        source="cache-only",
        catdv_client=None,
        cache_dir=None,
        proxy_cache_repo=ProxyCacheRepo(),
        db_provider=lambda: None,
    )
    assert isinstance(r, LocalCacheOnlyResolver)


def test_is_managed_returns_true_when_in_cache_dir(tmp_path):
    repo = ProxyCacheRepo()
    r = LocalCacheOnlyResolver(repo=repo, db_provider=lambda: None, cache_dir=tmp_path)
    inside = tmp_path / "1.mov"
    inside.write_bytes(b"")
    assert r.is_managed(inside) is True
    assert r.is_managed(Path("/elsewhere/2.mov")) is False
