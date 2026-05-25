from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.app.services.thumbnail_service import ThumbnailService


class _FakeArchive:
    def __init__(self, provider_data: dict):
        self._provider_data = provider_data

    async def get_clip(self, clip: str):
        return SimpleNamespace(provider_data=self._provider_data)


class _FakeCatdv:
    def __init__(self):
        self.calls: list[int] = []
        self.proxy_calls: list[int] = []

    async def download_thumbnail(self, thumb_id, dest, **kw):
        self.calls.append(thumb_id)
        Path(dest).write_bytes(b"\xff\xd8\xffJPEG")

    async def download_proxy(self, clip_id, dest, **kw):
        self.proxy_calls.append(clip_id)
        Path(dest).write_bytes(b"\xff\xd8\xffPROXYJPEG")


@pytest.mark.asyncio
async def test_cache_hit_skips_fetch(tmp_path: Path):
    (tmp_path / "42.jpg").write_bytes(b"cached")
    catdv = _FakeCatdv()
    svc = ThumbnailService(
        cache_dir=tmp_path, archive=_FakeArchive({"posterID": 9000}), catdv=catdv
    )
    out = await svc.get_or_fetch(42)
    assert out == tmp_path / "42.jpg"
    assert catdv.calls == []  # no fetch on hit


@pytest.mark.asyncio
async def test_online_miss_fetches_posterid(tmp_path: Path):
    catdv = _FakeCatdv()
    svc = ThumbnailService(
        cache_dir=tmp_path, archive=_FakeArchive({"posterID": 9000}), catdv=catdv
    )
    out = await svc.get_or_fetch(42)
    assert out == tmp_path / "42.jpg"
    assert out.read_bytes() == b"\xff\xd8\xffJPEG"
    assert catdv.calls == [9000]


@pytest.mark.asyncio
async def test_falls_back_to_thumbnail_ids(tmp_path: Path):
    catdv = _FakeCatdv()
    svc = ThumbnailService(
        cache_dir=tmp_path, archive=_FakeArchive({"thumbnailIDs": [5001, 5002]}), catdv=catdv
    )
    await svc.get_or_fetch(42)
    assert catdv.calls == [5001]


@pytest.mark.asyncio
async def test_no_poster_no_media_returns_none(tmp_path: Path):
    catdv = _FakeCatdv()
    svc = ThumbnailService(cache_dir=tmp_path, archive=_FakeArchive({}), catdv=catdv)
    assert await svc.get_or_fetch(42) is None
    assert catdv.calls == []
    assert catdv.proxy_calls == []


@pytest.mark.asyncio
async def test_image_media_fallback_when_no_poster(tmp_path: Path):
    # Still-image clips carry no posterID/thumbnailIDs but their media file
    # is itself a web image — serve that as the thumbnail.
    catdv = _FakeCatdv()
    pd = {"posterID": None, "thumbnailIDs": [], "media": {"filePath": "/vol/Anna 101.JPG"}}
    svc = ThumbnailService(cache_dir=tmp_path, archive=_FakeArchive(pd), catdv=catdv)
    out = await svc.get_or_fetch(42)
    assert out == tmp_path / "42.jpg"
    assert out.read_bytes() == b"\xff\xd8\xffPROXYJPEG"
    assert catdv.calls == []  # no poster fetch attempted
    assert catdv.proxy_calls == [42]  # fell back to the media file


@pytest.mark.asyncio
async def test_no_poster_non_image_media_returns_none(tmp_path: Path):
    # A video with no poster must NOT trigger a full media download.
    catdv = _FakeCatdv()
    pd = {"media": {"filePath": "/vol/clip.mov"}}
    svc = ThumbnailService(cache_dir=tmp_path, archive=_FakeArchive(pd), catdv=catdv)
    assert await svc.get_or_fetch(42) is None
    assert catdv.proxy_calls == []


@pytest.mark.asyncio
async def test_offline_no_client_returns_none(tmp_path: Path):
    svc = ThumbnailService(cache_dir=tmp_path, archive=_FakeArchive({"posterID": 9000}), catdv=None)
    assert await svc.get_or_fetch(42) is None


class _FailingCatdv:
    async def download_thumbnail(self, thumb_id, dest, **kw):
        Path(dest).write_bytes(b"")
        raise RuntimeError("boom")


class _EmptyBodyCatdv:
    async def download_thumbnail(self, thumb_id, dest, **kw):
        Path(dest).write_bytes(b"")


@pytest.mark.asyncio
async def test_download_failure_unlinks_zero_byte_file(tmp_path: Path):
    svc = ThumbnailService(
        cache_dir=tmp_path, archive=_FakeArchive({"posterID": 9000}), catdv=_FailingCatdv()
    )
    assert await svc.get_or_fetch(42) is None
    assert not svc.path_for(42).exists()


@pytest.mark.asyncio
async def test_empty_body_returns_none(tmp_path: Path):
    svc = ThumbnailService(
        cache_dir=tmp_path, archive=_FakeArchive({"posterID": 9000}), catdv=_EmptyBodyCatdv()
    )
    assert await svc.get_or_fetch(42) is None
