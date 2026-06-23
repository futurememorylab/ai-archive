"""Spec 2026-06-23-annotate-cache-queue-consistency §2: GET
/api/jobs/active-for-clip/{clip_id} lets the clip page resume the annotate
button after a reload by reporting the running job (if any) for that clip."""
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


def test_active_for_clip_reports_running_job_and_status(monkeypatch, tmp_path):
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
                ctx.db, prompt_version_id=vid, clip_ids=[314], kind="video"
            )
            await ctx.jobs_repo.update_status(ctx.db, jid, "running")
            items = await ctx.jobs_repo.list_items(ctx.db, jid)
            await ctx.jobs_repo.update_item_status(ctx.db, items[0].id, "uploading")
            return jid

        jid = client.portal.call(seed)

        hit = client.get("/api/jobs/active-for-clip/314")
        miss = client.get("/api/jobs/active-for-clip/999")

    assert hit.status_code == 200
    body = hit.json()
    assert body["job_id"] == jid
    assert body["item_status"] == "uploading"
    assert body["started_at"]  # lets the clip page resume the elapsed timer
    assert miss.status_code == 200
    assert miss.json() == {}
