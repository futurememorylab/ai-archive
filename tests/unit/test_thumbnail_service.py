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

    async def download_thumbnail(self, thumb_id, dest, **kw):
        self.calls.append(thumb_id)
        Path(dest).write_bytes(b"\xff\xd8\xffJPEG")


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
async def test_no_poster_returns_none(tmp_path: Path):
    catdv = _FakeCatdv()
    svc = ThumbnailService(cache_dir=tmp_path, archive=_FakeArchive({}), catdv=catdv)
    assert await svc.get_or_fetch(42) is None
    assert catdv.calls == []


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


@pytest.mark.asyncio
async def test_offline_provider_blocks_network_fetch(tmp_path: Path):
    """When the connection monitor reports offline, cache miss returns
    None without attempting any network fetch — the client stays alive
    for retry but the service doesn't poke it."""
    catdv = _FakeCatdv()
    svc = ThumbnailService(
        cache_dir=tmp_path,
        archive=_FakeArchive({"posterID": 9000}),
        catdv=catdv,
        is_online_provider=lambda: False,
    )
    assert await svc.get_or_fetch(42) is None
    assert catdv.calls == []  # no fetch attempted


@pytest.mark.asyncio
async def test_offline_provider_still_serves_cached_hit(tmp_path: Path):
    """Cache hits are served regardless of online state — the gate
    applies only to network fetches."""
    (tmp_path / "42.jpg").write_bytes(b"cached")
    catdv = _FakeCatdv()
    svc = ThumbnailService(
        cache_dir=tmp_path,
        archive=_FakeArchive({"posterID": 9000}),
        catdv=catdv,
        is_online_provider=lambda: False,
    )
    assert await svc.get_or_fetch(42) == tmp_path / "42.jpg"
    assert catdv.calls == []


class _RecordingArchive:
    """Tracks whether get_clip was called — never returns a value."""

    def __init__(self):
        self.calls: list[str] = []

    async def get_clip(self, clip: str):  # pragma: no cover — should not be called
        self.calls.append(clip)
        raise AssertionError("get_clip should not be called for unmetadata'd clips")


@pytest.mark.asyncio
async def test_no_cached_metadata_skips_network(tmp_path: Path):
    """When metadata_cached_provider returns False, the service must not
    touch the archive or the CatDV client — there is no way to know the
    posterID without metadata, so the network call would be pure waste
    and (if CatDV is slow / unresponsive) a per-row 60 s stall.

    This is the orphan case on the /cache page: clips with bytes in
    proxy_cache or ai_store_files but no clip_cache row.
    """
    catdv = _FakeCatdv()
    archive = _RecordingArchive()
    svc = ThumbnailService(
        cache_dir=tmp_path,
        archive=archive,
        catdv=catdv,
        metadata_cached_provider=lambda _cid: False,
    )
    assert await svc.get_or_fetch(42) is None
    assert archive.calls == []
    assert catdv.calls == []


@pytest.mark.asyncio
async def test_metadata_cached_proceeds_to_network(tmp_path: Path):
    """The gate must NOT block clips whose metadata IS cached — those
    still go through the normal fetch path when the local thumb file
    is missing."""
    catdv = _FakeCatdv()
    svc = ThumbnailService(
        cache_dir=tmp_path,
        archive=_FakeArchive({"posterID": 9000}),
        catdv=catdv,
        metadata_cached_provider=lambda _cid: True,
    )
    out = await svc.get_or_fetch(42)
    assert out == tmp_path / "42.jpg"
    assert catdv.calls == [9000]
