"""Orphaned-job recovery now RESUMES, not cancels (ADR 0125).

A job runs inside the single lifespan-owned JobRunner. A restart (deploy,
crash, or the dev --reload firing on a file save) leaves the DB saying
status='running' / item='prompting'. Faithful to the prefetch queue
(requeue_orphans on MediaPrefetcher.start), the worker now RESUMES these on
JobRunner.start() (requeue_orphaned_running: running -> pending, transient
items -> pending) instead of cancelling them. run_startup_cleanup no longer
touches jobs at all.
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
async def test_requeue_orphaned_running_resumes_job_and_transient_items(tmp_path):
    db = tmp_path / "t.db"
    async with aiosqlite.connect(db) as conn:
        await apply_migrations(conn, MIGRATIONS)
        jobs = JobsRepo()
        running_id, _ = await _seed_running_job(conn, clip_id=1, item_status="prompting")
        # A terminal job must be left alone.
        done_id, _ = await _seed_running_job(conn, clip_id=2, item_status="prompting")
        await jobs.update_status(conn, done_id, "completed")

        n = await jobs.requeue_orphaned_running(conn)

        assert n == 1
        # The orphan is requeued so the worker re-claims and resumes it; its
        # stuck transient item is reset so run_job re-runs it.
        assert (await jobs.get_job(conn, running_id)).status == "pending"
        items = await jobs.list_items(conn, running_id)
        assert items[0].status == "pending"
        # Terminal job untouched.
        assert (await jobs.get_job(conn, done_id)).status == "completed"


@pytest.mark.asyncio
async def test_startup_cleanup_no_longer_touches_jobs(tmp_path):
    db = tmp_path / "t.db"
    async with aiosqlite.connect(db) as conn:
        await apply_migrations(conn, MIGRATIONS)
        jobs = JobsRepo()
        job_id, _ = await _seed_running_job(conn, clip_id=888727, item_status="prompting")

        # Boot-time cleanup is now only about stale live_sessions — orphaned
        # jobs are resumed by JobRunner.start(), not here.
        await run_startup_cleanup(conn)

        assert (await jobs.get_job(conn, job_id)).status == "running"
        items = await jobs.list_items(conn, job_id)
        assert items[0].status == "prompting"
