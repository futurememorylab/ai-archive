import importlib

from fastapi.testclient import TestClient


def test_templates_crud_lifecycle(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("CATDV_BASE_URL", "http://localhost:0")
    monkeypatch.setenv("CATDV_USERNAME", "")
    monkeypatch.setenv("CATDV_PASSWORD", "p")
    monkeypatch.setenv("CATDV_CATALOG_ID", "881507")
    monkeypatch.setenv("GCP_PROJECT_ID", "p")
    monkeypatch.setenv("GCS_BUCKET_NAME", "b")
    monkeypatch.setenv("PROXY_SOURCE", "rest")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))

    from backend.app import main as main_mod
    importlib.reload(main_mod)
    app = main_mod.app

    with TestClient(app) as client:
        r = client.get("/api/templates")
        assert r.status_code == 200
        assert r.json() == []

        r = client.post("/api/templates", json={
            "name": "scene-markers",
            "prompt": "describe scenes",
            "output_schema": {"type": "object"},
            "target_map": {"scenes": {"kind": "markers"}},
            "model": "gemini-2.5-pro",
        })
        assert r.status_code == 201
        new_id = r.json()["id"]
        assert new_id > 0

        r = client.get(f"/api/templates/{new_id}")
        assert r.status_code == 200
        assert r.json()["name"] == "scene-markers"

        r = client.get("/api/templates")
        assert len(r.json()) == 1

        r = client.delete(f"/api/templates/{new_id}")
        assert r.status_code == 204

        r = client.get("/api/templates")
        assert r.json() == []
