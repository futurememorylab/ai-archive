"""JobsRepo claim-worker primitives — claim_next_job (CAS) and
requeue_orphaned_running (resume on boot). These back the lifespan-owned
JobRunner (services/job_runner.py). See ADR 0125."""

from pathlib import Path

import pytest

from backend.app.db import open_db
from backend.app.migrations_runner import apply_migrations
from backend.app.repositories.jobs import JobsRepo


@pytest.fixture
async def db(tmp_path: Path):
    cm = open_db(tmp_path / "t.db")
    conn = await cm.__aenter__()
    await apply_migrations(conn, Path("backend/migrations"))
    await conn.execute(
        "INSERT INTO prompts(id, name, archived, created_at, updated_at) "
        "VALUES (1, 'p', 0, '2026-05-26T00:00:00+00:00', '2026-05-26T00:00:00+00:00')"
    )
    await conn.execute(
        "INSERT INTO prompt_versions(id, prompt_id, version_num, state, body, target_map, "
        "output_schema, model, created_at, updated_at) "
        "VALUES (1, 1, 1, 'draft', 'b', '{}', '{}', 'm', "
        "'2026-05-26T00:00:00+00:00', '2026-05-26T00:00:00+00:00')"
    )
    await conn.commit()
    yield conn
    await cm.__aexit__(None, None, None)


@pytest.mark.asyncio
async def test_claim_next_job_takes_oldest_pending_and_marks_running(db):
    repo = JobsRepo()
    first = await repo.create_job(db, prompt_version_id=1, clip_ids=[10])
    second = await repo.create_job(db, prompt_version_id=1, clip_ids=[11])

    claimed = await repo.claim_next_job(db)
    assert claimed == first  # oldest first

    job = await repo.get_job(db, first)
    assert job.status == "running"
    # second is still pending
    assert (await repo.get_job(db, second)).status == "pending"


@pytest.mark.asyncio
async def test_claim_next_job_returns_none_when_no_pending(db):
    repo = JobsRepo()
    assert await repo.claim_next_job(db) is None


@pytest.mark.asyncio
async def test_claim_next_job_skips_non_pending(db):
    repo = JobsRepo()
    jid = await repo.create_job(db, prompt_version_id=1, clip_ids=[10])
    await repo.update_status(db, jid, "running")  # already running
    assert await repo.claim_next_job(db) is None


@pytest.mark.asyncio
async def test_requeue_orphaned_running_resumes_jobs_and_resets_transient_items(db):
    repo = JobsRepo()
    jid = await repo.create_job(db, prompt_version_id=1, clip_ids=[10, 11, 12])
    await repo.update_status(db, jid, "running")
    items = await repo.list_items(db, jid)
    # one done, one mid-prompting (orphaned transient), one still pending
    await repo.update_item_status(db, items[0].id, "annotated")
    await repo.update_item_status(db, items[1].id, "prompting")
    # items[2] stays pending

    n = await repo.requeue_orphaned_running(db)
    assert n == 1

    assert (await repo.get_job(db, jid)).status == "pending"
    after = {i.catdv_clip_id: i.status for i in await repo.list_items(db, jid)}
    assert after[10] == "annotated"  # terminal item untouched
    assert after[11] == "pending"  # transient reset so run_job re-runs it
    assert after[12] == "pending"


@pytest.mark.asyncio
async def test_requeue_orphaned_running_ignores_terminal_jobs(db):
    repo = JobsRepo()
    jid = await repo.create_job(db, prompt_version_id=1, clip_ids=[10])
    await repo.update_status(db, jid, "completed")
    assert await repo.requeue_orphaned_running(db) == 0
    assert (await repo.get_job(db, jid)).status == "completed"


# --- worker interrupt reconciliation (fix #2) ---------------------------


@pytest.mark.asyncio
async def test_reconcile_interrupted_job_mops_up_when_already_cancelled(db):
    """User-cancel path: the cancel route flips the job to 'cancelled' BEFORE
    interrupting the worker, so on CancelledError the worker mops up any item
    that raced into a transient state."""
    repo = JobsRepo()
    jid = await repo.create_job(db, prompt_version_id=1, clip_ids=[10, 11])
    await repo.update_status(db, jid, "running")
    items = await repo.list_items(db, jid)
    await repo.update_item_status(db, items[0].id, "prompting")  # raced transient
    await repo.cancel_job(db, jid)  # the route's DB flip → status 'cancelled'

    await repo.reconcile_interrupted_job(db, jid)

    assert (await repo.get_job(db, jid)).status == "cancelled"
    after = {i.catdv_clip_id: i.status for i in await repo.list_items(db, jid)}
    assert after[10] == "cancelled"  # transient mopped up
    assert after[11] == "cancelled"


@pytest.mark.asyncio
async def test_reconcile_interrupted_job_leaves_running_for_resume(db):
    """Shutdown-drain path: stop() interrupts the worker but the route did NOT
    flip the job, so it is still 'running'. Reconcile must leave it untouched so
    the next boot's requeue_orphaned_running resumes it (ADR 0125)."""
    repo = JobsRepo()
    jid = await repo.create_job(db, prompt_version_id=1, clip_ids=[10])
    await repo.update_status(db, jid, "running")
    items = await repo.list_items(db, jid)
    await repo.update_item_status(db, items[0].id, "prompting")

    await repo.reconcile_interrupted_job(db, jid)

    assert (await repo.get_job(db, jid)).status == "running"  # left for resume
    assert (await repo.list_items(db, jid))[0].status == "prompting"


# --- job-level failure (fix #3) -----------------------------------------


@pytest.mark.asyncio
async def test_fail_job_makes_a_running_job_terminal(db):
    """A job that errored at the job level (e.g. its prompt version was deleted)
    must be flipped terminal so it is not re-claimed and re-crashed on every
    restart. Its still-in-flight items become 'error' too."""
    repo = JobsRepo()
    jid = await repo.create_job(db, prompt_version_id=1, clip_ids=[10, 11])
    await repo.update_status(db, jid, "running")
    items = await repo.list_items(db, jid)
    await repo.update_item_status(db, items[1].id, "prompting")

    await repo.fail_job(db, jid)

    assert (await repo.get_job(db, jid)).status == "failed"
    after = {i.catdv_clip_id: i.status for i in await repo.list_items(db, jid)}
    assert after[10] == "error"  # pending → error
    assert after[11] == "error"  # transient → error


@pytest.mark.asyncio
async def test_fail_job_leaves_terminal_jobs_alone(db):
    """fail_job is guarded on status='running' so a racing cancel/complete is
    never clobbered."""
    repo = JobsRepo()
    jid = await repo.create_job(db, prompt_version_id=1, clip_ids=[10])
    await repo.update_status(db, jid, "cancelled")
    items = await repo.list_items(db, jid)

    await repo.fail_job(db, jid)

    assert (await repo.get_job(db, jid)).status == "cancelled"  # untouched
    assert (await repo.list_items(db, jid))[0].status == items[0].status
