# IAP Roles + Admin Console Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the already-verified IAP identity into app-side **roles**, gate the whole app **fail-closed** on them, enforce **Annotator-required** AI runs, and ship the **admin console** + **access-request** page from the `admin-panel-ai-archive` design handoff.

**Architecture:** Google IAP owns the gate (who reaches the app); a new SQLite `user_roles` table owns authorization (what a reached user may do). A single app-wide **default-deny middleware** enforces "must have an active role" with a tiny allow-list; small route helpers enforce `admin` (console) and `run` (AI). The admin console + access page are server-rendered with the existing shared UI library (`_ui.html`), HTMX, and the `Alpine.store('toast')` pattern — never the prototype's inline styles.

**Tech Stack:** FastAPI + Starlette middleware, `aiosqlite`, pydantic-settings, Jinja2 (`backend/app/routes/pages/templates.py`), Alpine/HTMX, the `.btn` / `ui.modal` / `ui.menu` / `ui.field` / `ui.status_pill` / `ui.page_header` components, design tokens in `static/app.css`.

**Spec:** `docs/specs/2026-06-14-iap-roles-admin-console-design.md` (read it first).

---

## Conventions used throughout this plan

- **Run tests** with the repo's pytest. From the repo root: `python -m pytest <path> -q` (use the project's 3.12/3.13 venv — Python 3.14 venvs are broken on this machine per CLAUDE.md; the existing `.pyc` files show 3.14 was used, so confirm the interpreter the suite already runs under and reuse it).
- **Commit** after each task's tests are green. Use `feat:` / `test:` / `docs:` prefixes and end every commit body with:
  `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`
- **TDD**: every task is red → green → commit. Show the failing run first.
- **Branch**: stay on `feat/iap-access-control` (already checked out). Do **not** sweep the previous agent's existing uncommitted PR1/PR2a changes into your commits — `git add` only the exact files each step names.
- **No `location.reload()`**, no `alert()`, no silent `.catch()` (CLAUDE.md frontend rules). CRUD endpoints return HTMX partials on `HX-Request: true`.

---

## File structure (what gets created / modified)

**Created**
- `backend/migrations/0020_user_roles.sql` — the table (verify `0020` is the next free number; the runner refuses sentinel collisions, ADR 0044).
- `backend/app/repositories/user_roles.py` — `UserRolesRepo` (leaf; raw SQL over `aiosqlite`).
- `backend/app/auth/roles.py` — `ROLE_CAPS`, `ROLE_ORDER`, `ROLE_META`, `CAP_ORDER`, `has_permission()`.
- `backend/app/auth/guards.py` — `require_role()` / `require_permission()` route helpers.
- `backend/app/routes/pages/admin.py` — the admin console route + role CRUD.
- `backend/app/templates/pages/admin.html` — the console page (extends `layout.html`).
- `backend/app/templates/pages/_admin_members.html` — the HTMX members-table partial.
- `backend/app/templates/pages/_perm_dots.html` — the V·P·A·M dots partial.
- Tests: `tests/unit/test_roles_caps.py`, `tests/unit/test_settings_admin_emails.py`, `tests/integration/test_user_roles_repo.py`, `tests/integration/test_auth_gate.py`, `tests/integration/test_run_permission.py`, `tests/integration/test_admin_console.py`, `tests/integration/test_access_request.py`.

**Modified**
- `backend/app/settings.py` — `admin_emails` + `admin_email_list`.
- `backend/app/auth/models.py` — `CurrentUser` gains `permissions` / `has()` / `is_admin`.
- `backend/app/context.py` — register `user_roles_repo` on `CoreCtx` + `LiveCtx` delegator; seed admins in `CoreCtx.build`.
- `backend/app/main.py` — replace the `_attach_current_user` middleware with the `_auth_gate` middleware.
- `backend/app/routes/jobs.py`, `backend/app/routes/studio.py`, `backend/app/routes/live.py` — `require_permission(request, "run")` on the three AI-run surfaces.
- `backend/app/routes/pages/__init__.py` — register the admin router.
- `backend/app/routes/pages/access.py` + `templates/pages/access.html` — identity card + request-access flow + rebrand.
- `backend/app/templates/pages/layout.html` — admin-only topbar link.
- `backend/app/static/app.css` — role pills, perm dots, admin table, access identity card (tokens only).

**Deviation from spec (note it):** the spec listed `POST /sync/run` as an AI-run surface. On inspection that endpoint (`sync.py::run_drain`) drains the **writeback** queue to CatDV — that is a *publish* action, deferred to the publish fast-follow, not an AI run. The genuine AI-run surfaces are `POST /api/jobs` (annotate), `POST /api/studio/runs` (studio), and `GET /live/session-config` (mints the Gemini-Live token). This plan gates those three. Update the spec's enforcement list to match in Task 16's commit.

---

# PHASE 1 — PR2b: roles + gate + AI-run enforcement

## Task 1: Settings — `admin_emails`

**Files:**
- Modify: `backend/app/settings.py`
- Test: `tests/unit/test_settings_admin_emails.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_settings_admin_emails.py
"""ADMIN_EMAILS is a comma-separated env string parsed into a normalized list
(lowercased, trimmed, de-duped) — the deploy-time root of trust for the first
admins (spec 2026-06-14-iap-roles-admin-console-design.md)."""
import pytest
from pydantic import ValidationError

from backend.app.settings import Settings


def _settings(**over) -> Settings:
    base = dict(
        catdv_base_url="http://localhost:0",
        catdv_catalog_id=881507,
        gcp_project_id="p",
        gcs_bucket_name="b",
    )
    base.update(over)
    return Settings(**base)


def test_admin_email_list_empty_by_default():
    assert _settings().admin_email_list == []


def test_admin_email_list_parses_and_normalizes():
    s = _settings(admin_emails="  Maya@X.com , elena@x.com ,maya@x.com")
    assert s.admin_email_list == ["maya@x.com", "elena@x.com"]


def test_prod_refuses_non_iap_backend():
    """Cloud (app_env=prod) must run gated: refuse to boot with the dev backend,
    which would treat every IAP-admitted user as implicit admin."""
    with pytest.raises(ValidationError):
        _settings(app_env="prod", auth_backend="dev")
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/unit/test_settings_admin_emails.py -q`
Expected: FAIL — `AttributeError: 'Settings' object has no attribute 'admin_email_list'`.

- [ ] **Step 3: Implement**

In `backend/app/settings.py`, add the field next to the other auth fields (after `iap_audience`, ~line 32):

```python
    # Comma-separated deploy-time list of emails seeded as admins at startup
    # (idempotently). The root of trust: the first admin(s) exist before the
    # admin console does, so no one can self-promote from inside the app.
    # See ADR 0081 / spec 2026-06-14-iap-roles-admin-console-design.md.
    admin_emails: str = ""
```

Add the property next to `vpn_managed` (after line 145):

```python
    @property
    def admin_email_list(self) -> list[str]:
        """ADMIN_EMAILS parsed: trimmed, lowercased, de-duped, order-preserved."""
        seen: dict[str, None] = {}
        for raw in self.admin_emails.split(","):
            e = raw.strip().lower()
            if e and e not in seen:
                seen[e] = None
        return list(seen)
```

Add a cross-field validator next to the existing `_validate_fs_archive` (~line 128) so the cloud can never accidentally run **ungated** — `app_env=prod` must use the IAP backend, otherwise every IAP-admitted user would be treated as implicit admin (the dev shortcut):

```python
    @model_validator(mode="after")
    def _validate_prod_auth(self) -> "Settings":
        if self.app_env == "prod" and self.auth_backend != "iap":
            raise ValueError(
                "APP_ENV=prod requires AUTH_BACKEND=iap — refusing to run ungated in cloud."
            )
        return self
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/unit/test_settings_admin_emails.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add backend/app/settings.py tests/unit/test_settings_admin_emails.py
git commit -m "feat(auth): ADMIN_EMAILS + prod-requires-iap guard (no ungated cloud)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: Migration — `user_roles` table

**Files:**
- Create: `backend/migrations/0020_user_roles.sql`
- Test: `tests/integration/test_user_roles_repo.py` (the table-exists assertion; the repo methods come in Task 3)

- [ ] **Step 1: Confirm the next free migration number**

Run: `ls backend/migrations/ | sort | tail -3`
Expected: highest is `0019_poster_cache.sql` and there is no `0020_*.txt` sentinel. If `0020` is taken, use the next free number and adjust every reference below.

- [ ] **Step 2: Write the failing test**

```python
# tests/integration/test_user_roles_repo.py
"""user_roles persistence — the app-side authorization layer (spec
2026-06-14-iap-roles-admin-console-design.md). Google owns the gate; this
table owns roles."""
import pytest

