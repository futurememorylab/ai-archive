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


class _FakeDurable:
    def __init__(self, has: set[int] | None = None):
        self.has = has or set()
        self.get_calls: list[int] = []
        self.put_calls: list[int] = []

    async def get(self, clip_id, dest):
        self.get_calls.append(clip_id)
        if clip_id in self.has:
            Path(dest).write_bytes(b"\xff\xd8GCS")
            return True
        return False

    async def put(self, clip_id, src):
        self.put_calls.append(clip_id)


@pytest.mark.asyncio
async def test_durable_hit_serves_offline_without_catdv(tmp_path: Path):
    # /data miss + GCS hit while CatDV OFFLINE -> served from GCS, no CatDV call.
    catdv = _FakeCatdv()
    durable = _FakeDurable(has={42})
    svc = ThumbnailService(
        cache_dir=tmp_path, archive=_FakeArchive({"posterID": 9000}),
        catdv=catdv, is_online_provider=lambda: False, durable_store=durable,
    )
    out = await svc.get_or_fetch(42)
    assert out == tmp_path / "42.jpg"
    assert out.read_bytes() == b"\xff\xd8GCS"
    assert catdv.calls == []
    assert durable.get_calls == [42]


@pytest.mark.asyncio
async def test_durable_miss_online_fetches_and_puts(tmp_path: Path):
    catdv = _FakeCatdv()
    durable = _FakeDurable(has=set())
    svc = ThumbnailService(
        cache_dir=tmp_path, archive=_FakeArchive({"posterID": 9000}),
        catdv=catdv, durable_store=durable,
    )
    out = await svc.get_or_fetch(42)
    assert out == tmp_path / "42.jpg"
    assert catdv.calls == [9000]
    assert durable.put_calls == [42]


@pytest.mark.asyncio
async def test_durable_miss_offline_returns_none(tmp_path: Path):
    catdv = _FakeCatdv()
    durable = _FakeDurable(has=set())
    svc = ThumbnailService(
        cache_dir=tmp_path, archive=_FakeArchive({"posterID": 9000}),
        catdv=catdv, is_online_provider=lambda: False, durable_store=durable,
    )
    assert await svc.get_or_fetch(42) is None
    assert catdv.calls == []
    assert durable.put_calls == []
    assert durable.get_calls == [42]


@pytest.mark.asyncio
async def test_data_hit_skips_durable(tmp_path: Path):
    (tmp_path / "42.jpg").write_bytes(b"local")
    durable = _FakeDurable(has={42})
    svc = ThumbnailService(
        cache_dir=tmp_path, archive=_FakeArchive({"posterID": 9000}),
        catdv=_FakeCatdv(), durable_store=durable,
    )
    out = await svc.get_or_fetch(42)
    assert out.read_bytes() == b"local"
    assert durable.get_calls == []


@pytest.mark.asyncio
async def test_no_durable_store_unchanged_behavior(tmp_path: Path):
    catdv = _FakeCatdv()
    svc = ThumbnailService(
        cache_dir=tmp_path, archive=_FakeArchive({"posterID": 9000}),
        catdv=catdv, is_online_provider=lambda: False,
    )
    assert await svc.get_or_fetch(42) is None


@pytest.mark.asyncio
async def test_uploaded_clip_served_from_durable(tmp_path: Path):
    from backend.app.uploaded_ids import to_clip_id
    cid = to_clip_id(3)
    durable = _FakeDurable(has={cid})
    svc = ThumbnailService(
        cache_dir=tmp_path, archive=_FakeArchive({}), catdv=None, durable_store=durable,
    )
    out = await svc.get_or_fetch(cid)
    assert out == svc.path_for(cid)
    assert durable.get_calls == [cid]


@pytest.mark.asyncio
async def test_push_durable_forwards(tmp_path: Path):
    durable = _FakeDurable()
    svc = ThumbnailService(
        cache_dir=tmp_path, archive=_FakeArchive({}), catdv=None, durable_store=durable,
    )
    p = tmp_path / "x.jpg"; p.write_bytes(b"jpg")
    await svc.push_durable(99, p)
    assert durable.put_calls == [99]


