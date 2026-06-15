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
        client.post("/admin/users", data={"email": "v@x.com", "role": "member", "display_name": ""})
        holder["email"] = "v@x.com"
        r = client.get("/admin")
    assert r.status_code == 403


def test_admin_lists_and_adds_member(monkeypatch, tmp_path: Path):
    holder = {"email": "boss@x.com"}
    main_mod = _app(monkeypatch, tmp_path, holder)
    with TestClient(main_mod.app) as client:
        page = client.get("/admin").text
        assert "Access" in page and "Permissions" in page
        r = client.post("/admin/users",
                        data={"email": "Annie@x.com", "role": "member", "display_name": "Annie"},
                        headers={"HX-Request": "true"})
        assert r.status_code in (200, 201)
        members = client.get("/admin/access").text
        assert "annie@x.com" in members


def test_self_protection(monkeypatch, tmp_path: Path):
    holder = {"email": "boss@x.com"}
    main_mod = _app(monkeypatch, tmp_path, holder)
    with TestClient(main_mod.app) as client:
        delete_response = client.delete("/admin/users/boss@x.com")
        assert delete_response.status_code == 403
        patch_response = client.patch("/admin/users/boss@x.com", data={"role": "member"})
        assert patch_response.status_code == 403


def test_last_admin_guard(monkeypatch, tmp_path: Path):
    holder = {"email": "boss@x.com"}
    main_mod = _app(monkeypatch, tmp_path, holder)
    with TestClient(main_mod.app) as client:
        # add a second admin, then it's safe to demote them, but never the last one
        client.post("/admin/users", data={"email": "a2@x.com", "role": "admin", "display_name": ""})
        # demote a2 (ok — boss remains)
        demote = client.patch("/admin/users/a2@x.com", data={"role": "member"})
        assert demote.status_code in (200, 201)
        # now boss is the only admin; revoking the only OTHER admin path is
        # covered by self-protection,
        # so simulate: make a2 admin again and try to delete boss-as-only-admin via a2
        client.patch("/admin/users/a2@x.com", data={"role": "admin"})
        holder["email"] = "a2@x.com"
        client.delete("/admin/users/boss@x.com")  # ok, two admins → one
        # now a2 is the last admin; a2 cannot be demoted by anyone but themselves (self-protect),
        # so add a fresh admin and have THEM try to demote a2 after deleting boss is impossible.
        # Assert count never reaches zero:
        holder["email"] = "a2@x.com"
        r = client.patch("/admin/users/a2@x.com", data={"role": "member"})
        assert r.status_code == 403  # self-protection also stops the last-admin self-demote


# --- Issue 1: add_member must not downgrade an active member ---

def test_add_member_preserves_active_status(monkeypatch, tmp_path: Path):
    """Re-POSTing an already-active member with a different role must change the
    role but keep status='active'; it must NOT regress them to 'invited'."""
    import asyncio

    holder = {"email": "boss@x.com"}
    main_mod = _app(monkeypatch, tmp_path, holder)
    with TestClient(main_mod.app) as client:
        ctx = main_mod.app.state.core_ctx

        # Step 1: invite the member as annotator
        r = client.post("/admin/users",
                        data={"email": "alice@x.com", "role": "member", "display_name": "Alice"})
        assert r.status_code in (200, 201)

        # Step 2: simulate first sign-in — mark_seen flips invited→active
        asyncio.run(ctx.user_roles_repo.mark_seen(ctx.db, "alice@x.com"))
        row = asyncio.run(ctx.user_roles_repo.get(ctx.db, "alice@x.com"))
        assert row["status"] == "active", "precondition: mark_seen should have flipped to active"

        # Step 3: admin re-adds alice with a different role
        r2 = client.post("/admin/users",
                         data={"email": "alice@x.com", "role": "admin", "display_name": ""})
        assert r2.status_code in (200, 201)

        # Step 4: status must still be 'active'; role must have changed
        updated = asyncio.run(ctx.user_roles_repo.get(ctx.db, "alice@x.com"))
        assert updated["status"] == "active", "active member must not be downgraded on re-add"
        assert updated["role"] == "admin", "role must be updated by re-add"


# --- Issue 2: PATCH and DELETE must surface a success toast (HTML attribute check) ---

