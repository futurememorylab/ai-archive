# tests/integration/test_auth_gate.py
"""The auth gate: under AUTH_BACKEND=iap, no active role → 403 + the access
page; allow-listed paths stay reachable; an admin (seeded via ADMIN_EMAILS)
gets through. Fail-closed (spec 2026-06-14-iap-roles-admin-console-design.md).

We patch main.resolve_user so we don't have to forge a signed IAP JWT; the
gate logic (role lookup, allow-list, deny) is what's under test."""
import importlib
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.archive.model import ClipPage
from backend.app.auth.models import CurrentUser
from tests._helpers.live_ctx import install_live_ctx


class _EmptyArchive:
    """Minimal archive fake: an empty clip page renders the clips list at 200
    without needing CatDV. Lets us prove the admin passes the gate AND reaches
    a real authorized page (not just dodges the 403)."""

    async def list_clips(self, catalog, query):
        return ClipPage(items=(), total=0, offset=query.offset, limit=query.limit)


def _make_app(monkeypatch, tmp_path, *, admin_emails="boss@x.com"):
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("AUTH_BACKEND", "iap")
    monkeypatch.setenv("IAP_AUDIENCE", "test-aud")
    monkeypatch.setenv("ADMIN_EMAILS", admin_emails)
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
    return main_mod


def test_unroled_user_is_denied_with_access_page(monkeypatch, tmp_path: Path):
    main_mod = _make_app(monkeypatch, tmp_path)
    monkeypatch.setattr(main_mod, "resolve_user",
                        lambda req, s: CurrentUser(email="nobody@x.com"))
    with TestClient(main_mod.app) as client:
        r = client.get("/", follow_redirects=False)
    assert r.status_code == 403
    assert "No access" in r.text or "Access not granted" in r.text


def test_allowlisted_paths_reachable_without_role(monkeypatch, tmp_path: Path):
    main_mod = _make_app(monkeypatch, tmp_path)
    monkeypatch.setattr(main_mod, "resolve_user",
                        lambda req, s: CurrentUser(email="nobody@x.com"))
    with TestClient(main_mod.app) as client:
        assert client.get("/api/health").status_code == 200
        assert client.get("/access").status_code == 200


def test_seeded_admin_gets_through(monkeypatch, tmp_path: Path):
    main_mod = _make_app(monkeypatch, tmp_path)
    monkeypatch.setattr(main_mod, "resolve_user",
                        lambda req, s: CurrentUser(email="boss@x.com"))
    with TestClient(main_mod.app) as client:
        # The clips list route needs a live ctx; supply a fake archive so the
        # page renders 200 once the gate admits the seeded admin (tests boot
        # offline, so without this `/` is 503 regardless of auth).
        install_live_ctx(client.app, archive=_EmptyArchive())
        r = client.get("/")
    assert r.status_code == 200


def test_json_caller_gets_json_403(monkeypatch, tmp_path: Path):
    main_mod = _make_app(monkeypatch, tmp_path)
    monkeypatch.setattr(main_mod, "resolve_user",
                        lambda req, s: CurrentUser(email="nobody@x.com"))
    with TestClient(main_mod.app) as client:
        r = client.get("/", headers={"HX-Request": "true"})
    assert r.status_code == 403
    assert r.json()["detail"]
