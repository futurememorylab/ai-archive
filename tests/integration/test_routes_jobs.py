import asyncio
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


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def test_create_job_lists_and_cancels(monkeypatch, tmp_path):
    _setenv(monkeypatch, tmp_path)
    from backend.app import main as main_mod

    importlib.reload(main_mod)
    app = main_mod.app

    with TestClient(app) as client:
        ctx = client.app.state.core_ctx
        # Seed a prompt + version via PromptsRepo directly — the prompts
        # REST API arrives in a later task.
        _, vid = _run(
            ctx.prompts_repo.create_with_initial_version(
                ctx.db,
                name="t",
                description=None,
                body="p",
                target_map={"scenes": {"kind": "markers"}},
                output_schema={},
                model="m",
            )
        )

        r = client.post(
            "/api/jobs",
            json={"prompt_version_id": vid, "clip_ids": [1, 2, 3]},
        )
        assert r.status_code == 201
        job_id = r.json()["id"]

        r = client.get("/api/jobs")
        assert any(j["id"] == job_id for j in r.json())

        r = client.get(f"/api/jobs/{job_id}")
        assert r.json()["total_clips"] == 3
        assert len(r.json()["items"]) == 3

        r = client.post(f"/api/jobs/{job_id}/cancel")
        assert r.status_code == 200
        r = client.get(f"/api/jobs/{job_id}")
        assert r.json()["status"] == "cancelled"
