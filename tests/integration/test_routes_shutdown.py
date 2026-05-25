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


def _patch_trigger(monkeypatch):
    calls = []
    import backend.app.routes.connection as conn_mod

    monkeypatch.setattr(conn_mod, "schedule_graceful_shutdown", lambda *a, **k: calls.append(True))
    return calls


def test_shutdown_returns_screen_and_fires_trigger(monkeypatch, tmp_path):
    monkeypatch.delenv("DEV_RELOAD", raising=False)
    app = _make_app(monkeypatch, tmp_path)
    calls = _patch_trigger(monkeypatch)
    with TestClient(app) as client:
        r = client.post("/api/connection/shutdown")
    assert r.status_code == 200
    assert "Shutting down" in r.text
    assert calls == [True]


def test_shutdown_refused_in_reload_mode(monkeypatch, tmp_path):
    monkeypatch.setenv("DEV_RELOAD", "1")
    app = _make_app(monkeypatch, tmp_path)
    calls = _patch_trigger(monkeypatch)
    with TestClient(app) as client:
        r = client.post("/api/connection/shutdown")
    assert r.status_code == 409
    assert calls == []
