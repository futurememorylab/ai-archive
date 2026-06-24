"""build_run_one_job — the per-job worker callable the lifespan JobRunner runs.
It wraps annotator.run_job with lifecycle reconciliation so a job is never left
lingering 'running' (ADR 0125):
  - CancelledError → reconcile_interrupted_job (mop up if user-cancelled, else
    leave 'running' for the next boot's requeue to resume);
  - any other Exception → fail_job (flip terminal so it isn't re-claimed and
    re-crashed every restart).
run_job is faked here so only the wrapper's routing is under test (fix #2/#3)."""

import asyncio
import types
from pathlib import Path

import pytest

from backend.app.db import open_db
from backend.app.migrations_runner import apply_migrations
from backend.app.repositories.jobs import JobsRepo
from backend.app.services import annotator


@pytest.fixture
async def core(tmp_path: Path):
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
    # The closure reads ~12 attrs off `core`; only db + jobs_repo are exercised
    # because run_job is faked, so the rest can be None.
    yield types.SimpleNamespace(
        db=conn,
        jobs_repo=JobsRepo(),
        event_bus=None,
        annotations_repo=None,
        review_items_repo=None,
        prompts_repo=None,
        studio_runs_repo=None,
        uploaded_clips_repo=None,
        run_telemetry_repo=None,
        telemetry_ctx=None,
        model_config_repo=None,
        prefetch_queue_repo=None,
    )
    await cm.__aexit__(None, None, None)


def _runner(core):
    return annotator.build_run_one_job(
        core, archive=None, proxy_resolver=None, ai_store=None, gemini=None
    )


@pytest.mark.asyncio
async def test_exception_marks_job_failed(core, monkeypatch):
    jid = await core.jobs_repo.create_job(core.db, prompt_version_id=1, clip_ids=[10])
    await core.jobs_repo.update_status(core.db, jid, "running")

    async def _boom(**kw):
        raise RuntimeError("prompt version deleted")

    monkeypatch.setattr(annotator, "run_job", _boom)

    with pytest.raises(RuntimeError):
        await _runner(core)(jid)

    assert (await core.jobs_repo.get_job(core.db, jid)).status == "failed"
    assert (await core.jobs_repo.list_items(core.db, jid))[0].status == "error"


@pytest.mark.asyncio
async def test_cancel_after_route_flip_mops_up(core, monkeypatch):
    jid = await core.jobs_repo.create_job(core.db, prompt_version_id=1, clip_ids=[10])
    await core.jobs_repo.update_status(core.db, jid, "running")
    await core.jobs_repo.cancel_job(core.db, jid)  # route flipped it first

    async def _cancelled(**kw):
        raise asyncio.CancelledError

    monkeypatch.setattr(annotator, "run_job", _cancelled)

    with pytest.raises(asyncio.CancelledError):
        await _runner(core)(jid)

    assert (await core.jobs_repo.get_job(core.db, jid)).status == "cancelled"
    assert (await core.jobs_repo.list_items(core.db, jid))[0].status == "cancelled"


@pytest.mark.asyncio
async def test_shutdown_cancel_leaves_job_running_for_resume(core, monkeypatch):
    jid = await core.jobs_repo.create_job(core.db, prompt_version_id=1, clip_ids=[10])
    await core.jobs_repo.update_status(core.db, jid, "running")  # route did NOT flip

    async def _cancelled(**kw):
        raise asyncio.CancelledError

    monkeypatch.setattr(annotator, "run_job", _cancelled)

    with pytest.raises(asyncio.CancelledError):
        await _runner(core)(jid)

    # Still 'running' → next boot's requeue_orphaned_running resumes it.
    assert (await core.jobs_repo.get_job(core.db, jid)).status == "running"
