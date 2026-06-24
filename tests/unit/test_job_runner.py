"""JobRunner — the lifespan-owned, one-at-a-time annotation/studio job worker.
Twin of MediaPrefetcher. Driven here with a fake repo + fake run_job_fn so the
lifecycle (claim/run/loop/orphan-requeue/cancel) is testable in isolation.
See ADR 0125."""

import asyncio

import pytest

from backend.app.services.job_runner import JobRunner


class _FakeRepo:
    """Minimal stand-in: a list of pending job ids + recorded requeue calls."""

    def __init__(self, pending):
        self.pending = list(pending)
        self.requeued = 0
        self.running = None

    async def claim_next_job(self, conn):
        if not self.pending:
            return None
        self.running = self.pending.pop(0)
        return self.running

    async def requeue_orphaned_running(self, conn):
        self.requeued += 1
        return 0


@pytest.mark.asyncio
async def test_tick_once_claims_and_runs_one_job():
    repo = _FakeRepo([7])
    ran = []

    async def run_job_fn(job_id):
        ran.append(job_id)

    runner = JobRunner(jobs_repo=repo, run_job_fn=run_job_fn, db_provider=lambda: None)
    result = await runner.tick_once()
    assert result == 7
    assert ran == [7]
    # empty queue -> None
    assert await runner.tick_once() is None


@pytest.mark.asyncio
async def test_tick_once_swallows_job_error_and_keeps_going():
    repo = _FakeRepo([1, 2])

    async def run_job_fn(job_id):
        if job_id == 1:
            raise RuntimeError("boom")

    runner = JobRunner(jobs_repo=repo, run_job_fn=run_job_fn, db_provider=lambda: None)
    assert await runner.tick_once() == 1  # error is logged, not raised
    assert await runner.tick_once() == 2


@pytest.mark.asyncio
async def test_start_requeues_orphans_then_drains_queue():
    repo = _FakeRepo([1, 2])
    ran = []

    async def run_job_fn(job_id):
        ran.append(job_id)

    runner = JobRunner(
        jobs_repo=repo, run_job_fn=run_job_fn, db_provider=lambda: None, tick_interval_s=0.01
    )
    await runner.start()
    # let the loop drain the queue
    for _ in range(50):
        if ran == [1, 2]:
            break
        await asyncio.sleep(0.01)
    await runner.stop()
    assert repo.requeued == 1
    assert ran == [1, 2]


@pytest.mark.asyncio
async def test_cancel_interrupts_only_the_current_job():
    repo = _FakeRepo([5])
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def run_job_fn(job_id):
        started.set()
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    runner = JobRunner(
        jobs_repo=repo, run_job_fn=run_job_fn, db_provider=lambda: None, tick_interval_s=0.01
    )
    await runner.start()
    await asyncio.wait_for(started.wait(), timeout=1.0)
    runner.cancel(999)  # not the current job -> no-op
    assert not cancelled.is_set()
    runner.cancel(5)  # the current job -> interrupted
    await asyncio.wait_for(cancelled.wait(), timeout=1.0)
    await runner.stop()
