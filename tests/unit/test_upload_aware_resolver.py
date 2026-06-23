from pathlib import Path

import pytest

from backend.app.db import open_db
from backend.app.migrations_runner import apply_migrations
from backend.app.repositories.proxy_cache import ProxyCacheRepo
from backend.app.services.proxy_resolver import ProxyNotFound, UploadAwareResolver
from backend.app.uploaded_ids import to_clip_id


class _Inner:
    is_host_local = False

    def __init__(self):
        self.calls = []

    async def path_for_clip_id(self, clip_id: int, progress_cb=None) -> Path:
        self.calls.append(clip_id)
        return Path("/archive/served.mov")

    def is_managed(self, path: Path) -> bool:
        return True


@pytest.fixture
async def conn(tmp_path):
    cm = open_db(tmp_path / "t.db")
    c = await cm.__aenter__()
    await apply_migrations(c, Path("backend/migrations"))
    yield c
    await cm.__aexit__(None, None, None)


@pytest.mark.asyncio
async def test_archive_id_delegates_to_inner(conn):
    inner = _Inner()
    r = UploadAwareResolver(inner, repo=ProxyCacheRepo(), db_provider=lambda: conn)
    assert await r.path_for_clip_id(42) == Path("/archive/served.mov")
    assert inner.calls == [42]


@pytest.mark.asyncio
async def test_uploaded_hit_serves_local_file_without_inner(conn, tmp_path):
    f = tmp_path / "up.mp4"
    f.write_bytes(b"video-bytes")
    clip_id = to_clip_id(1)
    repo = ProxyCacheRepo()
    await repo.record(conn, clip_id=clip_id, file_path=str(f), size_bytes=11,
                      etag=None, provider_id="uploaded", provider_clip_id=str(clip_id))
    inner = _Inner()
    r = UploadAwareResolver(inner, repo=repo, db_provider=lambda: conn)
    assert await r.path_for_clip_id(clip_id) == f
    assert inner.calls == []  # never touched the CatDV path


@pytest.mark.asyncio
async def test_uploaded_miss_raises_without_inner(conn):
    inner = _Inner()
    r = UploadAwareResolver(inner, repo=ProxyCacheRepo(), db_provider=lambda: conn)
    with pytest.raises(ProxyNotFound):
        await r.path_for_clip_id(to_clip_id(999))
    assert inner.calls == []
