from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.app.repositories.proxy_cache import ProxyCacheRepo
from backend.app.services.proxy_resolver import RestProxyResolver


class _FakeArchive:
    def __init__(self, provider_data: dict):
        self._pd = provider_data

    async def get_clip(self, clip_id: str):
        return SimpleNamespace(provider_data=self._pd)


class _FakeCatdv:
    def __init__(self):
        self.proxy_calls: list[int] = []
        self.original_calls: list[int] = []

    async def download_proxy(self, clip_id: int, dest: Path, *, progress_cb=None) -> None:
        self.proxy_calls.append(clip_id)
        dest.write_bytes(b"PROXY-OK!")  # noqa: ASYNC240

    async def download_original(self, media_id: int, dest: Path, *, progress_cb=None) -> None:
        self.original_calls.append(media_id)
        dest.write_bytes(b"\xff\xd8\xffJPEG-ORIGINAL")  # noqa: ASYNC240


IMAGE_PD = {"media": {"ID": 881519, "filePath": "/Volumes/ARECA/x/Anna 101.JPG"}}
VIDEO_PD = {"media": {"ID": 770000, "filePath": "/Volumes/ARECA/x/Bogdan 1.mov"}}


@pytest.mark.asyncio
async def test_image_clip_downloads_original_as_jpg(db, tmp_path):
    repo = ProxyCacheRepo()
    catdv = _FakeCatdv()
    resolver = RestProxyResolver(
        catdv=catdv,
        cache_dir=tmp_path / "cache",
        proxy_cache_repo=repo,
        db_provider=lambda: db,
        archive=_FakeArchive(IMAGE_PD),
    )
    path = await resolver.path_for_clip_id(888745)

    assert path.name == "888745.jpg"
    assert path.read_bytes() == b"\xff\xd8\xffJPEG-ORIGINAL"
    assert catdv.original_calls == [881519]
    assert catdv.proxy_calls == []
    row = await repo.get(db, 888745)
    assert row is not None
    assert row["file_path"] == str(path)


@pytest.mark.asyncio
async def test_image_clip_cache_hit_skips_get_clip(db, tmp_path):
    repo = ProxyCacheRepo()
    catdv = _FakeCatdv()
    resolver = RestProxyResolver(
        catdv=catdv,
        cache_dir=tmp_path / "cache",
        proxy_cache_repo=repo,
        db_provider=lambda: db,
        archive=_FakeArchive(IMAGE_PD),
    )
    await resolver.path_for_clip_id(888745)
    await resolver.path_for_clip_id(888745)
    assert catdv.original_calls == [881519]  # second call is a cache hit


@pytest.mark.asyncio
async def test_video_clip_still_uses_proxy_mov(db, tmp_path):
    repo = ProxyCacheRepo()
    catdv = _FakeCatdv()
    resolver = RestProxyResolver(
        catdv=catdv,
        cache_dir=tmp_path / "cache",
        proxy_cache_repo=repo,
        db_provider=lambda: db,
        archive=_FakeArchive(VIDEO_PD),
    )
    path = await resolver.path_for_clip_id(888894)
    assert path.name == "888894.mov"
    assert path.read_bytes() == b"PROXY-OK!"
    assert catdv.proxy_calls == [888894]
    assert catdv.original_calls == []