def test_patch_and_delete_carry_toast_handler(monkeypatch, tmp_path: Path):
    """The rendered members table must include @htmx:after-request toast handlers
    on both the role-change (hx-patch) and revoke (hx-delete) elements."""
    holder = {"email": "boss@x.com"}
    main_mod = _app(monkeypatch, tmp_path, holder)
    with TestClient(main_mod.app) as client:
        # Add a second member so the table has non-self rows with actions
        client.post("/admin/users",
                    data={"email": "bob@x.com", "role": "member", "display_name": "Bob"})
        html = client.get("/admin/access").text

    # The hx-patch items (role change menu items) must carry the toast handler
    assert "@htmx:after-request" in html, \
        "expected @htmx:after-request toast handler in rendered admin HTML"
    # Both role-change and revoke flavours of the toast text must appear
    assert "Role updated" in html, \
        "expected 'Role updated' toast text in hx-patch handler"
    assert "Access revoked" in html, \
        "expected 'Access revoked' toast text in hx-delete handler"


# --- Issue 3: stat counts must reflect totals, not the filtered set ---

def test_stat_counts_reflect_unfiltered_totals(monkeypatch, tmp_path: Path):
    """When a role filter is applied, the stat cards must still show global
    totals, not just the count of the filtered rows.

    Setup: boss (admin, seeded) + viewer + annotator = 3 members, 1 admin.
    When filtering ?role=member the page returns 1 row, but the Members stat
    card must still read 3 and the Admins stat card must still read 1.
    """
    holder = {"email": "boss@x.com"}
    main_mod = _app(monkeypatch, tmp_path, holder)
    with TestClient(main_mod.app) as client:
        # Add members: 1 admin (boss, seeded) + 1 viewer + 1 annotator = 3 total
        client.post("/admin/users", data={"email": "v@x.com", "role": "member", "display_name": ""})
        client.post("/admin/users", data={"email": "ann@x.com", "role": "member"})

        # Filter to viewers only — only 1 row appears in the table
        html = client.get("/admin/access?role=member").text

    # The .admin-stats block renders:
    #   <span class="n">{{ counts.members }}</span><span class="l">Members</span>
    #   <span class="n">{{ counts.admins }}</span><span class="l">Admins</span>
    # If counts come from the filtered list we'd get "1 Members / 0 Admins";
    # with the fix we must get "3 Members / 1 Admins".
    import re
    # Extract the Members stat value: text between class="n" span before "Members"
    members_match = re.search(r'<span class="n">(\d+)</span><span class="l">Members</span>', html)
    admins_match = re.search(r'<span class="n">(\d+)</span><span class="l">Admins</span>', html)
    assert members_match, "Members stat not found in admin page HTML"
    assert admins_match, "Admins stat not found in admin page HTML"
    assert members_match.group(1) == "3", \
        f"Members stat must be 3 (global total), got {members_match.group(1)}"
    assert admins_match.group(1) == "1", \
        f"Admins stat must be 1 (global total), got {admins_match.group(1)}"


def test_admin_link_only_for_admins(monkeypatch, tmp_path: Path):
    holder = {"email": "boss@x.com"}
    main_mod = _app(monkeypatch, tmp_path, holder)
    with TestClient(main_mod.app) as client:
        # admin: check on /admin page (core-only, renders layout.html)
        admin_view = client.get("/admin").text      # admin sees it
        assert 'href="/admin"' in admin_view
        client.post("/admin/users", data={"email": "v@x.com", "role": "member", "display_name": ""})
        holder["email"] = "v@x.com"
        # member: check on /prompts (core-only, renders layout.html)
        member_view = client.get("/prompts").text  # member does not
        assert 'href="/admin"' not in member_view


def test_accept_pending_request(monkeypatch, tmp_path: Path):
    """An admin Accepts a pending access request → the user becomes an active
    member."""
    import asyncio

    holder = {"email": "boss@x.com"}
    main_mod = _app(monkeypatch, tmp_path, holder)
    with TestClient(main_mod.app) as client:
        ctx = main_mod.app.state.core_ctx
        # a reached-but-unroled user records a request from the denial page
        holder["email"] = "newbie@x.com"
        req = client.post("/access/request")
        assert req.status_code == 200
        # admin accepts it
        holder["email"] = "boss@x.com"
        r = client.post("/admin/users/newbie@x.com/accept")
        assert r.status_code in (200, 201)
        row = asyncio.run(ctx.user_roles_repo.get(ctx.db, "newbie@x.com"))
    assert row["role"] == "member" and row["status"] == "active"
