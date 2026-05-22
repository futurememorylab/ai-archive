"""On-disk cache for CatDV poster JPEGs.

One JPEG per clip, keyed by `clip_id`, written atomically. A per-clip
asyncio.Lock coalesces concurrent first-fetches so the upstream is hit
exactly once; subsequent waiters fall through to the disk-hit branch.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable
from pathlib import Path


class PosterCache:
    def __init__(self, cache_dir: Path) -> None:
        self._dir = Path(cache_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._locks: dict[int, asyncio.Lock] = {}
        self._locks_guard = asyncio.Lock()

    def _path_for(self, clip_id: int) -> Path:
        return self._dir / f"{clip_id}.jpg"

    async def _lock_for(self, clip_id: int) -> asyncio.Lock:
        async with self._locks_guard:
            lock = self._locks.get(clip_id)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[clip_id] = lock
            return lock

    async def get_or_fetch(
        self,
        clip_id: int,
        fetcher: Callable[[int], Awaitable[bytes]],
    ) -> Path:
        path = self._path_for(clip_id)
        if path.exists():
            return path

        lock = await self._lock_for(clip_id)
        async with lock:
            # Double-check: another coroutine may have written the file
            # while we were waiting.
            if path.exists():
                return path

            data = await fetcher(clip_id)
            tmp = path.with_suffix(path.suffix + ".tmp")
            try:
                tmp.write_bytes(data)
                os.replace(tmp, path)
            except Exception:
                if tmp.exists():
                    tmp.unlink()
                raise
            return path
