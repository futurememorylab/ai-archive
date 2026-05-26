"""JobsRepo — `kind` column round-trips and defaults to NULL for back-compat."""

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
        "VALUES (10, 1, 1, 'draft', 'b', '{}', '{}', 'm', "
        "'2026-05-26T00:00:00+00:00', '2026-05-26T00:00:00+00:00')"
    )
    await conn.commit()
    yield conn
    await cm.__aexit__(None, None, None)


@pytest.mark.asyncio
async def test_create_job_default_kind_is_null(db):
    repo = JobsRepo()
    jid = await repo.create_job(db, prompt_version_id=10, clip_ids=[42])
    job = await repo.get_job(db, jid)
    assert job.kind is None


@pytest.mark.asyncio
async def test_create_job_with_studio_kind(db):
    repo = JobsRepo()
    jid = await repo.create_job(db, prompt_version_id=10, clip_ids=[42], kind="studio")
    job = await repo.get_job(db, jid)
    assert job.kind == "studio"
