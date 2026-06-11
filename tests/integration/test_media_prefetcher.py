import asyncio

import httpx
import pytest

from backend.app.repositories.prefetch_queue import PrefetchQueueRepo


class _FakeBackend:
    def __init__(self, sleep_s: float = 0.0, fail_on: set[int] | None = None):
        self._sleep_s = sleep_s
        self._fail_on = fail_on or set()
        self.calls: list[int] = []

    async def ensure_cached(self, clip_id: int) -> None:
        self.calls.append(clip_id)
        if clip_id in self._fail_on:
            raise RuntimeError(f"boom on {clip_id}")
        if self._sleep_s:
            await asyncio.sleep(self._sleep_s)


@pytest.mark.asyncio
async def test_tick_drains_in_order(db):
    from backend.app.services.media_prefetcher import MediaPrefetcher

    repo = PrefetchQueueRepo()
    await repo.enqueue(db, key=("catdv", "1"), who="request")
    await repo.enqueue(db, key=("catdv", "2"), who="request")
    backend = _FakeBackend()
    pf = MediaPrefetcher(
        queue_repo=repo,
        backend=backend,
        db_provider=lambda: db,
    )
    a = await pf.tick_once()
    b = await pf.tick_once()
    c = await pf.tick_once()  # empty
    assert a == 1 and b == 2 and c is None
    assert backend.calls == [1, 2]
    counts = await repo.count_by_status(db)
    assert counts.get("done") == 2


@pytest.mark.asyncio
async def test_tick_records_error_does_not_block_queue(db):
    from backend.app.services.media_prefetcher import MediaPrefetcher

    repo = PrefetchQueueRepo()
    await repo.enqueue(db, key=("catdv", "1"), who="request")
    await repo.enqueue(db, key=("catdv", "2"), who="request")
    backend = _FakeBackend(fail_on={1})
    pf = MediaPrefetcher(
        queue_repo=repo,
        backend=backend,
        db_provider=lambda: db,
    )
    await pf.tick_once()
    await pf.tick_once()
    counts = await repo.count_by_status(db)
    assert counts.get("error") == 1
    assert counts.get("done") == 1


@pytest.mark.asyncio
async def test_tick_records_humanised_error_not_blank(db):
    """A timeout-type failure (str(exc) == "") must be recorded as a
    non-empty, actionable message — not the blank string users saw in the
    toast + sync drawer for the tunnel-stall ReadTimeout."""
    from backend.app.services.media_prefetcher import MediaPrefetcher

    class _TimeoutBackend:
        async def ensure_cached(self, clip_id: int) -> None:
            raise httpx.ReadTimeout("")  # str(exc) == ""

    repo = PrefetchQueueRepo()
    await repo.enqueue(db, key=("catdv", "888892"), who="request")
    pf = MediaPrefetcher(
        queue_repo=repo,
        backend=_TimeoutBackend(),
        db_provider=lambda: db,
    )
    await pf.tick_once()

    rows = await repo.list_recent(db, limit=10)
    row = next(r for r in rows if r["provider_clip_id"] == "888892")
    assert row["status"] == "error"
    assert (row["error"] or "").strip(), "error message must not be blank"
    assert "timeout" in row["error"].lower()


@pytest.mark.asyncio
async def test_loop_processes_one_at_a_time(db):
    """Two slow downloads must not overlap."""
    from backend.app.services.media_prefetcher import MediaPrefetcher

    repo = PrefetchQueueRepo()
    await repo.enqueue(db, key=("catdv", "1"), who="request")
    await repo.enqueue(db, key=("catdv", "2"), who="request")

    overlap = {"max_concurrent": 0, "current": 0}

    class _TrackingBackend(_FakeBackend):
        async def ensure_cached(self, clip_id):
            overlap["current"] += 1
            overlap["max_concurrent"] = max(
                overlap["max_concurrent"],
                overlap["current"],
            )
            await asyncio.sleep(0.05)
            overlap["current"] -= 1

    pf = MediaPrefetcher(
        queue_repo=repo,
        backend=_TrackingBackend(),
        db_provider=lambda: db,
        tick_interval_s=0.01,
    )
    await pf.start()
    # Allow both rows to drain
    for _ in range(50):
        if (await repo.count_by_status(db)).get("done") == 2:
            break
        await asyncio.sleep(0.05)
    await pf.stop()
    assert overlap["max_concurrent"] == 1


@pytest.mark.asyncio
async def test_stop_returns_promptly_between_rows(db):
    from backend.app.services.media_prefetcher import MediaPrefetcher

    repo = PrefetchQueueRepo()
    backend = _FakeBackend()
    pf = MediaPrefetcher(
        queue_repo=repo,
        backend=backend,
        db_provider=lambda: db,
        tick_interval_s=0.01,
    )
    await pf.start()
    await asyncio.sleep(0.05)
    await pf.stop()  # must not hang
