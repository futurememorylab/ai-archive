"""StudioRunsRepo — run creation, completion, latest lookup, version indicator."""

import json
from pathlib import Path

import aiosqlite
import pytest

from backend.app.db import open_db
from backend.app.migrations_runner import apply_migrations
from backend.app.repositories.studio_runs import StudioRunsRepo


@pytest.fixture
async def db(tmp_path: Path):
    cm = open_db(tmp_path / "t.db")
    conn = await cm.__aenter__()
    await apply_migrations(conn, Path("backend/migrations"))
    # seed a prompt + version so FK on studio_run.prompt_version_id is valid
    await conn.execute(
        "INSERT INTO prompts(id, name, archived, created_at, updated_at) "
        "VALUES (1, 'p', 0, '2026-05-26T00:00:00+00:00', '2026-05-26T00:00:00+00:00')"
    )
    await conn.execute(
        "INSERT INTO prompt_versions(id, prompt_id, version_num, state, body, target_map, "
        "output_schema, model, created_at, updated_at) "
        "VALUES (10, 1, 1, 'draft', 'do x', '{}', '{}', 'gemini-2.5-pro', "
        "'2026-05-26T00:00:00+00:00', '2026-05-26T00:00:00+00:00')"
    )
    await conn.execute(
        "INSERT INTO prompt_versions(id, prompt_id, version_num, state, body, target_map, "
        "output_schema, model, created_at, updated_at) "
        "VALUES (11, 1, 2, 'draft', 'do y', '{}', '{}', 'gemini-2.5-pro', "
        "'2026-05-26T00:00:00+00:00', '2026-05-26T00:00:00+00:00')"
    )
    # seed a job so FK on studio_run.job_id is valid
    await conn.execute(
        "INSERT INTO jobs(id, prompt_version_id, status, created_at, total_clips) "
        "VALUES (99, 10, 'running', '2026-05-26T00:00:00+00:00', 1)"
    )
    await conn.commit()
    yield conn
    await cm.__aexit__(None, None, None)


@pytest.mark.asyncio
async def test_create_pending_run(db: aiosqlite.Connection):
    repo = StudioRunsRepo()
    rid = await repo.create_pending(db, prompt_version_id=10, clip_id=12041, model="gemini-2.5-pro")
    cur = await db.execute("SELECT status, model, output_json FROM studio_run WHERE id = ?", (rid,))
    row = await cur.fetchone()
    assert row[0] == "pending"
    assert row[1] == "gemini-2.5-pro"
    assert row[2] is None


@pytest.mark.asyncio
async def test_attach_job(db):
    repo = StudioRunsRepo()
    rid = await repo.create_pending(db, prompt_version_id=10, clip_id=12041, model="gemini-2.5-pro")
    await repo.attach_job(db, rid, job_id=99)
    cur = await db.execute("SELECT job_id FROM studio_run WHERE id = ?", (rid,))
    assert (await cur.fetchone())[0] == 99


@pytest.mark.asyncio
async def test_complete_ok_persists_output_and_stats(db):
    repo = StudioRunsRepo()
    rid = await repo.create_pending(db, prompt_version_id=10, clip_id=12041, model="gemini-2.5-pro")
    output = {"scenes": [{"name": "garden", "in_secs": 0, "out_secs": 12.4}]}
    await repo.complete_ok(
        db, rid,
        output_json=output,
        duration_s=7.4, tokens_in=14820, tokens_out=612, cost_usd=0.0218,
    )
    run = await repo.get(db, rid)
    assert run.status == "ok"
    assert run.output_json == output
    assert run.duration_s == 7.4
    assert run.finished_at is not None


@pytest.mark.asyncio
async def test_complete_error_records_message(db):
    repo = StudioRunsRepo()
    rid = await repo.create_pending(db, prompt_version_id=10, clip_id=12041, model="gemini-2.5-pro")
    await repo.complete_error(db, rid, error="rate-limited")
    run = await repo.get(db, rid)
    assert run.status == "error"
    assert run.error == "rate-limited"
    assert run.output_json is None


@pytest.mark.asyncio
async def test_latest_for_pair_returns_most_recent(db):
    repo = StudioRunsRepo()
    r1 = await repo.create_pending(db, prompt_version_id=10, clip_id=12041, model="m")
    await repo.complete_ok(db, r1, output_json={"k": 1}, duration_s=1.0, tokens_in=0, tokens_out=0, cost_usd=0)
    r2 = await repo.create_pending(db, prompt_version_id=10, clip_id=12041, model="m")
    await repo.complete_ok(db, r2, output_json={"k": 2}, duration_s=1.0, tokens_in=0, tokens_out=0, cost_usd=0)
    latest = await repo.latest_for_pair(db, prompt_version_id=10, clip_id=12041)
    assert latest is not None
    assert latest.output_json == {"k": 2}


@pytest.mark.asyncio
async def test_latest_for_pair_none_when_no_runs(db):
    repo = StudioRunsRepo()
    latest = await repo.latest_for_pair(db, prompt_version_id=10, clip_id=99999)
    assert latest is None


@pytest.mark.asyncio
async def test_versions_run_on_clip(db):
    repo = StudioRunsRepo()
    # v10 succeeded on clip; v11 succeeded on clip
    a = await repo.create_pending(db, prompt_version_id=10, clip_id=12041, model="m")
    await repo.complete_ok(db, a, output_json={}, duration_s=1.0, tokens_in=0, tokens_out=0, cost_usd=0)
    b = await repo.create_pending(db, prompt_version_id=11, clip_id=12041, model="m")
    await repo.complete_ok(db, b, output_json={}, duration_s=1.0, tokens_in=0, tokens_out=0, cost_usd=0)
    # v10 also has an errored run on a different clip — should not appear here
    c = await repo.create_pending(db, prompt_version_id=10, clip_id=99, model="m")
    await repo.complete_error(db, c, error="x")
    versions = await repo.versions_run_on_clip(db, clip_id=12041)
    assert sorted(versions) == [10, 11]
