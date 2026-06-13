"""Integration tests for /api/vpn routes.

In test/local config there is no WireGuard (vpn_managed=False, vpn_supervisor=None),
so status returns managed=False and enable/disable return 409.
"""

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


def test_status_unmanaged_returns_managed_false(monkeypatch, tmp_path):
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        r = client.get("/api/vpn/status")
    assert r.status_code == 200
    assert r.json()["managed"] is False


def test_enable_unmanaged_409(monkeypatch, tmp_path):
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        r = client.post("/api/vpn/enable")
    assert r.status_code == 409


def test_disable_unmanaged_409(monkeypatch, tmp_path):
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        r = client.post("/api/vpn/disable")
    assert r.status_code == 409


def test_retry_unmanaged_409(monkeypatch, tmp_path):
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        r = client.post("/api/vpn/retry")
    assert r.status_code == 409
