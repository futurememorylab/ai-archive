"""POST /api/jobs/{id}/cancel must actually interrupt the in-flight task.

Before this fix the route only flipped the DB row to 'cancelled'; the
fire-and-forget asyncio task tracked in CoreCtx._running_jobs was never
cancelled, so an in-flight item (notably the long Gemini call) ran to
completion and cancel latency was a whole clip. The route now also cancels
the tracked task, and reconciles job + item state in one commit.
"""
from pathlib import Path

import asyncio

import aiosqlite
import pytest
from httpx import ASGITransport, AsyncClient

from backend.app.main import app
from backend.app.migrations_runner import apply_migrations
from backend.app.repositories.jobs import JobsRepo
from backend.app.repositories.prompts import PromptsRepo

MIGRATIONS = Path(__file__).resolve().parents[2] / "backend" / "migrations"


@pytest.fixture
async def client_db(tmp_path):
    conn = await aiosqlite.connect(tmp_path / "t.db")
    await apply_migrations(conn, MIGRATIONS)

    class _Ctx:
        db = conn
        jobs_repo = JobsRepo()
        _running_jobs: dict[int, object] = {}
        settings = type("S", (), {"auth_backend": "dev", "dev_user_email": "dev@localhost"})()

    ctx = _Ctx()
    app.state.core_ctx = ctx
    app.state.live_ctx = None
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac, conn, ctx
    await conn.close()


@pytest.mark.asyncio
async def test_cancel_route_cancels_inflight_task_and_reconciles(client_db):
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

    ctx._running_jobs[job_id] = asyncio.create_task(_inflight())
    await started.wait()

    resp = await ac.post(f"/api/jobs/{job_id}/cancel")
    assert resp.status_code == 200
    assert resp.json()["status"] == "cancelled"

    await asyncio.sleep(0)  # let the cancellation propagate
    assert ctx._running_jobs[job_id].cancelled()
    assert (await ctx.jobs_repo.get_job(conn, job_id)).status == "cancelled"
    assert (await ctx.jobs_repo.list_items(conn, job_id))[0].status == "cancelled"
