"""Issue #78: the prefetcher's progress tracker writes the DB at most once
per interval, but tracks the latest bytes in memory on every call."""

import pytest

from backend.app.services.media_prefetcher import _ProgressTracker


class _SpyRepo:
    def __init__(self):
        self.writes: list[tuple[int, int]] = []

    async def update_progress(self, conn, rid, bytes_downloaded, bytes_total):
        self.writes.append((bytes_downloaded, bytes_total))


@pytest.mark.asyncio
async def test_tracker_throttles_db_writes_but_tracks_latest():
    clock = {"t": 0.0}
    repo = _SpyRepo()
    tracker = _ProgressTracker(
        repo, conn=None, rid=1, min_interval_s=0.75, clock=lambda: clock["t"]
    )

    # First call always writes.
    await tracker(1_000, 10_000)
    # Within the interval: no new write, but latest is tracked.
    clock["t"] = 0.1
    await tracker(2_000, 10_000)
    clock["t"] = 0.5
    await tracker(3_000, 10_000)
    # Past the interval: writes again.
    clock["t"] = 0.9
    await tracker(4_000, 10_000)

    assert repo.writes == [(1_000, 10_000), (4_000, 10_000)]
    assert tracker.last_downloaded == 4_000
    assert tracker.last_total == 10_000