from backend.app.db import open_db
from backend.app.migrations_runner import apply_migrations
from backend.app.context import MIGRATIONS


@pytest.fixture
async def conn(tmp_path):
    cm = open_db(tmp_path / "t.db")
    c = await cm.__aenter__()
    await apply_migrations(c, MIGRATIONS)
    yield c
    await cm.__aexit__(None, None, None)


async def test_user_roles_table_exists(conn):
    cur = await conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='user_roles'"
    )
    assert await cur.fetchone() is not None


async def test_role_check_constraint_rejects_bad_role(conn):
    import aiosqlite
    with pytest.raises(aiosqlite.IntegrityError):
        await conn.execute(
            "INSERT INTO user_roles(email, role) VALUES ('x@y.com', 'wizard')"
        )
        await conn.commit()
```

- [ ] **Step 3: Run to verify it fails**

Run: `python -m pytest tests/integration/test_user_roles_repo.py -q`
Expected: FAIL — no such table `user_roles`.

- [ ] **Step 4: Create the migration**

```sql
-- backend/migrations/0020_user_roles.sql
-- App-side authorization layer (spec 2026-06-14-iap-roles-admin-console-design).
-- Google IAP owns the GATE (who can reach the app); this table owns ROLES
-- (what a reached, IAP-verified user may DO). email is the verified identity,
-- stored lowercased. status: 'active' (roled + has signed in), 'invited'
-- (admin pre-assigned, awaiting first sign-in), 'requested' (user asked from
-- the denial page, awaiting an admin). Only 'active'/'invited' admit at the
-- gate; 'requested' is denied until granted.
CREATE TABLE user_roles (
  email        TEXT PRIMARY KEY,
  role         TEXT NOT NULL CHECK (role IN ('admin','annotator','publisher','viewer')),
  status       TEXT NOT NULL DEFAULT 'active'
                 CHECK (status IN ('active','invited','requested')),
  display_name TEXT,
  granted_by   TEXT,
  granted_at   TEXT NOT NULL DEFAULT (datetime('now')),
  last_seen_at TEXT
);
```

- [ ] **Step 5: Run to verify pass**

Run: `python -m pytest tests/integration/test_user_roles_repo.py -q`
Expected: PASS (2 passed).

- [ ] **Step 6: Commit**

```bash
git add backend/migrations/0020_user_roles.sql tests/integration/test_user_roles_repo.py
git commit -m "feat(auth): user_roles migration (roles, not the gate)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: `UserRolesRepo`

**Files:**
- Create: `backend/app/repositories/user_roles.py`
- Modify: `backend/app/context.py` (register on `CoreCtx`, delegate on `LiveCtx`)
- Test: append to `tests/integration/test_user_roles_repo.py`

- [ ] **Step 1: Write the failing tests** (append to the existing test file)

```python
from backend.app.repositories.user_roles import UserRolesRepo


async def test_upsert_get_active_role_and_seed(conn):
    repo = UserRolesRepo()
    # seed admins (idempotent; never downgrades)
    await repo.seed_admins(conn, ["Boss@X.com", "boss@x.com"])
    assert await repo.get_active_role(conn, "boss@x.com") == "admin"
    assert await repo.count_admins(conn) == 1
    # re-seed is a no-op and does not duplicate
    await repo.seed_admins(conn, ["boss@x.com"])
    assert await repo.count_admins(conn) == 1

    # invited admits at the gate; requested does not
    await repo.upsert_role(conn, "inv@x.com", "viewer", status="invited", granted_by="boss@x.com")
    assert await repo.get_active_role(conn, "inv@x.com") == "viewer"
    await repo.record_request(conn, "req@x.com", display_name="Req")
    assert await repo.get_active_role(conn, "req@x.com") is None  # denied until granted
    row = await repo.get(conn, "req@x.com")
    assert row["status"] == "requested"


async def test_mark_seen_flips_invited_to_active(conn):
    repo = UserRolesRepo()
    await repo.upsert_role(conn, "inv@x.com", "annotator", status="invited", granted_by="b@x.com")
    await repo.mark_seen(conn, "inv@x.com")
    row = await repo.get(conn, "inv@x.com")
    assert row["status"] == "active"
    assert row["last_seen_at"] is not None


async def test_list_filter_and_delete(conn):
    repo = UserRolesRepo()
    await repo.upsert_role(conn, "a@x.com", "admin", status="active", granted_by=None)
    await repo.upsert_role(conn, "v@x.com", "viewer", status="active", granted_by="a@x.com")
    admins = await repo.list_members(conn, role="admin")
    assert [m["email"] for m in admins] == ["a@x.com"]
    found = await repo.list_members(conn, query="v@")
    assert [m["email"] for m in found] == ["v@x.com"]
    assert await repo.delete(conn, "v@x.com") == 1
    assert await repo.get(conn, "v@x.com") is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/integration/test_user_roles_repo.py -q`
Expected: FAIL — `ModuleNotFoundError: backend.app.repositories.user_roles`.

- [ ] **Step 3: Implement the repo**

```python
# backend/app/repositories/user_roles.py
"""user_roles persistence — the app-side authorization layer.

Google IAP owns the gate; this table owns roles (spec
2026-06-14-iap-roles-admin-console-design.md). A leaf repo: raw SQL over
aiosqlite, no service imports. Email is the IAP-verified identity, stored and
compared lowercased so case can't defeat self-protection / dedupe.
"""

from __future__ import annotations

from typing import Any

import aiosqlite

_COLS = ("email", "role", "status", "display_name", "granted_by", "granted_at", "last_seen_at")
# Roles that admit at the gate. 'requested' is intentionally excluded.
_ACTIVE = ("active", "invited")


def _norm(email: str) -> str:
    return email.strip().lower()


class UserRolesRepo:
    """DB-backed user_roles."""

    async def get(self, conn: aiosqlite.Connection, email: str) -> dict[str, Any] | None:
        cur = await conn.execute(
            f"SELECT {', '.join(_COLS)} FROM user_roles WHERE email = ?", (_norm(email),)
        )
        row = await cur.fetchone()
        return dict(zip(_COLS, row, strict=True)) if row else None

    async def get_active_role(self, conn: aiosqlite.Connection, email: str) -> str | None:
        """The role that ADMITS at the gate, or None. Only active/invited admit;
        requested (and absent) are denied."""
        cur = await conn.execute(
            "SELECT role FROM user_roles WHERE email = ? AND status IN ('active','invited')",
            (_norm(email),),
        )
        row = await cur.fetchone()
        return row[0] if row else None

    async def list_members(
        self,
        conn: aiosqlite.Connection,
        *,
        role: str | None = None,
        status: str | None = None,
        query: str | None = None,
    ) -> list[dict[str, Any]]:
        sql = [f"SELECT {', '.join(_COLS)} FROM user_roles"]
        where: list[str] = []
        args: list[Any] = []
        if role:
            where.append("role = ?")
            args.append(role)
        if status:
            where.append("status = ?")
            args.append(status)
        if query:
            where.append("(email LIKE ? OR lower(coalesce(display_name,'')) LIKE ?)")
            q = f"%{query.strip().lower()}%"
            args += [q, q]
        if where:
            sql.append("WHERE " + " AND ".join(where))
        sql.append("ORDER BY (role='admin') DESC, email ASC")
        cur = await conn.execute(" ".join(sql), args)
        rows = await cur.fetchall()
        return [dict(zip(_COLS, r, strict=True)) for r in rows]

    async def upsert_role(
        self,
        conn: aiosqlite.Connection,
        email: str,
        role: str,
        *,
        status: str = "active",
        granted_by: str | None,
        display_name: str | None = None,
    ) -> None:
        await conn.execute(
            """
            INSERT INTO user_roles(email, role, status, display_name, granted_by, granted_at)
            VALUES (?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(email) DO UPDATE SET
                role = excluded.role,
                status = excluded.status,
                display_name = COALESCE(excluded.display_name, user_roles.display_name),
                granted_by = excluded.granted_by,
                granted_at = excluded.granted_at
            """,
            (_norm(email), role, status, display_name, granted_by and _norm(granted_by)),
        )
        await conn.commit()

    async def record_request(
        self, conn: aiosqlite.Connection, email: str, *, display_name: str | None = None
    ) -> None:
        """Record an access request from the denial page. No-op if the user
        already has any row (an active/invited user shouldn't be downgraded; a
        repeat request stays a single pending row)."""
        existing = await self.get(conn, email)
        if existing is not None:
            return
        await conn.execute(
            "INSERT INTO user_roles(email, role, status, display_name, granted_by, granted_at) "
            "VALUES (?, 'viewer', 'requested', ?, NULL, datetime('now'))",
            (_norm(email), display_name),
        )
        await conn.commit()

    async def mark_seen(self, conn: aiosqlite.Connection, email: str) -> None:
        """Bounded last-seen touch (≤ once/min) + flip invited→active on first
        sight. Bounded to keep Litestream write churn low (perf discipline)."""
        await conn.execute(
            """
            UPDATE user_roles
               SET last_seen_at = datetime('now'),
                   status = CASE WHEN status='invited' THEN 'active' ELSE status END
             WHERE email = ?
               AND status IN ('active','invited')
               AND (last_seen_at IS NULL OR last_seen_at < datetime('now','-60 seconds'))
            """,
            (_norm(email),),
        )
        await conn.commit()

    async def count_admins(self, conn: aiosqlite.Connection) -> int:
        cur = await conn.execute(
            "SELECT COUNT(*) FROM user_roles WHERE role='admin' AND status IN ('active','invited')"
        )
        row = await cur.fetchone()
        return int(row[0]) if row else 0

    async def delete(self, conn: aiosqlite.Connection, email: str) -> int:
        cur = await conn.execute("DELETE FROM user_roles WHERE email = ?", (_norm(email),))
        await conn.commit()
        return cur.rowcount

    async def seed_admins(self, conn: aiosqlite.Connection, emails: list[str]) -> None:
        """Idempotently seed deploy-time admins. INSERT OR IGNORE never
        downgrades or duplicates an existing row."""
        for e in emails:
            await conn.execute(
                "INSERT OR IGNORE INTO user_roles(email, role, status, granted_by, granted_at) "
                "VALUES (?, 'admin', 'active', 'bootstrap', datetime('now'))",
                (_norm(e),),
            )
        await conn.commit()
```

