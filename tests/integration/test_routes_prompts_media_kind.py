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


def _client(monkeypatch, tmp_path) -> TestClient:
    _setenv(monkeypatch, tmp_path)
    from backend.app import main as main_mod

    importlib.reload(main_mod)
    return TestClient(main_mod.app)


def test_create_with_media_kind_and_list_returns_it(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as client:
        r = client.post("/api/prompts", json={
            "name": "img-prompt", "description": None, "body": "b",
            "target_map": {"summary_cz": {"kind": "note", "target": "t"}},
            "output_schema": {}, "model": "gemini-2.5-pro", "media_kind": "image",
        })
        assert r.status_code == 201
        pid = r.json()["id"]
        rows = {p["id"]: p for p in client.get("/api/prompts?archived=0").json()}
        assert rows[pid]["media_kind"] == "image"


def test_patch_media_kind(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as client:
        r = client.post("/api/prompts", json={
            "name": "p2", "description": None, "body": "b",
            "target_map": {}, "output_schema": {}, "model": "gemini-2.5-pro",
        })
        pid = r.json()["id"]
        assert client.patch(
            f"/api/prompts/{pid}", json={"media_kind": "video"}
        ).status_code == 200
        assert client.get(f"/api/prompts/{pid}").json()["media_kind"] == "video"
