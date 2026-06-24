"""POST /api/jobs/{id}/cancel must flip DB state AND interrupt the in-flight
job promptly.

Under the JobRunner claim-worker model (ADR 0125) the route is a pure DB
writer for cancel: it flips the job + its in-flight items to 'cancelled'
(offline-safe) and then, when a live worker is running this exact job, calls
`live.job_runner.cancel(job_id)` so the long Gemini call is interrupted instead
of running to completion. Cancel latency is no longer a whole clip.
"""
import asyncio
from pathlib import Path

import aiosqlite
import pytest
from httpx import ASGITransport, AsyncClient

from backend.app.main import app
from backend.app.migrations_runner import apply_migrations
from backend.app.repositories.jobs import JobsRepo
from backend.app.repositories.prompts import PromptsRepo
from backend.app.services.job_runner import JobRunner

MIGRATIONS = Path(__file__).resolve().parents[2] / "backend" / "migrations"


@pytest.fixture
async def client_db(tmp_path):
    conn = await aiosqlite.connect(tmp_path / "t.db")
    await apply_migrations(conn, MIGRATIONS)

    class _Ctx:
        db = conn
        jobs_repo = JobsRepo()
        settings = type("S", (), {"auth_backend": "dev", "dev_user_email": "dev@localhost"})()

    ctx = _Ctx()
    app.state.core_ctx = ctx
    app.state.live_ctx = None
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac, conn, ctx
    await conn.close()


@pytest.mark.asyncio
async def test_cancel_route_flips_db_and_interrupts_inflight_job(client_db):
    ac, conn, ctx = client_db
    _pid, vid = await PromptsRepo().create_with_initial_version(
        conn, name="p", description=None, body="b",
        target_map={"x": {"kind": "markers"}}, output_schema={}, model="m",
    )
    job_id = await ctx.jobs_repo.create_job(conn, prompt_version_id=vid, clip_ids=[1])
    await ctx.jobs_repo.update_status(conn, job_id, "running")
    items = await ctx.jobs_repo.list_items(conn, job_id)
    await ctx.jobs_repo.update_item_status(conn, items[0].id, "prompting")

    started = asyncio.Event()

    async def _inflight():
        started.set()
        await asyncio.sleep(3600)

    inflight = asyncio.create_task(_inflight())
    await started.wait()

    # A live JobRunner currently running this exact job.
    runner = JobRunner(
        jobs_repo=ctx.jobs_repo, run_job_fn=lambda jid: None, db_provider=lambda: conn
    )
    runner._current = (job_id, inflight)
    app.state.live_ctx = type("Live", (), {"job_runner": runner})()

    resp = await ac.post(f"/api/jobs/{job_id}/cancel")
    assert resp.status_code == 200
    assert resp.json()["status"] == "cancelled"

    await asyncio.sleep(0)  # let the cancellation propagate
    assert inflight.cancelled()
    assert (await ctx.jobs_repo.get_job(conn, job_id)).status == "cancelled"
    assert (await ctx.jobs_repo.list_items(conn, job_id))[0].status == "cancelled"