- [ ] **Step 4: Register on the context**

In `backend/app/context.py`:

(a) Add the import next to the other repo imports (after `from backend.app.repositories.uploaded_clips import UploadedClipsRepo`, ~line 66):

```python
from backend.app.repositories.user_roles import UserRolesRepo
```

(b) Add the field to `CoreCtx` (after `run_telemetry_repo`, ~line 113):

```python
    user_roles_repo: UserRolesRepo = field(default_factory=UserRolesRepo)
```

(c) Add the delegating property to `LiveCtx` (after the `run_telemetry_repo` property, ~line 325) — **required** by the drift guard `tests/unit/test_context_delegation.py`:

```python
    @property
    def user_roles_repo(self) -> UserRolesRepo:
        return self.core.user_roles_repo
```

(d) Seed admins in `CoreCtx.build`, right after the crash-recovery commit (after line 147 `await conn.commit()`, before `ctx = cls(...)` — note we need `ctx` to exist; place it *after* `ctx = cls(settings=settings, db=conn, db_cm=cm)` at ~line 149):

```python
        await ctx.user_roles_repo.seed_admins(conn, settings.admin_email_list)
```

- [ ] **Step 5: Run to verify pass**

Run: `python -m pytest tests/integration/test_user_roles_repo.py tests/unit/test_context_delegation.py -q`
Expected: PASS (repo tests + the delegation guard still green).

- [ ] **Step 6: Commit**

```bash
git add backend/app/repositories/user_roles.py backend/app/context.py tests/integration/test_user_roles_repo.py
git commit -m "feat(auth): UserRolesRepo + context wiring + admin seeding

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: `auth/roles.py` — the one role→capability source of truth

**Files:**
- Create: `backend/app/auth/roles.py`
- Test: `tests/unit/test_roles_caps.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_roles_caps.py
"""ROLE_CAPS is the single source of truth for role→permission (spec
2026-06-14-iap-roles-admin-console-design.md): Admin VPAM, Annotator VPA,
Publisher VP, Viewer V."""
from backend.app.auth.roles import ROLE_CAPS, ROLE_ORDER, has_permission


def test_capability_ladder():
    assert ROLE_CAPS["viewer"] == {"view"}
    assert ROLE_CAPS["publisher"] == {"view", "publish"}
    assert ROLE_CAPS["annotator"] == {"view", "publish", "run"}
    assert ROLE_CAPS["admin"] == {"view", "publish", "run", "manage"}


def test_role_order_admin_first():
    assert ROLE_ORDER == ["admin", "annotator", "publisher", "viewer"]


def test_has_permission_is_fail_closed_for_unknown_role():
    assert has_permission("annotator", "run") is True
    assert has_permission("viewer", "run") is False
    assert has_permission(None, "view") is False
    assert has_permission("wizard", "view") is False
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/unit/test_roles_caps.py -q`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement**

```python
# backend/app/auth/roles.py
"""The single source of truth for the role model (spec
2026-06-14-iap-roles-admin-console-design.md). Imported by the gate, the
CurrentUser capability helpers, the admin console UI, and the guards — so the
ladder is defined exactly once.
"""

from __future__ import annotations

# role -> set of capabilities. View(V) Publish(P) Run AI(A) Manage access(M).
ROLE_CAPS: dict[str, set[str]] = {
    "admin": {"view", "publish", "run", "manage"},
    "annotator": {"view", "publish", "run"},
    "publisher": {"view", "publish"},
    "viewer": {"view"},
}

# Display order (privilege descending), used by the table + role pickers.
ROLE_ORDER: list[str] = ["admin", "annotator", "publisher", "viewer"]

# Human labels + one-line descriptions for the role picker / pills.
ROLE_META: dict[str, dict[str, str]] = {
    "admin": {"label": "Admin", "desc": "Full control — manage members & access"},
    "annotator": {"label": "Annotator", "desc": "Run AI analysis, publish & view"},
    "publisher": {"label": "Publisher", "desc": "Publish & view analyses"},
    "viewer": {"label": "Viewer", "desc": "View analyses only"},
}

# Ordered (capability, letter) pairs for the V·P·A·M permission dots.
CAP_ORDER: list[tuple[str, str]] = [
    ("view", "V"),
    ("publish", "P"),
    ("run", "A"),
    ("manage", "M"),
]


def has_permission(role: str | None, cap: str) -> bool:
    """Fail-closed: an unknown/None role has no capabilities."""
    return cap in ROLE_CAPS.get(role or "", set())
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/unit/test_roles_caps.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add backend/app/auth/roles.py tests/unit/test_roles_caps.py
git commit -m "feat(auth): ROLE_CAPS — single source of truth for the role model

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 5: `CurrentUser` capabilities

**Files:**
- Modify: `backend/app/auth/models.py`
- Test: `tests/unit/test_current_user_caps.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_current_user_caps.py
"""CurrentUser derives permissions from role via ROLE_CAPS — no stored
permission state to drift."""
from backend.app.auth.models import CurrentUser


def test_admin_has_all_caps_and_is_admin():
    u = CurrentUser(email="a@x.com", role="admin")
    assert u.has("manage") and u.has("run") and u.is_admin


def test_viewer_lacks_run_and_manage():
    u = CurrentUser(email="v@x.com", role="viewer")
    assert u.has("view") and not u.has("run") and not u.has("manage")
    assert u.is_admin is False


def test_unroled_user_has_nothing():
    u = CurrentUser(email="x@x.com", role=None)
    assert u.permissions == frozenset()
    assert not u.has("view")
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/unit/test_current_user_caps.py -q`
Expected: FAIL — `CurrentUser` has no `has` / `permissions` / `is_admin`.

- [ ] **Step 3: Implement** — replace `backend/app/auth/models.py` body's class with:

```python
"""Identity value type shared by the seam and its adapters.

Lives in its own module so both ``identity`` (the dispatcher) and the
``adapters`` can import ``CurrentUser`` without a circular import.
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.app.auth.roles import ROLE_CAPS


@dataclass(frozen=True)
class CurrentUser:
    """Who is making the current request, and what they may do.

    ``role`` is populated by the authorization layer (the ``user_roles``
    lookup in the auth gate). ``permissions`` is derived from ``role`` via
    ``ROLE_CAPS`` so there is no stored permission state to drift.
    """

    email: str
    role: str | None = None

    @property
    def is_authenticated(self) -> bool:
        return bool(self.email)

    @property
    def permissions(self) -> frozenset[str]:
        return frozenset(ROLE_CAPS.get(self.role or "", set()))

    def has(self, cap: str) -> bool:
        return cap in self.permissions

    @property
    def is_admin(self) -> bool:
        return self.has("manage")
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/unit/test_current_user_caps.py tests/unit/test_auth_seam.py -q`
Expected: PASS (new caps tests + the existing seam tests still green).

