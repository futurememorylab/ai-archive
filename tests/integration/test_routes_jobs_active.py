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


def test_active_jobs_lists_running_with_progress(monkeypatch, tmp_path):
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        ctx = client.app.state.ctx

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
