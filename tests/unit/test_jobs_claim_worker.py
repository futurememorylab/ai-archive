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
