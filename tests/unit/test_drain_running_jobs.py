"""drain_running_jobs — shutdown drain for fire-and-forget annotation/studio
job tasks.

On SIGTERM, LiveCtx.aclose() must cancel and await any in-flight job tasks
tracked in CoreCtx._running_jobs *before* it closes the DB connection.
Without this the DB is closed under a running run_job(), which then dies with
a write-to-closed-DB error and the completed-but-unpersisted Gemini work is
lost. Mirrors MediaPrefetcher.stop(): cancel, then bounded await.
"""
import asyncio

import pytest

from backend.app.context import drain_running_jobs


@pytest.mark.asyncio
async def test_drain_cancels_inflight_tasks():
    started = asyncio.Event()

    async def _never_ends():
        started.set()
        await asyncio.sleep(3600)

    running: dict[int, object] = {1: asyncio.create_task(_never_ends())}
    await started.wait()

    await drain_running_jobs(running, timeout=2.0)

    assert running[1].cancelled()


@pytest.mark.asyncio
async def test_drain_leaves_completed_tasks_alone():
    async def _done():
        return "ok"

    task = asyncio.create_task(_done())
    await task
    running: dict[int, object] = {7: task}

    await drain_running_jobs(running, timeout=2.0)

    assert not task.cancelled()
    assert task.result() == "ok"


@pytest.mark.asyncio
async def test_drain_empty_is_noop():
    await drain_running_jobs({}, timeout=2.0)  # must not raise
