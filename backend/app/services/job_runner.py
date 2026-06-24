"""JobRunner: one-at-a-time, lifespan-owned annotation/studio job worker.

The queue paradigm twin of MediaPrefetcher (services/media_prefetcher.py).
Routes insert a `pending` jobs row and return; this worker claims the oldest
pending job via CAS (JobsRepo.claim_next_job), runs it through run_job, and
loops. One job runs at a time, by construction (a single coroutine + sequential
tick_once calls) — do NOT add a concurrency knob; that would be a new service.

Orphan recovery RESUMES (requeue_orphaned_running: running -> pending) rather
than cancelling, so a crash/restart re-runs unfinished work for free. run_job
is idempotent (it skips already-finished items), so resume only re-runs what
was left undone. See docs/adr/0125-jobrunner-claim-worker.md.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

import aiosqlite

from backend.app.repositories.jobs import JobsRepo
from backend.app.services.errors import humanise

log = logging.getLogger(__name__)


class JobRunner:
    def __init__(
        self,
        *,
        jobs_repo: JobsRepo,
        run_job_fn: Callable[[int], Awaitable[None]],
        db_provider: Callable[[], aiosqlite.Connection],
        tick_interval_s: float = 0.75,
    ) -> None:
        self._jobs = jobs_repo
        self._run_job_fn = run_job_fn
        self._db_provider = db_provider
        self._tick_interval_s = tick_interval_s
        self._stop_evt: asyncio.Event = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._current: tuple[int, asyncio.Task] | None = None

    # --- lifecycle ---------------------------------------------------

    async def start(self) -> None:
        if self._task is not None:
            return
        try:
            requeued = await self._jobs.requeue_orphaned_running(self._db_provider())
            if requeued:
                log.info("job_runner requeued %d orphaned running job(s) on start", requeued)
        except Exception:  # noqa: BLE001 -- recovery must not block startup
            log.exception("job_runner orphan recovery failed")
        self._stop_evt.clear()
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task is None:
            return
        self._stop_evt.set()
        # Interrupt the in-flight job so shutdown does not wait out a long
        # Gemini call; its CancelledError handler reconciles DB state.
        if self._current is not None:
            _, cur_task = self._current
            cur_task.cancel()
        try:
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
                log.exception("job_runner tick failed")
                processed = None
            if processed is None:
                try:
                    await asyncio.wait_for(
                        self._stop_evt.wait(),
                        timeout=self._tick_interval_s,
                    )
                except TimeoutError:
                    pass
            # If we processed a job, loop immediately to drain.

    # --- single tick -------------------------------------------------

    async def tick_once(self) -> int | None:
        """Claim and run the next pending job, if any. Returns the job id that
        was run, or None when the queue was empty."""
        db = self._db_provider()
        job_id = await self._jobs.claim_next_job(db)
        if job_id is None:
            return None
        task = asyncio.create_task(self._run_job_fn(job_id))
        self._current = (job_id, task)
        try:
            await task
        except asyncio.CancelledError:
            # Cancelled via cancel()/stop(): run_job_fn's own handler already
            # reconciled the job's item state. Swallow here so the loop lives.
            log.info("job %s cancelled mid-run", job_id)
        except Exception as exc:  # noqa: BLE001 -- one bad job must not kill the loop
            log.warning("job %s failed: %s", job_id, humanise(exc), exc_info=True)
        finally:
            self._current = None
        return job_id

    def cancel(self, job_id: int) -> None:
        """Interrupt the in-flight job iff it is the one currently running.
        No-op otherwise (a pending/terminal job needs only the DB flip the
        cancel route already did)."""
        if self._current is not None and self._current[0] == job_id:
            self._current[1].cancel()
