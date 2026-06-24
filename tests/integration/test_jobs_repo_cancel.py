"""JobsRepo.cancel_job — user-initiated cancel of a single job.

Cancelling a job must leave the DB internally consistent in one commit: the
job AND all its still-in-flight items flip to 'cancelled', while terminal
items (done / review_ready / applied / rejected / error / cancelled) are left
untouched. It must be idempotent so the in-flight task's CancelledError
handler can re-run it to mop up an item that raced into a transient state
after the cancel route's first sweep.
"""
from pathlib import Path

import aiosqlite
import pytest

from backend.app.migrations_runner import apply_migrations
from backend.app.repositories.jobs import JobsRepo
from backend.app.repositories.prompts import PromptsRepo

MIGRATIONS = Path(__file__).resolve().parents[2] / "backend" / "migrations"


async def _seed_job(conn, *, clip_ids):
    _pid, vid = await PromptsRepo().create_with_initial_version(
        conn, name="p", description=None, body="b",
        target_map={"x": {"kind": "markers"}}, output_schema={}, model="m",
    )
    jobs = JobsRepo()
    job_id = await jobs.create_job(conn, prompt_version_id=vid, clip_ids=clip_ids)
    await jobs.update_status(conn, job_id, "running")
    return job_id, await jobs.list_items(conn, job_id)


@pytest.mark.asyncio
async def test_cancel_job_flips_job_and_inflight_items(tmp_path):
    db = tmp_path / "t.db"
    async with aiosqlite.connect(db) as conn:
        await apply_migrations(conn, MIGRATIONS)
        jobs = JobsRepo()
        job_id, items = await _seed_job(conn, clip_ids=[1, 2, 3])
        # one in-flight, one still queued, one already done.
        await jobs.update_item_status(conn, items[0].id, "prompting")
        # items[1] left 'pending'
        await jobs.update_item_status(conn, items[2].id, "review_ready")

        await jobs.cancel_job(conn, job_id)

        assert (await jobs.get_job(conn, job_id)).status == "cancelled"
        after = {it.id: it.status for it in await jobs.list_items(conn, job_id)}
        assert after[items[0].id] == "cancelled"  # in-flight → cancelled
        assert after[items[1].id] == "cancelled"  # pending → cancelled
        assert after[items[2].id] == "review_ready"  # terminal → untouched


@pytest.mark.asyncio
async def test_cancel_job_leaves_error_items_untouched(tmp_path):
    db = tmp_path / "t.db"
    async with aiosqlite.connect(db) as conn:
        await apply_migrations(conn, MIGRATIONS)
        jobs = JobsRepo()
        job_id, items = await _seed_job(conn, clip_ids=[1])
        await jobs.update_item_status(conn, items[0].id, "error", error="boom")

        await jobs.cancel_job(conn, job_id)

        after = await jobs.list_items(conn, job_id)
        assert after[0].status == "error"
        assert after[0].error_message == "boom"


@pytest.mark.asyncio
async def test_cancel_job_is_idempotent(tmp_path):
    db = tmp_path / "t.db"
    async with aiosqlite.connect(db) as conn:
        await apply_migrations(conn, MIGRATIONS)
        jobs = JobsRepo()
        job_id, items = await _seed_job(conn, clip_ids=[1])
        await jobs.update_item_status(conn, items[0].id, "prompting")

        await jobs.cancel_job(conn, job_id)
        # A racing transient write lands AFTER the first cancel sweep.
        await jobs.update_item_status(conn, items[0].id, "uploading")
        await jobs.cancel_job(conn, job_id)  # mop-up run must reconcile it

        assert (await jobs.get_job(conn, job_id)).status == "cancelled"
        assert (await jobs.list_items(conn, job_id))[0].status == "cancelled"
