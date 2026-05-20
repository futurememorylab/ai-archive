from pathlib import Path

import pytest

from backend.app.repositories.proxy_cache import ProxyCacheRepo
from backend.app.services.proxy_resolver import RestProxyResolver


class _FakeCatdv:
    """Minimal CatDV client stub: writes a 9-byte file."""
    def __init__(self):
        self.calls = []

    async def download_proxy(self, clip_id: int, dest: Path) -> None:
        self.calls.append((clip_id, dest))
        dest.write_bytes(b"PROXY-OK!")  # noqa: ASYNC240


@pytest.mark.asyncio
async def test_resolver_records_after_download(db, tmp_path):
    repo = ProxyCacheRepo()
    catdv = _FakeCatdv()
    resolver = RestProxyResolver(
        catdv=catdv,
        cache_dir=tmp_path / "cache",
        proxy_cache_repo=repo,
        db_provider=lambda: db,
    )
    path = await resolver.path_for_clip_id(1234)

    assert path.exists() and path.read_bytes() == b"PROXY-OK!"
    row = await repo.get(db, 1234)
    assert row is not None
    assert row["size_bytes"] == 9
    assert row["file_path"] == str(path)


@pytest.mark.asyncio
async def test_resolver_does_not_redownload_or_redouble_record(db, tmp_path):
    repo = ProxyCacheRepo()
    catdv = _FakeCatdv()
    resolver = RestProxyResolver(
        catdv=catdv,
        cache_dir=tmp_path / "cache",
        proxy_cache_repo=repo,
        db_provider=lambda: db,
    )
    await resolver.path_for_clip_id(1234)
    await resolver.path_for_clip_id(1234)
    assert len(catdv.calls) == 1   # cache hit on second call
