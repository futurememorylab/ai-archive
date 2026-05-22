import asyncio
from pathlib import Path

import pytest

from backend.app.services.poster_cache import PosterCache


@pytest.mark.asyncio
async def test_cache_miss_calls_fetcher_and_writes_file(tmp_path: Path):
    cache = PosterCache(tmp_path)
    calls: list[int] = []

    async def fetcher(clip_id: int) -> bytes:
        calls.append(clip_id)
        return b"\xff\xd8POSTER"

    path = await cache.get_or_fetch(42, fetcher)

    assert path == tmp_path / "42.jpg"
    assert path.read_bytes() == b"\xff\xd8POSTER"
    assert calls == [42]


@pytest.mark.asyncio
async def test_cache_hit_skips_fetcher(tmp_path: Path):
    (tmp_path / "7.jpg").write_bytes(b"already here")
    cache = PosterCache(tmp_path)

    async def fetcher(clip_id: int) -> bytes:
        raise AssertionError("fetcher must not be called on hit")

    path = await cache.get_or_fetch(7, fetcher)
    assert path.read_bytes() == b"already here"


@pytest.mark.asyncio
async def test_concurrent_first_fetches_coalesce(tmp_path: Path):
    cache = PosterCache(tmp_path)
    barrier = asyncio.Event()
    started = 0

    async def fetcher(clip_id: int) -> bytes:
        nonlocal started
        started += 1
        await barrier.wait()
        return b"\xff\xd8FROMUPSTREAM"

    async def call() -> bytes:
        path = await cache.get_or_fetch(99, fetcher)
        return path.read_bytes()

    t1 = asyncio.create_task(call())
    t2 = asyncio.create_task(call())
    await asyncio.sleep(0)  # let both reach the lock
    barrier.set()
    a, b = await asyncio.gather(t1, t2)

    assert a == b == b"\xff\xd8FROMUPSTREAM"
    assert started == 1, "second waiter should have read from disk, not re-fetched"


@pytest.mark.asyncio
async def test_atomic_write_does_not_leave_partial_file(tmp_path: Path, monkeypatch):
    cache = PosterCache(tmp_path)

    async def fetcher(clip_id: int) -> bytes:
        raise RuntimeError("upstream blew up")

    with pytest.raises(RuntimeError):
        await cache.get_or_fetch(13, fetcher)

    # No 13.jpg should exist, and no leftover 13.jpg.tmp either.
    assert not (tmp_path / "13.jpg").exists()
    assert not (tmp_path / "13.jpg.tmp").exists()


@pytest.mark.asyncio
async def test_creates_cache_dir_if_missing(tmp_path: Path):
    cache_dir = tmp_path / "deep" / "posters"
    cache = PosterCache(cache_dir)

    async def fetcher(clip_id: int) -> bytes:
        return b"x"

    path = await cache.get_or_fetch(1, fetcher)
    assert path.exists()
    assert cache_dir.is_dir()
