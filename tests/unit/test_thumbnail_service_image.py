import io
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from backend.app.services.thumbnail_service import ThumbnailService


class _FakeArchive:
    def __init__(self, provider_data: dict):
        self._pd = provider_data

    async def get_clip(self, clip_id: str):
        return SimpleNamespace(provider_data=self._pd)


def _make_jpeg_bytes(w: int, h: int) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (200, 30, 30)).save(buf, format="JPEG")
    return buf.getvalue()


class _OriginalCatdv:
    def __init__(self, blob: bytes):
        self._blob = blob
        self.original_calls: list[int] = []

    async def download_original(self, media_id: int, dest: Path) -> None:
        self.original_calls.append(media_id)
        Path(dest).write_bytes(self._blob)

    async def download_thumbnail(self, thumb_id, dest, **kw):  # unused here
        raise AssertionError("should not fetch a CatDV thumbnail for a still")


IMAGE_PD = {
    "posterID": None,
    "thumbnailIDs": [],
    "media": {"ID": 881519, "filePath": "/Volumes/ARECA/x/Anna 101.JPG"},
}


@pytest.mark.asyncio
async def test_builds_downscaled_poster_for_still(tmp_path: Path):
    catdv = _OriginalCatdv(_make_jpeg_bytes(1000, 800))
    svc = ThumbnailService(cache_dir=tmp_path, archive=_FakeArchive(IMAGE_PD), catdv=catdv)
    out = await svc.get_or_fetch(888745)
    assert out == tmp_path / "888745.jpg"
    assert catdv.original_calls == [881519]
    with Image.open(out) as im:
        assert max(im.size) <= 480
    assert not (tmp_path / "888745.jpg.orig").exists()


@pytest.mark.asyncio
async def test_undecodable_original_returns_none(tmp_path: Path):
    catdv = _OriginalCatdv(b"this is not an image")
    svc = ThumbnailService(cache_dir=tmp_path, archive=_FakeArchive(IMAGE_PD), catdv=catdv)
    out = await svc.get_or_fetch(888745)
    assert out is None
    assert not (tmp_path / "888745.jpg").exists()


@pytest.mark.asyncio
async def test_non_image_with_no_poster_returns_none(tmp_path: Path):
    catdv = _OriginalCatdv(_make_jpeg_bytes(10, 10))
    pd = {"media": {"ID": 1, "filePath": "/Volumes/ARECA/x/clip.mov"}}
    svc = ThumbnailService(cache_dir=tmp_path, archive=_FakeArchive(pd), catdv=catdv)
    out = await svc.get_or_fetch(123)
    assert out is None
    assert catdv.original_calls == []
