"""MediaPrefetcher: one-at-a-time background download worker.

Drains `prefetch_queue` in FIFO order. Each row is processed by calling
`backend.ensure_cached(int(clip_id))` -- the cache backend owns de-dup,
sizing, and recording the result (local proxy cache or GCS upload); the
prefetcher just sequences the work and records the queue-row outcome
(bytes=0, because the backend does not surface a size here).

Designed for the WireGuard pipe to Pragafilm: only one row is
in-flight at a time, by construction (a single coroutine + sequential
`tick_once()` calls). If a future deployment can tolerate parallelism,
that's a new service -- don't add a semaphore knob here.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

import aiosqlite

from backend.app.repositories.prefetch_queue import PrefetchQueueRepo
from backend.app.services.errors import humanise

log = logging.getLogger(__name__)


class MediaPrefetcher:
    def __init__(
        self,
        *,
        queue_repo: PrefetchQueueRepo,
        backend,
        db_provider: Callable[[], aiosqlite.Connection],
        tick_interval_s: float = 2.0,
    ) -> None:
        self._queue = queue_repo
        self._backend = backend
        self._db_provider = db_provider
        self._tick_interval_s = tick_interval_s
        self._stop_evt: asyncio.Event = asyncio.Event()
        self._task: asyncio.Task | None = None

    # --- lifecycle ---------------------------------------------------

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stop_evt.clear()
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task is None:
            return
        self._stop_evt.set()
        try:
            # Generous timeout -- a download in flight has to land or
            # error out before we can return. If the user really needs
            # the worker dead, cancel() is the escape hatch.
            await asyncio.wait_for(self._task, timeout=5.0)
        except TimeoutError:
            self._task.cancel()
        finally:
            self._task = None

    async def _loop(self) -> None:
        while not self._stop_evt.is_set():
            try:
                processed = await self.tick_once()
            except Exception:  # noqa: BLE001 -- loop must not die
                log.exception("media_prefetcher tick failed")
                processed = None
            if processed is None:
                try:
                    await asyncio.wait_for(
                        self._stop_evt.wait(),
                        timeout=self._tick_interval_s,
                    )
                except TimeoutError:
                    pass
            # If we processed a row, loop immediately to drain.

    # --- single tick -------------------------------------------------

    async def tick_once(self) -> int | None:
        """Process the next queued row, if any.

        Returns the integer clip id that was processed, or None if the
        queue was empty.
        """
        db = self._db_provider()
        row = await self._queue.claim_next(db)
        if row is None:
            return None
        rid = int(row["id"])
        clip_id_str = row["provider_clip_id"]
        try:
            clip_id_int = int(clip_id_str)
        except ValueError:
            await self._queue.mark_error(
                db,
                rid,
                f"non-integer clip id: {clip_id_str!r}",
            )
            return clip_id_int if clip_id_str.isdigit() else 0

        try:
            await self._backend.ensure_cached(clip_id_int)
            await self._queue.mark_done(db, rid, bytes_downloaded=0)
        except Exception as exc:  # noqa: BLE001
            # humanise(), not str(exc): a stalled-tunnel ReadTimeout has an
            # empty str(), which left the toast + sync drawer blank. exc_info
            # keeps the type/traceback in the log for diagnosis.
            msg = humanise(exc)
            log.warning("prefetch failed for clip %s: %s", clip_id_int, msg, exc_info=True)
            await self._queue.mark_error(db, rid, msg)
        return clip_id_int
