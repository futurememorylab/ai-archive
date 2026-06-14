"""PR2a — the resolved identity surfaces in the topbar ("signed in as …").

A request-scoped middleware resolves the current user via the auth seam and
the layout renders it. In the dev backend that's the configured
``dev_user_email``; the assertion here is that it reaches a rendered layout
page (``/prompts`` renders with core ctx only, so it works offline).
"""

import importlib
from pathlib import Path

from fastapi.testclient import TestClient


def _make_app(monkeypatch, tmp_path, **env):
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("CATDV_BASE_URL", "http://localhost:0")
    monkeypatch.setenv("CATDV_USERNAME", "")
    monkeypatch.setenv("CATDV_PASSWORD", "p")
    monkeypatch.setenv("CATDV_CATALOG_ID", "881507")
    monkeypatch.setenv("GCP_PROJECT_ID", "p")
    monkeypatch.setenv("GCS_BUCKET_NAME", "b")
    monkeypatch.setenv("PROXY_SOURCE", "rest")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    from backend.app import main as main_mod

    importlib.reload(main_mod)
    return main_mod.app


def test_topbar_shows_dev_user_email(monkeypatch, tmp_path: Path):
    app = _make_app(monkeypatch, tmp_path, DEV_USER_EMAIL="alice@studio.test")
    with TestClient(app) as client:
        r = client.get("/prompts")
    assert r.status_code == 200
    assert "alice@studio.test" in r.text
