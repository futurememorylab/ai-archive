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


def test_create_job_lists_and_cancels(monkeypatch, tmp_path):
    _setenv(monkeypatch, tmp_path)
    from backend.app import main as main_mod

    importlib.reload(main_mod)
    app = main_mod.app

    with TestClient(app) as client:
        r = client.post(
            "/api/templates",
            json={
                "name": "t",
                "prompt": "p",
                "output_schema": {},
                "target_map": {"scenes": {"kind": "markers"}},
                "model": "m",
            },
        )
        tid = r.json()["id"]

        r = client.post(
            "/api/jobs", json={"template_id": tid, "clip_ids": [1, 2, 3], "auto_start": False}
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