- [ ] **Step 5: Commit**

```bash
git add backend/app/auth/models.py tests/unit/test_current_user_caps.py
git commit -m "feat(auth): CurrentUser derives permissions from role

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 6: The auth gate middleware (default-deny + allow-list)

This replaces the display-only `_attach_current_user` with `_auth_gate`: it still sets `request.state.current_user` (now with role) for the topbar, AND — under `auth_backend == 'iap'` — enforces default-deny with a tiny allow-list. Under `dev`, the single operator is implicit **admin** and nothing is gated.

**Files:**
- Modify: `backend/app/main.py`
- Test: `tests/integration/test_auth_gate.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_auth_gate.py
"""The auth gate: under AUTH_BACKEND=iap, no active role → 403 + the access
page; allow-listed paths stay reachable; an admin (seeded via ADMIN_EMAILS)
gets through. Fail-closed (spec 2026-06-14-iap-roles-admin-console-design.md).

We patch main.resolve_user so we don't have to forge a signed IAP JWT; the
gate logic (role lookup, allow-list, deny) is what's under test."""
import importlib
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.auth.models import CurrentUser


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
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/integration/test_auth_gate.py -q`
Expected: FAIL — today `/` is reachable / there is no gate (200 instead of 403).

- [ ] **Step 3: Implement the gate**

In `backend/app/main.py`, add imports near the top (with the other auth imports, ~line 14):

```python
from fastapi.responses import JSONResponse
from starlette.responses import Response
```

Add a module-level allow-list and a deny helper above the middleware (after `_timing_log = ...`, ~line 126):

```python
# Paths reachable WITHOUT an active role (everything else is default-deny under
# AUTH_BACKEND=iap). Keep this list tiny and explicit — forgetting a public
# path is harmless; the opt-out shape means we never forget a protected one.
_AUTH_ALLOWLIST = ("/static/", "/api/health", "/access", "/favicon.ico")


def _is_allowlisted(path: str) -> bool:
    return any(path == p or path.startswith(p.rstrip("/") + "/") or path == p.rstrip("/")
               for p in _AUTH_ALLOWLIST)


def _deny(request: Request, email: str | None) -> Response:
    """Fail-closed denial. JSON for HTMX/fetch callers; the access page (403)
    for a browser navigation."""
    wants_json = (
        request.headers.get("hx-request")
        or "application/json" in request.headers.get("accept", "")
    )
    if wants_json:
        return JSONResponse({"detail": "access not granted"}, status_code=403)
    from backend.app.routes.pages.templates import templates
    return templates.TemplateResponse(
        request, "pages/access.html",
        {"state": "denied", "email": email}, status_code=403,
    )
```

Replace the entire `_attach_current_user` middleware (lines ~169-187) with:

```python
@app.middleware("http")
async def _auth_gate(request: Request, call_next):
    """Resolve identity, attach it (with role) for the layout, and — under
    AUTH_BACKEND=iap — enforce default-deny on every non-allow-listed path.

    Fail-closed: any failure to establish a trustworthy identity, or any
    error in the role lookup, denies. Under AUTH_BACKEND=dev the single local
    operator is implicit admin and nothing is gated (local dev stays usable;
    no IAP path is exercised). See ADR 0081 + spec
    2026-06-14-iap-roles-admin-console-design.md.
    """
    request.state.current_user = None
    core = getattr(request.app.state, "core_ctx", None)
    path = request.url.path
    if core is None or path.startswith("/static/"):
        return await call_next(request)

    settings = core.settings

    # Resolve identity — cheap, no DB. Fail-closed → anonymous.
    try:
        ident = resolve_user(request, settings)
    except (NotAuthenticated, RuntimeError):
        ident = None

    # Dev: the single local operator is implicit admin; nothing is gated.
    if settings.auth_backend != "iap":
        if ident is not None:
            request.state.current_user = CurrentUser(email=ident.email, role="admin")
        return await call_next(request)

    # IAP: attach identity (role unknown yet) so /access can show who you are.
    email = ident.email if ident else None
    if email:
        request.state.current_user = CurrentUser(email=email, role=None)

    # Allow-list short-circuit BEFORE any DB work (health probes hit this often).
    if _is_allowlisted(path):
        return await call_next(request)

    # Authorize: look up the active role (fail-closed on any error).
    role = None
    if email:
        try:
            role = await core.user_roles_repo.get_active_role(core.db, email)
        except Exception:  # noqa: BLE001 — any lookup error denies, never admits
            role = None
    if role is None:
        return _deny(request, email)

    request.state.current_user = CurrentUser(email=email, role=role)
    await core.user_roles_repo.mark_seen(core.db, email)
    return await call_next(request)
```

Add the `CurrentUser` import to main.py if not present (with the other auth imports):

```python
from backend.app.auth.models import CurrentUser
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/integration/test_auth_gate.py tests/integration/test_topbar_current_user.py -q`
Expected: PASS — gate tests pass and the existing topbar test still passes (dev path still sets `current_user`).

- [ ] **Step 5: Run the broader suite to catch fallout**

Run: `python -m pytest tests/integration -q`
Expected: PASS. If any test that previously hit a now-gated route fails, it will be running under `AUTH_BACKEND=dev` (default) where nothing is gated — so failures here indicate a real wiring bug, not the gate. Fix before committing.

- [ ] **Step 6: Commit**

```bash
git add backend/app/main.py tests/integration/test_auth_gate.py
git commit -m "feat(auth): default-deny auth gate (fail-closed, allow-listed)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 7: Route guards `require_role` / `require_permission`

**Files:**
- Create: `backend/app/auth/guards.py`
- Test: `tests/unit/test_auth_guards.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_auth_guards.py
"""Route guards read request.state.current_user (set by the gate) and raise
403 fail-closed when the capability/role is missing."""
import pytest
from types import SimpleNamespace
from fastapi import HTTPException

from backend.app.auth.guards import require_permission, require_role
from backend.app.auth.models import CurrentUser


def _req(user):
    return SimpleNamespace(state=SimpleNamespace(current_user=user))


def test_require_permission_allows_capable_user():
    u = CurrentUser(email="a@x.com", role="annotator")
    assert require_permission(_req(u), "run") is u


def test_require_permission_denies_incapable_user():
    with pytest.raises(HTTPException) as ei:
        require_permission(_req(CurrentUser(email="v@x.com", role="viewer")), "run")
    assert ei.value.status_code == 403


def test_guards_deny_when_no_user():
    with pytest.raises(HTTPException) as ei:
        require_role(_req(None), "admin")
    assert ei.value.status_code == 403
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/unit/test_auth_guards.py -q`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement**

```python
# backend/app/auth/guards.py
"""Route-level authorization guards. The gate middleware already enforces
"must have an active role" app-wide; these add the finer checks (admin-only
console, run-capable AI endpoints). Both read the CurrentUser the gate stashed
on request.state and fail closed with 403.
"""

from __future__ import annotations

from fastapi import HTTPException, Request

from backend.app.auth.models import CurrentUser


def require_permission(request: Request, cap: str) -> CurrentUser:
    user = getattr(request.state, "current_user", None)
    if user is None or not user.has(cap):
        raise HTTPException(403, f"requires '{cap}' permission")
    return user


def require_role(request: Request, role: str) -> CurrentUser:
    user = getattr(request.state, "current_user", None)
    if user is None or user.role != role:
        raise HTTPException(403, f"requires '{role}' role")
    return user
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/unit/test_auth_guards.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add backend/app/auth/guards.py tests/unit/test_auth_guards.py
git commit -m "feat(auth): require_role / require_permission route guards

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 8: Gate the three AI-run surfaces with `require_permission("run")`

**Files:**
- Modify: `backend/app/routes/jobs.py` (`create_job`, ~line 28)
- Modify: `backend/app/routes/studio.py` (`create_run`, ~line 257)
- Modify: `backend/app/routes/live.py` (`session_config`, ~line 47)
- Test: `tests/integration/test_run_permission.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_run_permission.py
"""Only Annotator+ may trigger AI runs (cost + Gemini key + scarce CatDV seat).
A Viewer reaching the app is still refused at the run endpoints (spec
2026-06-14-iap-roles-admin-console-design.md). Drives identity via a mutable
holder so one app instance can act as two users."""
import importlib
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.auth.models import CurrentUser


