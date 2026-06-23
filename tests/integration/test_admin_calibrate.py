"""Admin calibrate launch + projected-cost endpoints (PR4a A4).

The Calibrate dialog (3 clips → sweep of 3 resolutions × 2 repeats = 6
jobs, each telemetry-only) is launched by POST
/admin/prompts/{vid}/calibrate. A sibling advisory endpoint
/admin/prompts/{vid}/calibrate/estimate returns a projected cost.

Harness: the same on-disk reload-and-seed pattern as
test_admin_prompts_tab.py. The launch route needs a LiveCtx — the
in-process test app boots offline (app.state.live_ctx is None), so the
live-required tests set a minimal stub on app.state.live_ctx AND
monkeypatch start_job_in_background to a no-op recorder. That cleanly
exercises the orchestration (6 jobs, 3 distinct resolutions × 2,
record_only=True) without running 18 real background tasks.
"""

import asyncio
import importlib

from fastapi.testclient import TestClient


def _client(monkeypatch, tmp_path) -> TestClient:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from backend.app import main as main_mod

    importlib.reload(main_mod)
    return TestClient(main_mod.app)


async def _seed_prompt(db_path) -> int:
    """Insert one prompt + version; return the version_id."""
    import aiosqlite

    from backend.app.repositories.prompts import PromptsRepo

    async with aiosqlite.connect(db_path) as conn:
        repo = PromptsRepo()
        _pid, vid = await repo.create_with_initial_version(
            conn,
            name="CalPrompt",
            description="test prompt",
            body="Identify scenes.",
            target_map={"scenes": {"kind": "markers"}},
            output_schema={"type": "object"},
            model="gemini-2.5-flash-lite",
            media_resolution="low",
        )
        return vid


class _LiveStub:
    """Minimal stand-in for LiveCtx — start_job_in_background is
    monkeypatched, so the stub is never dereferenced beyond the
    `is None` check in the route."""


def test_calibrate_offline_503(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as client:
        vid = asyncio.run(_seed_prompt(tmp_path / "app.db"))
        client.app.state.live_ctx = None
        r = client.post(
            f"/admin/prompts/{vid}/calibrate",
            data={"clip_ids": ["1", "2", "3"]},
        )
        assert r.status_code == 503


def test_calibrate_requires_three_clips(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as client:
        vid = asyncio.run(_seed_prompt(tmp_path / "app.db"))
        client.app.state.live_ctx = _LiveStub()
        r = client.post(
            f"/admin/prompts/{vid}/calibrate",
            data={"clip_ids": ["1", "2"]},
        )
        assert r.status_code == 422


def test_calibrate_unknown_version_404(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as client:
        asyncio.run(_seed_prompt(tmp_path / "app.db"))
        client.app.state.live_ctx = _LiveStub()
        r = client.post(
            "/admin/prompts/999999/calibrate",
            data={"clip_ids": ["1", "2", "3"]},
        )
        assert r.status_code == 404


def test_calibrate_creates_six_jobs(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as client:
        vid = asyncio.run(_seed_prompt(tmp_path / "app.db"))
        client.app.state.live_ctx = _LiveStub()

        calls: list[dict] = []

        from backend.app.routes.pages import admin as admin_mod

        def _recorder(core, live, job_id, **kw):
            calls.append({"job_id": job_id, **kw})

        monkeypatch.setattr(admin_mod, "start_job_in_background", _recorder)

        r = client.post(
            f"/admin/prompts/{vid}/calibrate",
            data={"clip_ids": ["11", "22", "33"]},
        )
        assert r.status_code == 200

        # Orchestration: 6 launches — each resolution twice, record_only=True.
        assert len(calls) == 6
        resolutions = sorted(c["force_resolution"] for c in calls)
        assert resolutions == ["high", "high", "low", "low", "medium", "medium"]
        assert all(c["record_only"] is True for c in calls)

        # 6 jobs persisted with a calibration:<vid>:<ts> run_group, each 3 items.
        from backend.app.repositories.jobs import JobsRepo
        import aiosqlite

        async def _check():
            async with aiosqlite.connect(tmp_path / "app.db") as conn:
                cur = await conn.execute(
                    "SELECT id FROM jobs WHERE run_group LIKE 'calibration:%'"
                )
                job_ids = [row[0] for row in await cur.fetchall()]
                assert len(job_ids) == 6
                repo = JobsRepo()
                for jid in job_ids:
                    items = await repo.list_items(conn, jid)
                    assert len(items) == 3

        asyncio.run(_check())


def test_calibrate_estimate_returns_projected_cost(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as client:
        vid = asyncio.run(_seed_prompt(tmp_path / "app.db"))
        r = client.post(
            f"/admin/prompts/{vid}/calibrate/estimate",
            json={"clip_ids": [11, 22, 33]},
        )
        assert r.status_code == 200
        body = r.json()
        assert "projected_cost_usd" in body  # number or null
        assert body["runs"] == 18  # 3 res × 2 repeats × 3 clips


def test_calibrate_estimate_empty_clip_ids(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as client:
        vid = asyncio.run(_seed_prompt(tmp_path / "app.db"))
        r = client.post(
            f"/admin/prompts/{vid}/calibrate/estimate",
            json={"clip_ids": []},
        )
        assert r.status_code == 200
        body = r.json()
        assert body == {"projected_cost_usd": None, "runs": 0}
