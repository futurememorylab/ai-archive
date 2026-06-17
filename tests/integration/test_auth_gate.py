# tests/integration/test_auth_gate.py
"""The auth gate: under AUTH_BACKEND=iap, no active role → 403 + the access
page; allow-listed paths stay reachable; an admin (seeded via ADMIN_EMAILS)
gets through. Fail-closed (spec 2026-06-14-iap-roles-admin-console-design.md).

We patch main.resolve_user so we don't have to forge a signed IAP JWT; the
gate logic (role lookup, allow-list, deny) is what's under test."""
import asyncio
import importlib
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.archive.model import ClipPage
from backend.app.auth.models import CurrentUser
from backend.app.repositories.user_roles import UserRolesRepo
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
        health = client.get("/api/health")
        assert health.status_code == 200
        access = client.get("/access")
        assert access.status_code == 200


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


def test_active_user_browse_does_no_db_write(monkeypatch, tmp_path: Path):
    """Issue #73: an already-active user browsing must NOT trigger an auth-path
    DB write/commit. With last-seen tracking gone, the only reason to write is
    the one-time invited→active flip — so the gate reads `(role, status)` and
    writes ONLY when status=='invited'. An active user hits the gate read-only.
    The old code ran an UPDATE + commit() on every browse (no-op or not), which
    kept poking the SQLite connection Litestream was checkpointing — the lock
    contention that crashed the container."""
    main_mod = _make_app(monkeypatch, tmp_path)  # boss@x.com seeded active admin
    monkeypatch.setattr(main_mod, "resolve_user",
                        lambda req, s: CurrentUser(email="boss@x.com"))
    writes: list[str] = []
    orig = UserRolesRepo.activate_on_first_sight

    async def _spy(self, conn, email):
        writes.append(email)
        return await orig(self, conn, email)

    monkeypatch.setattr(UserRolesRepo, "activate_on_first_sight", _spy)
    with TestClient(main_mod.app) as client:
        install_live_ctx(client.app, archive=_EmptyArchive())
        assert client.get("/").status_code == 200
        assert client.get("/").status_code == 200
    assert writes == [], f"active user must not trigger an activation write; got {writes}"


def test_invited_user_is_activated_once_then_stays_read_only(monkeypatch, tmp_path: Path):
    """An invited user flips to active on first authenticated sight (exactly one
    write); every later request is read-only, because the flip is gated on
    status=='invited'."""
    main_mod = _make_app(monkeypatch, tmp_path)
    monkeypatch.setattr(main_mod, "resolve_user",
                        lambda req, s: CurrentUser(email="inv@x.com"))
    writes: list[str] = []
    orig = UserRolesRepo.activate_on_first_sight

    async def _spy(self, conn, email):
        writes.append(email)
        return await orig(self, conn, email)

    monkeypatch.setattr(UserRolesRepo, "activate_on_first_sight", _spy)
    with TestClient(main_mod.app) as client:
        install_live_ctx(client.app, archive=_EmptyArchive())
        ctx = main_mod.app.state.core_ctx
        asyncio.run(ctx.user_roles_repo.upsert_role(
            ctx.db, "inv@x.com", "member", status="invited", granted_by="boss@x.com"))
        assert client.get("/").status_code == 200   # first sight → flip
        assert client.get("/").status_code == 200   # now active → no write
        row = asyncio.run(ctx.user_roles_repo.get(ctx.db, "inv@x.com"))
    assert writes == ["inv@x.com"], f"expected exactly one activation write; got {writes}"
    assert row["status"] == "active"


def test_activate_on_first_sight_db_lock_does_not_500_the_request(monkeypatch, tmp_path: Path):
    """`activate_on_first_sight` is best-effort: if the (gated) invited→active
    write hits a transient `database is locked`, the request must still be
    served — the invited user still admits and flips on a later request.

    Regression guard for the prod outage on 2026-06-17: an unguarded write
    turned a transient SQLite write-lock into a 500 on every authenticated
    request, including `GET /`."""
    main_mod = _make_app(monkeypatch, tmp_path)
    monkeypatch.setattr(main_mod, "resolve_user",
                        lambda req, s: CurrentUser(email="inv@x.com"))

    async def _locked(self, conn, email):
        raise sqlite3.OperationalError("database is locked")

    with TestClient(main_mod.app) as client:
        install_live_ctx(client.app, archive=_EmptyArchive())
        ctx = main_mod.app.state.core_ctx
        # Seed an invited user so the gate actually attempts the flip write.
        asyncio.run(ctx.user_roles_repo.upsert_role(
            ctx.db, "inv@x.com", "member", status="invited", granted_by="boss@x.com"))
        monkeypatch.setattr(UserRolesRepo, "activate_on_first_sight", _locked)
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