import asyncio as _asyncio


class _PosterProvider:
    def __init__(self, mapping: dict[int, int]):
        self.mapping = mapping
        self.calls: list[int] = []

    async def __call__(self, clip_id):
        self.calls.append(clip_id)
        return self.mapping.get(clip_id)


@pytest.mark.asyncio
async def test_poster_cache_fallback_downloads_without_get_clip(tmp_path: Path):
    # metadata gate says "not cached" -> use poster cache, download directly,
    # never call archive.get_clip.
    catdv = _FakeCatdv()
    poster = _PosterProvider({42: 882156})
    svc = ThumbnailService(
        cache_dir=tmp_path, archive=_RecordingArchive(), catdv=catdv,
        is_online_provider=lambda: True,
        metadata_cached_provider=lambda _cid: False,
        poster_id_provider=poster,
    )
    out = await svc.get_or_fetch(42)
    assert out == tmp_path / "42.jpg"
    assert catdv.calls == [882156]      # downloaded the poster id from cache
    assert poster.calls == [42]


@pytest.mark.asyncio
async def test_poster_cache_miss_returns_none(tmp_path: Path):
    catdv = _FakeCatdv()
    poster = _PosterProvider({})        # no entry
    svc = ThumbnailService(
        cache_dir=tmp_path, archive=_RecordingArchive(), catdv=catdv,
        is_online_provider=lambda: True,
        metadata_cached_provider=lambda _cid: False,
        poster_id_provider=poster,
    )
    assert await svc.get_or_fetch(42) is None
    assert catdv.calls == []


@pytest.mark.asyncio
async def test_no_poster_provider_gate_still_terminal(tmp_path: Path):
    # Without a poster provider, the gate miss is terminal as before.
    catdv = _FakeCatdv()
    svc = ThumbnailService(
        cache_dir=tmp_path, archive=_RecordingArchive(), catdv=catdv,
        is_online_provider=lambda: True,
        metadata_cached_provider=lambda _cid: False,
    )
    assert await svc.get_or_fetch(42) is None
    assert catdv.calls == []


@pytest.mark.asyncio
async def test_download_concurrency_is_bounded(tmp_path: Path):
    # Many concurrent fetches must not exceed download_concurrency in-flight.
    state = {"now": 0, "max": 0}

    class _SlowCatdv:
        async def download_thumbnail(self, thumb_id, dest, **kw):
            state["now"] += 1
            state["max"] = max(state["max"], state["now"])
            await _asyncio.sleep(0.02)
            state["now"] -= 1
            Path(dest).write_bytes(b"\xff\xd8x")

    poster = _PosterProvider({i: 1000 + i for i in range(10)})
    svc = ThumbnailService(
        cache_dir=tmp_path, archive=_RecordingArchive(), catdv=_SlowCatdv(),
        is_online_provider=lambda: True,
        metadata_cached_provider=lambda _cid: False,
        poster_id_provider=poster,
        download_concurrency=3,
    )
    outs = await _asyncio.gather(*[svc.get_or_fetch(i) for i in range(10)])
    assert all(o is not None for o in outs)
    assert state["max"] <= 3


@pytest.mark.asyncio
async def test_evict_removes_local_and_durable(tmp_path: Path):
    from unittest.mock import AsyncMock, MagicMock

    (tmp_path / "42.jpg").write_bytes(b"poster")
    durable = MagicMock()
    durable.delete = AsyncMock()
    svc = ThumbnailService(
        cache_dir=tmp_path, archive=_FakeArchive({}), durable_store=durable
    )
    await svc.evict(42)
    assert not (tmp_path / "42.jpg").exists()
    durable.delete.assert_awaited_once_with(42)


@pytest.mark.asyncio
async def test_evict_missing_local_is_not_an_error(tmp_path: Path):
    svc = ThumbnailService(cache_dir=tmp_path, archive=_FakeArchive({}))
    await svc.evict(999)  # no local file, no durable store — must not raise
