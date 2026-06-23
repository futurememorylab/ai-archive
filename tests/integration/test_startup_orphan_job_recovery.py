"""Startup recovery for orphaned jobs (ADR 0114 follow-up).

A job runs as a fire-and-forget background task. A restart (deploy, crash, or
the dev --reload firing on a file save) kills that task mid-flight, but the DB
still says status='running' / item='prompting'. Unlike the prefetch queue
(requeue_orphans on MediaPrefetcher.start), nothing recovered these — so the
clip page kept showing "Annotating" forever for a job that was already dead.

run_startup_cleanup must, at boot (single process → nothing in-flight by
construction): reset transient item statuses to 'pending' and cancel the
orphaned 'running' jobs so they stop appearing active.
"""
from pathlib import Path

import aiosqlite
import pytest

from backend.app.migrations_runner import apply_migrations
from backend.app.repositories.jobs import JobsRepo
from backend.app.repositories.prompts import PromptsRepo
from backend.app.startup import run_startup_cleanup

MIGRATIONS = Path(__file__).resolve().parents[2] / "backend" / "migrations"


async def _seed_running_job(conn, *, clip_id, item_status):
    _pid, vid = await PromptsRepo().create_with_initial_version(
        conn, name=f"p{clip_id}", description=None, body="b",
        target_map={"x": {"kind": "markers"}}, output_schema={}, model="m",
    )
    jobs = JobsRepo()
    job_id = await jobs.create_job(conn, prompt_version_id=vid, clip_ids=[clip_id])
    await jobs.update_status(conn, job_id, "running")
    items = await jobs.list_items(conn, job_id)
    await jobs.update_item_status(conn, items[0].id, item_status)
    return job_id, items[0].id


@pytest.mark.asyncio
async def test_cancel_orphaned_running_flips_job_and_items_to_cancelled(tmp_path):
    db = tmp_path / "t.db"
    async with aiosqlite.connect(db) as conn:
        await apply_migrations(conn, MIGRATIONS)
        jobs = JobsRepo()
        running_id, run_item = await _seed_running_job(conn, clip_id=1, item_status="prompting")
        # A terminal job must be left alone.
        done_id, _ = await _seed_running_job(conn, clip_id=2, item_status="prompting")
        await jobs.update_status(conn, done_id, "completed")

        n = await jobs.cancel_orphaned_running(conn)

        assert n == 1
        assert (await jobs.get_job(conn, running_id)).status == "cancelled"
        # The unfinished item is cancelled too — a terminal job must not keep a
        # pending/in-flight item (that's what made the Batches view show it
        # "Running" while the clip page showed it stopped).
        items = await jobs.list_items(conn, running_id)
        assert items[0].status == "cancelled"
        # Terminal job untouched.
        assert (await jobs.get_job(conn, done_id)).status == "completed"


@pytest.mark.asyncio
async def test_startup_cleanup_clears_orphaned_annotate_job(tmp_path):
    db = tmp_path / "t.db"
    async with aiosqlite.connect(db) as conn:
        await apply_migrations(conn, MIGRATIONS)
        jobs = JobsRepo()
        job_id, item_id = await _seed_running_job(conn, clip_id=888727, item_status="prompting")

        await run_startup_cleanup(conn)

        # The phantom is gone: job + its unfinished item both cancelled.
        assert (await jobs.get_job(conn, job_id)).status == "cancelled"
        items = await jobs.list_items(conn, job_id)
        assert items[0].status == "cancelled"
        # And the clip page would no longer try to resume it.
        assert await jobs.find_running_item_for_clip(conn, 888727) is None
