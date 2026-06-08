import pytest

from backend.app.services.thumbnail_service import ThumbnailService
from backend.app.uploaded_ids import to_clip_id


class _ExplodingArchive:
    async def get_clip(self, clip_id):
        raise AssertionError("archive.get_clip must NOT be called for uploaded clips")


class _ExplodingCatdv:
    async def download_thumbnail(self, *a, **k):
        raise AssertionError("download_thumbnail must NOT be called for uploaded clips")


@pytest.mark.asyncio
async def test_uploaded_hit_returns_poster(tmp_path):
    svc = ThumbnailService(cache_dir=tmp_path, archive=_ExplodingArchive(),
                           catdv=_ExplodingCatdv(), is_online_provider=lambda: True)
    cid = to_clip_id(1)
    poster = svc.path_for(cid)
    poster.write_bytes(b"jpeg")
    assert await svc.get_or_fetch(cid) == poster


@pytest.mark.asyncio
async def test_uploaded_miss_returns_none_without_network(tmp_path):
    svc = ThumbnailService(cache_dir=tmp_path, archive=_ExplodingArchive(),
                           catdv=_ExplodingCatdv(), is_online_provider=lambda: True)
    assert await svc.get_or_fetch(to_clip_id(2)) is None