def _app(monkeypatch, tmp_path, holder):
    for k, v in {
        "APP_ENV": "dev", "AUTH_BACKEND": "iap", "IAP_AUDIENCE": "aud",
        "ADMIN_EMAILS": "boss@x.com", "CATDV_BASE_URL": "http://localhost:0",
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


def test_viewer_cannot_create_job(monkeypatch, tmp_path: Path):
    holder = {"email": "boss@x.com"}
    main_mod = _app(monkeypatch, tmp_path, holder)
    with TestClient(main_mod.app) as client:
        # boss (admin) invites a viewer
        r = client.post("/admin/users",
                        data={"email": "viewer@x.com", "role": "viewer", "display_name": ""})
        assert r.status_code in (200, 201)
        # become the viewer; the run endpoint must refuse
        holder["email"] = "viewer@x.com"
        r = client.post("/api/jobs", json={"prompt_version_id": 1, "clip_ids": [1]})
    assert r.status_code == 403


def test_admin_passes_run_gate(monkeypatch, tmp_path: Path):
    holder = {"email": "boss@x.com"}
    main_mod = _app(monkeypatch, tmp_path, holder)
    with TestClient(main_mod.app) as client:
        r = client.post("/api/jobs", json={"prompt_version_id": 1, "clip_ids": [1]})
    # admin has 'run' → passes the gate (may 4xx later for other reasons, but NOT 403)
    assert r.status_code != 403
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/integration/test_run_permission.py -q`
Expected: FAIL — the viewer currently is NOT refused (no run guard yet). (The admin test may already pass; the viewer test is the red one.)

- [ ] **Step 3: Implement the guards**

In `backend/app/routes/jobs.py`, add the import (with the other imports, ~line 10):

```python
from backend.app.auth.guards import require_permission
```

In `create_job`, add as the first line of the body (before `ctx = get_core_ctx(request)`):

```python
    require_permission(request, "run")
```

In `backend/app/routes/studio.py`, add the import near the top:

```python
from backend.app.auth.guards import require_permission
```

In `create_run`, add as the first line of the body (before `ctx = get_core_ctx(request)`):

```python
    require_permission(request, "run")
```

In `backend/app/routes/live.py`, add the import near the top:

```python
from backend.app.auth.guards import require_permission
```

In `session_config`, add as the first line of the body (before `ctx = get_live_ctx(request)`):

```python
    require_permission(request, "run")
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/integration/test_run_permission.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Run the studio/jobs/live suites for regressions**

Run: `python -m pytest tests/integration/test_studio_api.py tests/integration/test_routes_live.py -q`
Expected: PASS — these run under `AUTH_BACKEND=dev` (implicit admin), so the run gate is satisfied. If any fail with 403, the test calls the handler without the gate having set `current_user`; fix by ensuring it goes through `TestClient` (which runs the middleware) rather than calling the handler function directly.

- [ ] **Step 6: Commit**

```bash
git add backend/app/routes/jobs.py backend/app/routes/studio.py backend/app/routes/live.py tests/integration/test_run_permission.py
git commit -m "feat(auth): Annotator-required for AI runs (jobs, studio, live key)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

# PHASE 2 — PR3: admin console + access-request

## Task 9: Admin console route + role CRUD (with self-protection + last-admin guard)

**Files:**
- Create: `backend/app/routes/pages/admin.py`
- Modify: `backend/app/routes/pages/__init__.py`
- Test: `tests/integration/test_admin_console.py`

- [ ] **Step 1: Write the failing test**

```python
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
        assert "Access & Permissions" in client.get("/admin").text
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
```

> Note: the last-admin test is deliberately conservative — self-protection already prevents an admin demoting themselves, which covers the "only admin demotes self" case. The explicit `count_admins` guard in the route is the belt-and-braces for the "admin A demotes admin B when B is the last *other* admin" path; keep both.

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/integration/test_admin_console.py -q`
Expected: FAIL — `/admin` 404 (route not registered).

- [ ] **Step 3: Implement the route**

```python
# backend/app/routes/pages/admin.py
"""Admin console — Access & Permissions (spec
2026-06-14-iap-roles-admin-console-design.md). Admin-only. Google IAP owns the
gate; this page manages the app-side user_roles (what a reached user may do).
'Add member' pre-assigns a role (status 'invited') — it does NOT open the
Google gate; a human admin must also add them to the Google Group.

CRUD endpoints return the members-table partial on HX-Request and push a toast;
never location.reload(). Self-protection + last-admin guard are enforced
server-side, not just hidden in the UI.
"""

from __future__ import annotations

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse

from backend.app.auth.guards import require_role
from backend.app.auth.roles import ROLE_CAPS, ROLE_META, ROLE_ORDER
from backend.app.deps import get_core_ctx
from backend.app.routes.pages.templates import templates

router = APIRouter()


def _norm(email: str) -> str:
    return email.strip().lower()


async def _members_ctx(request: Request, *, role=None, status=None, query=None) -> dict:
    ctx = get_core_ctx(request)
    members = await ctx.user_roles_repo.list_members(
        ctx.db, role=role or None, status=status or None, query=query or None
    )
    admins = sum(1 for m in members if m["role"] == "admin")
    pending = sum(1 for m in members if m["status"] == "requested")
    me = request.state.current_user.email
    return {
        "members": members,
        "me": me,
        "counts": {"members": len(members), "admins": admins, "pending": pending},
        "role_order": ROLE_ORDER,
        "role_meta": ROLE_META,
        "role_caps": ROLE_CAPS,
        "filters": {"role": role or "", "status": status or "", "query": query or ""},
    }


@router.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request, role: str = "", status: str = "", q: str = ""):
    require_role(request, "admin")
    data = await _members_ctx(request, role=role, status=status, query=q)
    template = "pages/_admin_members.html" if request.headers.get("hx-request") else "pages/admin.html"
    return templates.TemplateResponse(request, template, data)


@router.post("/admin/users", response_class=HTMLResponse)
async def add_member(
    request: Request,
    email: str = Form(...),
    role: str = Form(...),
    display_name: str = Form(""),
):
    require_role(request, "admin")
    if role not in ROLE_CAPS:
        raise HTTPException(400, "unknown role")
    if "@" not in email:
        raise HTTPException(400, "invalid email")
    ctx = get_core_ctx(request)
    existing = await ctx.user_roles_repo.get(ctx.db, email)
    # An access-request becomes a real grant; a brand-new email becomes 'invited'.
    status = "active" if existing and existing["status"] == "requested" else "invited"
    await ctx.user_roles_repo.upsert_role(
        ctx.db, email, role, status=status, granted_by=request.state.current_user.email,
        display_name=display_name or None,
    )
    data = await _members_ctx(request)
    return templates.TemplateResponse(request, "pages/_admin_members.html", data)


@router.patch("/admin/users/{email}", response_class=HTMLResponse)
async def change_role(request: Request, email: str, role: str = Form(...)):
    require_role(request, "admin")
    if role not in ROLE_CAPS:
        raise HTTPException(400, "unknown role")
    ctx = get_core_ctx(request)
    target = _norm(email)
    if target == _norm(request.state.current_user.email):
        raise HTTPException(403, "you can't change your own role")
    current = await ctx.user_roles_repo.get(ctx.db, target)
    if current is None:
        raise HTTPException(404, "no such member")
    # last-admin guard: never let the count of admins reach zero.
    if current["role"] == "admin" and role != "admin":
        if await ctx.user_roles_repo.count_admins(ctx.db) <= 1:
            raise HTTPException(409, "can't demote the last admin")
    await ctx.user_roles_repo.upsert_role(
        ctx.db, target, role, status=current["status"],
        granted_by=request.state.current_user.email,
    )
    data = await _members_ctx(request)
    return templates.TemplateResponse(request, "pages/_admin_members.html", data)


@router.delete("/admin/users/{email}", response_class=HTMLResponse)
async def revoke(request: Request, email: str):
    require_role(request, "admin")
    ctx = get_core_ctx(request)
    target = _norm(email)
    if target == _norm(request.state.current_user.email):
        raise HTTPException(403, "you can't revoke your own access")
    current = await ctx.user_roles_repo.get(ctx.db, target)
    if current is None:
        raise HTTPException(404, "no such member")
    if current["role"] == "admin" and await ctx.user_roles_repo.count_admins(ctx.db) <= 1:
        raise HTTPException(409, "can't revoke the last admin")
    await ctx.user_roles_repo.delete(ctx.db, target)
    data = await _members_ctx(request)
    return templates.TemplateResponse(request, "pages/_admin_members.html", data)
```

Register the router in `backend/app/routes/pages/__init__.py`:

```python
from backend.app.routes.pages.access import router as access_router
from backend.app.routes.pages.admin import router as admin_router
from backend.app.routes.pages.clips import router as clips_router
from backend.app.routes.pages.prompts import router as prompts_router
from backend.app.routes.pages.studio import router as studio_router

page_routers = [clips_router, prompts_router, studio_router, access_router, admin_router]

__all__ = [
    "page_routers",
    "access_router",
    "admin_router",
    "clips_router",
    "prompts_router",
    "studio_router",
]
```

> The page templates (`admin.html`, `_admin_members.html`, `_perm_dots.html`) are created in Task 10; this task's tests will still fail to render until Task 10. To keep Task 9 independently green, implement Task 10's three templates **before** running Step 4 here (they are tightly coupled — treat Tasks 9+10 as one commit if you prefer).

- [ ] **Step 4: Run after Task 10's templates exist**

Run: `python -m pytest tests/integration/test_admin_console.py -q`
Expected: PASS once templates exist.

- [ ] **Step 5: Commit** (after Task 10)

```bash
git add backend/app/routes/pages/admin.py backend/app/routes/pages/__init__.py tests/integration/test_admin_console.py
git commit -m "feat(admin): role CRUD with self-protection + last-admin guard

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 10: Admin console templates (reusing the shared UI library)

**Files:**
- Create: `backend/app/templates/pages/admin.html`
- Create: `backend/app/templates/pages/_admin_members.html`
- Create: `backend/app/templates/pages/_perm_dots.html`

- [ ] **Step 1: `_perm_dots.html`** — the V·P·A·M dots (tokens only)

```jinja
{# V·P·A·M permission dots derived from a role's capability set. Display-only.
   Pass `caps` = the set of capabilities for the row's role, and the shared
   CAP order via `cap_order` (list of (cap, letter)). #}
<span class="perm-dots" aria-label="permissions">
  {%- for cap, letter in cap_order -%}
    <span class="perm-dot{{ ' on' if cap in caps else '' }}" title="{{ cap }}">{{ letter }}</span>
  {%- endfor -%}
</span>
```

- [ ] **Step 2: `_admin_members.html`** — the HTMX-swappable table

```jinja
{% import "components/_ui.html" as ui %}
{# CAP order shared with roles.py CAP_ORDER; kept inline here (the included
   _perm_dots.html reads `cap_order` + `caps` from this context). #}
{% set cap_order = [('view','V'), ('publish','P'), ('run','A'), ('manage','M')] %}
<div id="members" class="admin-members">
  <table class="admin-table">
    <thead>
      <tr><th>Member</th><th>Role</th><th>Permissions</th><th>Last sign-in</th><th>Status</th><th></th></tr>
    </thead>
    <tbody>
      {% for m in members %}
      <tr>
        <td class="m-id">
          <span class="avatar">{{ (m.display_name or m.email)[:2] | upper }}</span>
          <span class="m-name">
            {{ m.display_name or m.email.split('@')[0] }}
            {% if m.email == me %}<span class="you-chip">YOU</span>{% endif %}
            <span class="m-email">{{ m.email }}</span>
          </span>
        </td>
        <td>
          {% if m.email == me %}
            <span class="role-pill {{ 'admin' if m.role=='admin' else '' }}">{{ role_meta[m.role].label }}</span>
          {% else %}
            {% call ui.menu(label=role_meta[m.role].label, variant='ghost', size='sm',
                            trigger_cls=('role-pill ' ~ ('admin' if m.role=='admin' else ''))) %}
              {% for r in role_order %}
                {{ ui.menu_item(role_meta[r].label, desc=role_meta[r].desc, current=(r==m.role),
                   attrs='hx-patch="/admin/users/' ~ m.email ~ '" hx-vals=\'{"role":"' ~ r ~ '"}\' hx-target="#members" hx-swap="outerHTML"') }}
              {% endfor %}
            {% endcall %}
          {% endif %}
        </td>
        <td>{% with caps = role_caps[m.role] %}{% include "pages/_perm_dots.html" %}{% endwith %}</td>
        <td class="mono">{{ m.last_seen_at or '—' }}</td>
        <td>{{ ui.status_pill(m.status, 'ok' if m.status=='active' else ('accent' if m.status=='invited' else 'bad')) }}</td>
        <td class="m-actions">
          {% if m.email != me %}
          {% call ui.menu(label='⋯', variant='ghost', size='sm', align='right') %}
            {{ ui.menu_item('Revoke access', danger=True,
               attrs='hx-delete="/admin/users/' ~ m.email ~ '" hx-target="#members" hx-swap="outerHTML" hx-confirm="Revoke access for ' ~ m.email ~ '?"') }}
          {% endcall %}
          {% endif %}
        </td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
</div>
```

> After an HTMX swap, push a success toast and re-init the subtree. Add this once at the bottom of `_admin_members.html`:

```jinja
<script>
  if (window.htmxAlpine) window.htmxAlpine.reinit(document.getElementById('members'));
</script>
```

- [ ] **Step 3: `admin.html`** — the full page

```jinja
{% extends "pages/layout.html" %}
{% import "components/_ui.html" as ui %}
{% block rail_active %}admin{% endblock %}
{% block body %}
<div class="admin-page" x-data="{ showAdd: false }">

  {% call ui.page_header('Access & Permissions',
       meta='Manage who can reach CatDV and what each member is allowed to do') %}
    {{ ui.button('+ Add member', variant='primary', attrs='@click="showAdd = true"') }}
  {% endcall %}

  <div class="admin-cards">
    <div class="info-card">
      <strong>Identity-Aware Proxy <span class="pill accent"><span class="led"></span>Enforced</span></strong>
      <p>Google verifies every visitor's identity at the edge before any request reaches CatDV.</p>
    </div>
    <div class="info-card">
      <strong>Application roles · IAM</strong>
      <p>Roles below are stored in the app and control in-app permissions. Adding a member
         here assigns a role — an admin must also add them to the Google Group to let them in.</p>
    </div>
  </div>

  <div class="admin-stats">
    <div class="stat"><span class="n">{{ counts.members }}</span><span class="l">Members</span></div>
    <div class="stat"><span class="n">{{ counts.admins }}</span><span class="l">Admins</span></div>
    <div class="stat"><span class="n">{{ counts.pending }}</span><span class="l">Pending requests</span></div>
  </div>

  <form class="admin-filters" hx-get="/admin" hx-target="#members" hx-swap="outerHTML"
        hx-trigger="input delay:200ms from:input, change from:select">
    {{ ui.field('Search', 'q', value=filters.query, input_attrs='placeholder="Search by name or email"') }}
    <label class="field"><span class="field-label">Role</span>
      <select class="txt" name="role">
        <option value="">Any</option>
        {% for r in role_order %}<option value="{{ r }}" {{ 'selected' if filters.role==r }}>{{ role_meta[r].label }}</option>{% endfor %}
      </select>
    </label>
    <label class="field"><span class="field-label">Status</span>
      <select class="txt" name="status">
        <option value="">Any</option>
        {% for s in ['active','invited','requested'] %}<option value="{{ s }}" {{ 'selected' if filters.status==s }}>{{ s }}</option>{% endfor %}
      </select>
    </label>
  </form>

  {% include "pages/_admin_members.html" %}

  {% call ui.modal('showAdd', label='Add member', card_cls='sm') %}
    <form class="modal-body" hx-post="/admin/users" hx-target="#members" hx-swap="outerHTML"
          @htmx:after-request="if ($event.detail.successful) { showAdd = false; $store.toast.push('Member added', {level:'success'}); }">
      {{ ui.field('Email address', 'email', input_attrs='placeholder="name@yourco.com" required') }}
      {{ ui.field('Display name (optional)', 'display_name', input_attrs='placeholder="e.g. Jordan Lee"') }}
      <label class="field"><span class="field-label">Assign a role</span>
        <select class="txt" name="role">
          {% for r in role_order %}<option value="{{ r }}" {{ 'selected' if r=='viewer' }}>{{ role_meta[r].label }} — {{ role_meta[r].desc }}</option>{% endfor %}
        </select>
      </label>
      <p class="field-help">They'll be added to the app's access list. To let them sign in, an admin must also add their email to the Google Group.</p>
      <div class="modal-actions">
        {{ ui.button('Cancel', variant='ghost', attrs='type="button" @click="showAdd = false"') }}
        {{ ui.button('Send invite', variant='primary', type='submit') }}
      </div>
    </form>
  {% endcall %}

</div>
{% endblock %}
```

- [ ] **Step 4: Run the admin console tests** (Task 9 Step 4)

Run: `python -m pytest tests/integration/test_admin_console.py tests/unit/test_design_language_guard.py -q`
Expected: PASS — console works AND the design-language guard is happy (we used `.btn`/`ui.*`, tokens, no `*-menu`/`modal-*` hand-rolling). If the guard flags a class, replace it with the sanctioned component/token.

- [ ] **Step 5: Commit** (templates + Task 9 route together)

```bash
git add backend/app/templates/pages/admin.html backend/app/templates/pages/_admin_members.html backend/app/templates/pages/_perm_dots.html backend/app/routes/pages/admin.py backend/app/routes/pages/__init__.py tests/integration/test_admin_console.py
git commit -m "feat(admin): Access & Permissions console (shared UI, HTMX, toasts)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 11: Admin-only topbar link

**Files:**
- Modify: `backend/app/templates/pages/layout.html` (~lines 53-55)
- Test: extend `tests/integration/test_admin_console.py`

- [ ] **Step 1: Write the failing test** (append)

```python
def test_admin_link_only_for_admins(monkeypatch, tmp_path: Path):
    holder = {"email": "boss@x.com"}
    main_mod = _app(monkeypatch, tmp_path, holder)
    with TestClient(main_mod.app) as client:
        assert 'href="/admin"' in client.get("/").text          # admin sees it
        client.post("/admin/users", data={"email": "v@x.com", "role": "viewer", "display_name": ""})
        holder["email"] = "v@x.com"
        assert 'href="/admin"' not in client.get("/").text       # viewer does not
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/integration/test_admin_console.py::test_admin_link_only_for_admins -q`
Expected: FAIL — no admin link in the topbar.

- [ ] **Step 3: Implement** — in `layout.html`, replace the current-user block (lines 53-55) with:

```jinja
      {% if request.state.current_user and request.state.current_user.is_authenticated %}
      {% if request.state.current_user.is_admin %}
      <a class="btn ghost sm topbar-admin" href="/admin">Admin</a>
      {% endif %}
      <span class="topbar-user" title="Signed in">{{ request.state.current_user.email }}</span>
      {% endif %}
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/integration/test_admin_console.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/templates/pages/layout.html tests/integration/test_admin_console.py
git commit -m "feat(admin): admin-only topbar link

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 12: Access page — identity card + Request access + rebrand

**Files:**
- Modify: `backend/app/routes/pages/access.py`
- Modify: `backend/app/templates/pages/access.html`
- Test: `tests/integration/test_access_request.py`; update `tests/integration/test_access_page.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_access_request.py
"""The denial page lets a reached-but-unroled user record an access request
(in-console, no email promised) and shows who they're signed in as."""
import importlib
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.auth.models import CurrentUser


def _app(monkeypatch, tmp_path, email):
    for k, v in {
        "APP_ENV": "dev", "AUTH_BACKEND": "iap", "IAP_AUDIENCE": "aud",
        "ADMIN_EMAILS": "boss@x.com", "CATDV_BASE_URL": "http://localhost:0",
        "CATDV_USERNAME": "", "CATDV_PASSWORD": "p", "CATDV_CATALOG_ID": "881507",
        "GCP_PROJECT_ID": "p", "GCS_BUCKET_NAME": "b", "PROXY_SOURCE": "rest",
        "DATA_DIR": str(tmp_path),
    }.items():
        monkeypatch.setenv(k, v)
    from backend.app import main as main_mod
    importlib.reload(main_mod)
    monkeypatch.setattr(main_mod, "resolve_user",
                        lambda req, s: CurrentUser(email=email))
    return main_mod


def test_request_access_records_pending(monkeypatch, tmp_path: Path):
    main_mod = _app(monkeypatch, tmp_path, "newbie@x.com")
    with TestClient(main_mod.app) as client:
        denied = client.get("/")
        assert denied.status_code == 403
        assert "newbie@x.com" in denied.text          # identity card
        assert "CatDV Annotator" in denied.text        # rebrand (not "Archive AI")
        r = client.post("/access/request", headers={"HX-Request": "true"})
        assert r.status_code == 200
        assert "review" in r.text.lower()
        # appears as a pending request to the admin
    main2 = _app(monkeypatch, tmp_path, "boss@x.com")
    with TestClient(main2.app) as client:
        assert "newbie@x.com" in client.get("/admin?status=requested").text


def test_request_access_is_allowlisted(monkeypatch, tmp_path: Path):
    main_mod = _app(monkeypatch, tmp_path, "newbie@x.com")
    with TestClient(main_mod.app) as client:
        # the POST itself must not be gated (would loop)
        assert client.post("/access/request").status_code == 200
```

Also update `tests/integration/test_access_page.py`: change the brand assertion from `"Archive AI"` to `"CatDV Annotator"` in `test_access_denied_renders` (and the page `<title>`).

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/integration/test_access_request.py -q`
Expected: FAIL — `/access/request` 404 / no identity card / brand mismatch.

- [ ] **Step 3: Implement the route** — append to `backend/app/routes/pages/access.py`:

```python
from fastapi import Form


@router.post("/access/request", response_class=HTMLResponse)
async def request_access(request: Request):
    """Record an access request from a reached-but-unroled user. Allow-listed
    (the gate must not block this — it's the one action a denied user can take).
    No email is sent; admins see the request in the console."""
    from backend.app.deps import get_core_ctx

    user = getattr(request.state, "current_user", None)
    if user is None or not user.email:
        # No verified identity → nothing to record. Fail closed but quietly.
        raise HTTPException(403, "no identity")
    ctx = get_core_ctx(request)
    await ctx.user_roles_repo.record_request(ctx.db, user.email)
    return templates.TemplateResponse(
        request, "pages/access.html", {"state": "requested", "email": user.email}
    )
```

Add `HTTPException` to the imports in `access.py`:

```python
from fastapi import APIRouter, Form, HTTPException, Request
```

- [ ] **Step 4: Implement the template** — replace `backend/app/templates/pages/access.html` with the rebranded version that adds the identity card + request states. Keep it standalone (no `layout.html`).

```jinja
{% import "components/_ui.html" as ui %}
<!doctype html>
{# Standalone access-control page (no nav/topbar — an unauthorized user must
   not see app chrome). states: denied | requested | error. Sign-in/redirect
   are owned by Google IAP. Spec 2026-06-14-iap-roles-admin-console-design.md. #}
<html lang="en" data-theme="dark">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{% if state == 'error' %}Sign-in error{% else %}No access{% endif %} · CatDV Annotator</title>
  <link rel="stylesheet" href="/static/app.css">
</head>
<body>
  <header class="auth-topbar"><span class="brand-dot"></span> CatDV Annotator</header>
  <main class="auth-screen">
    <div class="auth-panel">
      {% if state == 'error' %}
      <div class="auth-body">
        <div class="auth-icon err">&times;</div>
        <div class="auth-title">Couldn't sign you in</div>
        <div class="auth-msg">Something went wrong during authentication. Please try again.</div>
        <div class="auth-actions">{{ ui.button('Try again', href='/', variant='ghost') }}</div>
        <div class="auth-foot">Error code: {{ error_code or 'auth_failed' }}</div>
      </div>
      {% else %}
      <div class="auth-body">
        <div class="auth-title">No access to this catalog</div>
        <div class="auth-msg">You're signed in, but your account isn't on the access list yet. An administrator needs to grant you a role.</div>

        {% if email %}
        <div class="auth-identity">
          <span class="avatar">{{ email[:2] | upper }}</span>
          <span class="auth-email">{{ email }}</span>
          <span class="pill ok"><span class="led"></span>Signed in</span>
        </div>
        {% endif %}

        <div id="access-action">
          {% if state == 'requested' %}
          <div class="auth-sent"><span class="pill ok"><span class="led"></span></span>
            Request sent. An admin will review it.</div>
          {% else %}
          {# Plain form POST (no JS): the standalone denial page stays robust
             even with no scripts/offline. The POST re-renders this page with
             state='requested'. #}
          <form method="post" action="/access/request">
            {{ ui.button('Request access', variant='primary', type='submit') }}
          </form>
          {% endif %}
          <div class="auth-actions">
            {{ ui.button('Try again', href='/', variant='ghost') }}
            {{ ui.button('Use a different account', href='?gcp-iap-mode=CLEAR_LOGIN_COOKIE', variant='ghost') }}
          </div>
        </div>
        <div class="auth-foot">Need access sooner? Contact your workspace admin.</div>
      </div>
      {% endif %}
    </div>
  </main>
</body>
</html>
```

> The `access.py` GET handler already accepts `state`; add `"requested"` to its `Literal` and pass-through (`view = state if state in ('error','requested') else 'denied'`).

Update the GET handler signature in `access.py`:

```python
@router.get("/access", response_class=HTMLResponse)
async def access(
    request: Request,
    state: Literal["denied", "error", "requested"] = "denied",
    email: str | None = None,
):
    view = state if state in ("error", "requested") else "denied"
    return templates.TemplateResponse(
        request, "pages/access.html", {"state": view, "email": email},
    )
```

- [ ] **Step 5: Run to verify pass**

Run: `python -m pytest tests/integration/test_access_request.py tests/integration/test_access_page.py -q`
Expected: PASS (new request flow + the updated brand assertions).

- [ ] **Step 6: Commit**

```bash
git add backend/app/routes/pages/access.py backend/app/templates/pages/access.html tests/integration/test_access_request.py tests/integration/test_access_page.py
git commit -m "feat(auth): access-denied identity card + in-console request flow

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 13: Styles — role pills, perm dots, admin table, access card (tokens only)

**Files:**
- Modify: `backend/app/static/app.css`
- Test: visual + `tests/unit/test_design_language_guard.py`

- [ ] **Step 1: Add styles** at the end of `app.css`, using ONLY `var(--…)` tokens (no raw hex). Match the existing dark/amber/green language.

```css
/* ── Admin console + access page (spec 2026-06-14) ───────────────── */
.admin-page { padding: 16px 20px; }
.admin-cards { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin: 14px 0; }
.info-card { border: 1px solid var(--line-2); border-radius: 10px; padding: 12px 14px; background: var(--surface); }
.info-card p { color: var(--text-3); font-size: 12.5px; margin: 6px 0 0; }
.admin-stats { display: flex; gap: 12px; margin: 12px 0; }
.admin-stats .stat { border: 1px solid var(--line-2); border-radius: 10px; padding: 10px 16px; background: var(--surface); }
.admin-stats .n { display: block; font-size: 22px; font-weight: 700; }
.admin-stats .l { color: var(--text-3); font-size: 11px; text-transform: uppercase; letter-spacing: .05em; }
.admin-filters { display: flex; gap: 12px; align-items: end; margin: 12px 0; }

.admin-table { width: 100%; border-collapse: collapse; }
.admin-table th { text-align: left; color: var(--text-3); font-size: 11px; text-transform: uppercase;
  letter-spacing: .05em; padding: 8px 10px; border-bottom: 1px solid var(--line); }
.admin-table td { padding: 10px; border-bottom: 1px solid var(--line); vertical-align: middle; }
.admin-table .m-id { display: flex; align-items: center; gap: 10px; }
.admin-table .m-email { display: block; color: var(--text-3); font-size: 11.5px; font-family: var(--f-mono); }
.avatar { width: 30px; height: 30px; border-radius: 50%; background: var(--surface-2);
  border: 1px solid var(--line-2); display: inline-flex; align-items: center; justify-content: center;
  font-size: 11px; font-weight: 700; color: var(--text-2); flex-shrink: 0; }
.you-chip { font-size: 9px; font-weight: 700; color: var(--accent); border: 1px solid var(--accent-2);
  border-radius: 4px; padding: 0 4px; margin-left: 6px; vertical-align: middle; }

.role-pill { border: 1px solid var(--line-2); border-radius: 11px; }
.role-pill.admin { color: var(--accent); border-color: color-mix(in oklab, var(--accent) 35%, transparent); }

.perm-dots { display: inline-flex; gap: 4px; }
.perm-dot { width: 18px; height: 18px; border-radius: 4px; font-size: 10px; font-weight: 700;
  display: inline-flex; align-items: center; justify-content: center;
  background: var(--surface-2); color: var(--text-3); border: 1px solid var(--line); }
.perm-dot.on { background: color-mix(in oklab, var(--good) 22%, transparent); color: var(--good);
  border-color: color-mix(in oklab, var(--good) 40%, transparent); }

/* access page */
.auth-topbar { height: 54px; display: flex; align-items: center; gap: 9px; padding: 0 16px;
  background: var(--bg-2); border-bottom: 1px solid var(--line); font-weight: 700; }
.auth-identity { display: flex; align-items: center; gap: 10px; padding: 10px 0; margin: 10px 0;
  border-top: 1px solid var(--line); border-bottom: 1px solid var(--line); }
.auth-identity .auth-email { font-family: var(--f-mono); font-size: 12.5px; color: var(--text-2); flex: 1; }
.auth-sent { display: flex; align-items: center; gap: 8px; color: var(--good); font-size: 13px; margin: 10px 0; }
```

- [ ] **Step 2: Verify the guard + a visual check**

Run: `python -m pytest tests/unit/test_design_language_guard.py -q`
Expected: PASS.

Manual: start the dev server (use the `server-start` skill / its discipline), open `http://127.0.0.1:8765/admin`. Confirm the table, role pills, V·P·A·M dots, Add-member modal, and the access page at `/access?state=denied&email=x@y.com` all read as CatDV's dark/amber/green language. Stop the server with the `server-stop` skill (SIGTERM only).

- [ ] **Step 3: Commit**

```bash
git add backend/app/static/app.css
git commit -m "feat(admin): styles for role pills, perm dots, admin table, access card

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 14: Full suite, guards, and spec reconciliation

**Files:**
- Modify: `docs/specs/2026-06-14-iap-roles-admin-console-design.md` (correct the run-surface list)
- Possibly modify: existing test fakes that build `Settings`-like objects

- [ ] **Step 1: Run the whole suite + the architecture guards**

Run:
```
python -m pytest -q
lint-imports
```
Expected: all green. Common fixups:
- A `type("S", ...)` / `SimpleNamespace` settings fake missing `admin_emails` / `admin_email_list` → add them (mirror the PR2a fixes the spec mentions).
- A test that calls a gated handler **function directly** (not via TestClient) → it won't have `request.state.current_user`; route it through TestClient or set `request.state.current_user` in the test.

- [ ] **Step 2: Extend the seam boundary guard** if needed

Run: `python -m pytest tests/unit/test_auth_seam_boundary.py -q`
Expected: PASS. The new `auth/roles.py`, `auth/guards.py`, and the gate read identity through `CurrentUser` / `resolve_user` and never touch IAP specifics, so the boundary holds. If the guard enumerates allowed files, add `roles.py`/`guards.py` to the non-adapter set.

- [ ] **Step 3: Reconcile the spec** — edit the spec's enforcement list so `POST /sync/run` is replaced by `POST /api/jobs`, matching what was actually gated (see the "Deviation" note at the top of this plan).

- [ ] **Step 4: Commit**

```bash
git add docs/specs/2026-06-14-iap-roles-admin-console-design.md <any test fakes touched>
git commit -m "test+docs: green suite; reconcile run-surface list to /api/jobs

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 15: ADR + manual acceptance pass

**Files:**
- Create: `docs/adr/00NN-iap-roles-admin-console.md` (next free number — verify; parallel branches may collide, per the ADR-collision hazard)
- Modify: `docs/decisions.md` (index row)

- [ ] **Step 1: Write the ADR** (MADR-lite) recording: the 4-role model (resolving ADR 0081 open #1), the app-never-touches-the-Group decision, "Add member = pre-assign role", the in-console request flow (no email), the default-deny middleware (opt-out) over per-route dependencies, and `dev` operator = implicit admin. Sections: `## Context`, `## Alternatives`, `## Decision`, `## Consequences`. Cross-reference ADR 0081 and the spec.

- [ ] **Step 2: Add the index row** to the table in `docs/decisions.md`.

- [ ] **Step 3: Walk the spec's Manual acceptance flows** (#1–#12). For the cloud-only ones (IAP gate, cold start), confirm they're covered by the integration tests' fail-closed assertions; the live IAP flows are validated at PR4 cutover. Use the `deploy-staging` skill to exercise the gate in the cloud without touching prod if you want a live check before cutover.

- [ ] **Step 4: Commit**

```bash
git add docs/adr/00NN-iap-roles-admin-console.md docs/decisions.md
git commit -m "docs: ADR for IAP roles + admin console decisions

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Done criteria

- `python -m pytest -q` and `lint-imports` green.
- Under `AUTH_BACKEND=iap`: no-role → 403 + access page; allow-list reachable; admin reaches `/admin`; viewer 403s on `/api/jobs`, `/studio/runs`, `/live/session-config`; self-demote/revoke and last-admin removal refused.
- Under `AUTH_BACKEND=dev`: app fully usable locally as implicit admin; no IAP path exercised.
- The admin console + access page render in CatDV's design language with the shared UI library (design-language guard green).
- Spec + ADR committed and reconciled.

**Out of scope (fast follow):** Publisher-gated publish + Viewer read-only enforcement on existing routes; PR4 `--iap` cutover (already scoped in the 06-13 spec); per-user attribution stamping (can ride a later slice).
