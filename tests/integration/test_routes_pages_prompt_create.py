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


def test_create_form_persists_media_kind(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as client:
        r = client.post(
            "/prompts/_create",
            data={
                "name": "ui-image-prompt", "description": "", "body": "b",
                "target_map": "{}", "output_schema": "{}",
                "model": "gemini-2.5-pro", "media_kind": "image",
            },
            follow_redirects=False,
        )
        assert r.status_code == 303
        rows = client.get("/api/prompts?archived=0").json()
        created = next(p for p in rows if p["name"] == "ui-image-prompt")
        assert created["media_kind"] == "image"
