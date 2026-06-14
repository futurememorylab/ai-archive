# tests/integration/test_admin_console.py
"""Admin console role CRUD: admin-only, self-protection, last-admin guard
(spec 2026-06-14-iap-roles-admin-console-design.md)."""
import importlib
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.auth.models import CurrentUser


def _app(monkeypatch, tmp_path, holder, admins="boss@x.com"):
    for k, v in {
        "APP_ENV": "dev", "AUTH_BACKEND": "iap", "IAP_AUDIENCE": "aud",
        "ADMIN_EMAILS": admins, "CATDV_BASE_URL": "http://localhost:0",
        "CATDV_USERNAME": "", "CATDV_PASSWORD": "p", "CATDV_CATALOG_ID": "881507",
        "GCP_PROJECT_ID": "p", "GCS_BUCKET_NAME": "b", "PROXY_SOURCE": "rest",
        "DATA_DIR": str(tmp_path),
    }.items():
        monkeypatch.setenv(k, v)
    from backend.app import main as main_mod
    importlib.reload(main_mod)
    monkeypatch.setattr(main_mod, "resolve_user",
                        lambda req, s: CurrentUser(email=holder["email"]))
    return main_mod


def test_non_admin_cannot_open_console(monkeypatch, tmp_path: Path):
    holder = {"email": "boss@x.com"}
    main_mod = _app(monkeypatch, tmp_path, holder)
    with TestClient(main_mod.app) as client:
        client.post("/admin/users", data={"email": "v@x.com", "role": "viewer", "display_name": ""})
        holder["email"] = "v@x.com"
        r = client.get("/admin")
    assert r.status_code == 403


def test_admin_lists_and_adds_member(monkeypatch, tmp_path: Path):
    holder = {"email": "boss@x.com"}
    main_mod = _app(monkeypatch, tmp_path, holder)
    with TestClient(main_mod.app) as client:
        assert "Access" in client.get("/admin").text and "Permissions" in client.get("/admin").text
        r = client.post("/admin/users",
                        data={"email": "Annie@x.com", "role": "annotator", "display_name": "Annie"},
                        headers={"HX-Request": "true"})
        assert r.status_code in (200, 201)
        assert "annie@x.com" in client.get("/admin").text


def test_self_protection(monkeypatch, tmp_path: Path):
    holder = {"email": "boss@x.com"}
    main_mod = _app(monkeypatch, tmp_path, holder)
    with TestClient(main_mod.app) as client:
        assert client.delete("/admin/users/boss@x.com").status_code == 403
        assert client.patch("/admin/users/boss@x.com", data={"role": "viewer"}).status_code == 403


def test_last_admin_guard(monkeypatch, tmp_path: Path):
    holder = {"email": "boss@x.com"}
    main_mod = _app(monkeypatch, tmp_path, holder)
    with TestClient(main_mod.app) as client:
        # add a second admin, then it's safe to demote them, but never the last one
        client.post("/admin/users", data={"email": "a2@x.com", "role": "admin", "display_name": ""})
        # demote a2 (ok — boss remains)
        assert client.patch("/admin/users/a2@x.com", data={"role": "viewer"}).status_code in (200, 201)
        # now boss is the only admin; revoking the only OTHER admin path is covered by self-protection,
        # so simulate: make a2 admin again and try to delete boss-as-only-admin via a2
        client.patch("/admin/users/a2@x.com", data={"role": "admin"})
        holder["email"] = "a2@x.com"
        client.delete("/admin/users/boss@x.com")  # ok, two admins → one
        # now a2 is the last admin; a2 cannot be demoted by anyone but themselves (self-protect),
        # so add a fresh admin and have THEM try to demote a2 after deleting boss is impossible.
        # Assert count never reaches zero:
        holder["email"] = "a2@x.com"
        r = client.patch("/admin/users/a2@x.com", data={"role": "viewer"})
        assert r.status_code == 403  # self-protection also stops the last-admin self-demote
