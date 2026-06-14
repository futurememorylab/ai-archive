"""The IAP access-control pages — the app-rendered states (3 "Access not
granted", 4 "Error") of the login design (spec
2026-06-13-iap-access-control-design.md).

Under IAP, Google owns the sign-in + redirect states; the app only renders
the denial and error cards, standalone (no nav rail / topbar — an unauthorized
user must not see the app chrome).
"""

import importlib
from pathlib import Path

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


def test_access_denied_renders(monkeypatch, tmp_path: Path):
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        r = client.get("/access?state=denied&email=f.miller@gmail.com")
    assert r.status_code == 200
    assert "Access not granted" in r.text
    assert "Archive AI" in r.text
    assert "f.miller@gmail.com" in r.text
    # "Use a different account" → IAP cookie clear (re-auth as someone else)
    assert "Use a different account" in r.text
    assert "gcp-iap-mode=CLEAR_LOGIN_COOKIE" in r.text
    assert "Contact your workspace admin" in r.text
    # Standalone — no app chrome for an unauthorized user.
    assert '<aside class="rail">' not in r.text


def test_access_error_renders(monkeypatch, tmp_path: Path):
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        r = client.get("/access?state=error")
    assert r.status_code == 200
    assert "Couldn't sign you in" in r.text
    assert "Try again" in r.text
    assert "auth_failed" in r.text
    assert '<aside class="rail">' not in r.text


def test_access_defaults_to_denied(monkeypatch, tmp_path: Path):
    """An unknown/missing state falls back to the denial card, never error."""
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        r = client.get("/access")
    assert r.status_code == 200
    assert "Access not granted" in r.text
