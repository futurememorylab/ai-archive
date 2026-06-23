import importlib

from fastapi.testclient import TestClient


def _setenv(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("CATDV_BASE_URL", "http://localhost:0")
    monkeypatch.setenv("CATDV_USERNAME", "")
    monkeypatch.setenv("CATDV_PASSWORD", "p")
    monkeypatch.setenv("CATDV_CATALOG_ID", "881507")
    monkeypatch.setenv("GCP_PROJECT_ID", "p")
    monkeypatch.setenv("GCS_BUCKET_NAME", "b")
    monkeypatch.setenv("PROXY_SOURCE", "rest")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))


def _make_app(monkeypatch, tmp_path):
    _setenv(monkeypatch, tmp_path)
    from backend.app import main as main_mod

    importlib.reload(main_mod)
    return main_mod.app


def _flatten_paths(app):
    """Collect every effective route path from the app, in match order.

    FastAPI < 0.137 eagerly flattens included routers into ``app.routes`` as
    ``APIRoute`` objects carrying a ``.path``. FastAPI >= 0.137 instead stores
    lazy ``_IncludedRouter`` placeholders that resolve their child routes on
    demand via ``effective_candidates()`` (path resolution is deferred to
    match time). Walk both shapes so the route-ordering guard below stays
    independent of the installed FastAPI version.
    """
    paths: list[str] = []

    def visit(routes):
        for route in routes:
            candidates = getattr(route, "effective_candidates", None)
            if callable(candidates):  # FastAPI >= 0.137 lazy include placeholder
                visit(candidates())
            else:
                path = getattr(route, "path", None)
                if path is not None:
                    paths.append(path)

    visit(app.routes)
    return paths


def test_active_jobs_lists_running_with_progress(monkeypatch, tmp_path):
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        ctx = client.app.state.core_ctx

        async def seed():
            from backend.app.repositories.prompts import PromptsRepo

            _, vid = await PromptsRepo().create_with_initial_version(
                ctx.db, name="t", description=None, body="p",
                target_map={"x": {"kind": "markers"}}, output_schema={}, model="m",
            )
            jid = await ctx.jobs_repo.create_job(
                ctx.db, prompt_version_id=vid, clip_ids=[1, 2], kind="video"
            )
            await ctx.jobs_repo.update_status(ctx.db, jid, "running")
            return jid

        jid = client.portal.call(seed)

        r = client.get("/api/jobs/active")

    assert r.status_code == 200
    body = r.json()
    assert len(body) == 1
    assert body[0]["id"] == jid
    assert body[0]["total"] == 2
    assert body[0]["done"] == 0
    assert body[0]["errors"] == 0
    assert body[0]["kind"] == "video"
    assert body[0]["status"] == "running"
    assert "run_group" in body[0]
    assert body[0]["run_group"] is None  # no run_group was set


def test_create_job_reports_started_flag(monkeypatch, tmp_path):
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        ctx = client.app.state.core_ctx

        async def seed_version():
            from backend.app.repositories.prompts import PromptsRepo
            _, vid = await PromptsRepo().create_with_initial_version(
                ctx.db, name="t", description=None, body="p",
                target_map={"x": {"kind": "markers"}}, output_schema={}, model="m",
            )
            return vid

        vid = client.portal.call(seed_version)
        r = client.post("/api/jobs", json={"prompt_version_id": vid, "clip_ids": [1], "auto_start": True})

    assert r.status_code == 201
    body = r.json()
    assert "id" in body
    assert isinstance(body["started"], bool)


def test_active_jobs_exposes_run_group_for_calibration_jobs(monkeypatch, tmp_path):
    """run_group is included in /api/jobs/active so the topbar pill can
    identify calibration jobs (run_group starting 'calibration:')."""
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        ctx = client.app.state.core_ctx

        async def seed():
            from backend.app.repositories.prompts import PromptsRepo

            _, vid = await PromptsRepo().create_with_initial_version(
                ctx.db, name="cal", description=None, body="p",
                target_map={"x": {"kind": "markers"}}, output_schema={}, model="m",
            )
            jid = await ctx.jobs_repo.create_job(
                ctx.db,
                prompt_version_id=vid,
                clip_ids=[10],
                run_group="calibration:5:42",
            )
            await ctx.jobs_repo.update_status(ctx.db, jid, "running")
            return jid

        jid = client.portal.call(seed)

        r = client.get("/api/jobs/active")

    assert r.status_code == 200
    body = r.json()
    assert len(body) == 1
    assert body[0]["id"] == jid
    assert body[0]["run_group"] == "calibration:5:42"


def test_jobs_events_route_resolves_before_job_id(monkeypatch, tmp_path):
    """Regression: /api/jobs/events must not be shadowed by /api/jobs/{job_id}."""
    app = _make_app(monkeypatch, tmp_path)
    paths = _flatten_paths(app)
    assert "/api/jobs/events" in paths, "/api/jobs/events route not registered"
    assert "/api/jobs/active" in paths, "/api/jobs/active route not registered"
    # Find the /{job_id} catch-all — it's the one containing {job_id} but not
    # also containing /events (that's the per-job stream, not the global one).
    catch_all_paths = [
        p for p in paths
        if p is not None and "{job_id}" in p and not p.endswith("/events")
    ]
    assert catch_all_paths, "No /api/jobs/{job_id} route found"
    i_events = paths.index("/api/jobs/events")
    i_jobid = paths.index(catch_all_paths[0])
    assert i_events < i_jobid, (
        f"/api/jobs/events (index {i_events}) is registered AFTER "
        f"{catch_all_paths[0]} (index {i_jobid}) — it will be shadowed"
    )
