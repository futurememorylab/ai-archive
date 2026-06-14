# IAP roles + admin console (PR2b enforcement + PR3 admin console)

**Date:** 2026-06-14
**Status:** Draft (design) — approved in brainstorm 2026-06-14, pending spec review.
**Extends:** `docs/specs/2026-06-13-iap-access-control-design.md` (the IAP access-control
design). This spec details the **authorization** half — the `user_roles` layer, the
default-deny gate, AI-run enforcement, the **admin console**, and the **access-request**
flow — incorporating the Claude-Design handoff `admin-panel-ai-archive` (`Admin
Console.dc.html` + `Access Denied.dc.html`). It does **not** change the mechanism
decisions (IAP direct on Cloud Run; the seam) already fixed in the 06-13 spec / ADR 0078.
**Decision record:** ADR 0078. This spec resolves the 06-13 spec's OPEN decisions #1 (roles
model → the 4-role model below) and #3 (first-admin bootstrap → `ADMIN_EMAILS` seed); a
follow-up ADR records the 4-role model + the in-console request flow.

## Why this spec exists

PR1 (the auth seam) and PR2a (IAP JWT verification + display-only identity in the topbar)
are merged on `feat/iap-access-control`. The verified identity exists but is **not yet
enforced** and carries **no role**. The remaining work — and the explicit ask — is: turn
the verified email into a **role**, **gate the app on it (fail-closed)**, enforce the
**costly capability** (AI runs), and ship the **admin console** plus the **access-denied /
request-access** page from the design handoff.

The user's hard requirement, verbatim: *"We can't afford to fuck up — don't follow the
happy path, this needs to work at all times."* Every section below is written to that bar:
fail-closed, default-deny, server-side enforcement, and no UI that promises something the
backend can't deliver.

## Decisions locked (from the 2026-06-14 brainstorm)

1. **Google owns the gate; the app owns roles only.** IAP + a Google Group/domain decides
   *who can reach the app*. The app's `user_roles` table decides *what a reached user may
   do*. **The app never modifies the Google Group** (no Admin SDK / Cloud Identity
   credential — that surface is deliberately not taken on). This keeps ADR 0078's non-goal
   ("managing the allow-list from inside the app") intact.
2. **"Add member" = pre-assign a role**, not a gate edit. The console assigns a role to an
   email and **reminds the admin that a human must also add them to the Google Group**.
   Such a row is `status='invited'` until that person's first verified sign-in.
3. **"Request access" = an in-console pending request.** A reached-but-unroled user can
   record a request (`status='requested'`); admins action it in the console. **No email is
   promised** (no email infra today); the prototype's *"you'll get an email"* copy is
   reworded to *"an admin will review your request."* Email is a clearly-future option.
4. **Four roles, permissions derived from role** (one source of truth):

   | Role | View (V) | Publish (P) | Run AI (A) | Manage access (M) |
   |---|:--:|:--:|:--:|:--:|
   | **Viewer** | ✓ | | | |
   | **Publisher** | ✓ | ✓ | | |
   | **Annotator** | ✓ | ✓ | ✓ | |
   | **Admin** | ✓ | ✓ | ✓ | ✓ |

5. **Enforcement scope for this work:** the **default-deny gate** (no active role → no
   app), the **Admin-only console**, and **Annotator-required for AI runs** (the action
   that spends money, ships the Gemini key, and consumes the scarce CatDV seat). **Publish
   (P) and View (V) are stored, displayed, and exposed via a `has_permission()` helper, but
   not yet wired onto existing publish/read routes** — that is a fast follow, made
   mechanical by the helper + dots already being in place.
6. **Brand = "CatDV Annotator"** everywhere, including the existing `access.html` (today it
   says "Archive AI" — realign it).

## What is already built (do not rebuild)

- `backend/app/auth/` seam: `get_current_user()` / `resolve_user()` → `CurrentUser(email,
  role)`, backend chosen by `settings.auth_backend` (`dev` | `iap`). Boundary enforced by
  `tests/unit/test_auth_seam_boundary.py` + (per ADR 0078) an import-linter contract.
- `adapters/iap.py`: **cryptographically verifies** the signed `X-Goog-IAP-JWT-Assertion`
  (signature against Google's IAP keys + `settings.iap_audience`), fail-closed; returns
  `CurrentUser(email=…)`. `adapters/dev.py`: returns `settings.dev_user_email`.
- `_attach_current_user` middleware (`main.py`): resolves identity **display-only** today
  (a failure degrades to anonymous), stashes `request.state.current_user`. The topbar
  renders "signed in as …".
- `/access` page (`routes/pages/access.py` + `templates/pages/access.html`): static
  `denied` / `error` cards (from the earlier `Login.dc.html` handoff). This spec **extends**
  it with the identity card + request-access flow.
- Settings: `app_env`, `auth_backend`, `dev_user_email`, `iap_audience`.

## Architecture

Two layers, one seam (unchanged from ADR 0078):

```
  Browser ──HTTPS──► IAP (Google: login + coarse gate = email ∈ Google Group)
                       │  injects signed X-Goog-IAP-JWT-Assertion
                       ▼
  Cloud Run ─► get_current_user() ─verify JWT─► email
                       │
                       ▼  look up role in user_roles (SQLite/Litestream)
              CurrentUser(email, role, permissions)
                       │
        ┌──────────────┼─────────────────────────────┐
   gate middleware   require_role("admin")     require_permission("run")
   (active role?)    (console + role writes)   (studio/sync runs, live key)
```

### Data model

New migration `backend/migrations/0020_user_roles.sql` (verify `0020` is the next free
number at implementation time — the runner refuses collisions with `*.txt` sentinels;
ADR 0044):

```sql
CREATE TABLE user_roles (
  email        TEXT PRIMARY KEY,                       -- the IAP-verified identity (lowercased)
  role         TEXT NOT NULL CHECK (role IN ('admin','annotator','publisher','viewer')),
  status       TEXT NOT NULL DEFAULT 'active'
                 CHECK (status IN ('active','invited','requested')),
  display_name TEXT,
  granted_by   TEXT,                                   -- admin email who set/changed it (audit)
  granted_at   TEXT NOT NULL DEFAULT (datetime('now')),
  last_seen_at TEXT
);
```

- New `backend/app/repositories/user_roles.py`, mirroring the existing ~20 repos (a leaf —
  no service imports; list reads through `_batch.chunked_in_clause` if a list-of-keys read
  is ever needed). Methods: `get(email)`, `list(filters)`, `upsert_role(email, role, *,
  status, granted_by, display_name)`, `delete(email)`, `count_admins()`,
  `touch_last_seen(email)`.
- **Email is normalized to lowercase** on every read and write so identity comparisons
  (and self-protection) can't be defeated by case.
- `status` lifecycle: `requested` (user asked) → admin grants → `invited` (role assigned,
  awaiting first sign-in) → first verified request flips it → `active`. An admin granting a
  role to an already-active user just updates `role`.

### Role → capabilities (single source of truth)

A `ROLE_CAPS` map lives in the auth package (e.g. `backend/app/auth/roles.py`) and is the
**only** place the role→permission mapping is defined — consumed by the gate, the
`has_permission()` helper, the permission dots, and the tests:

```python
ROLE_CAPS = {
    "admin":     {"view", "publish", "run", "manage"},
    "annotator": {"view", "publish", "run"},
    "publisher": {"view", "publish"},
    "viewer":    {"view"},
}
```

`CurrentUser` gains a `permissions: frozenset[str]` (derived from `role` via `ROLE_CAPS`)
and `has(cap) -> bool`. `resolve_user()` populates `role`/`permissions` by looking the
verified email up in `user_roles` after the adapter establishes identity.

### The gate — app-wide default-deny (opt-out), not per-route (opt-in)

Enforcement is an **app-wide middleware**, chosen deliberately over per-route `Depends`:
with an allow-list, forgetting a *public* path is harmless; with per-route dependencies,
forgetting a *protected* route fails **open**. Default-deny everything; exempt a tiny,
explicit allow-list.

- Extend `_attach_current_user` (or add a sibling gate middleware that runs after it) so
  that **when enforcement is active** it:
  1. resolves identity — any resolution error (including `NotAuthenticated` /
     config `RuntimeError`) → **deny**, never admit;
  2. looks up the role; **no row, or `status != 'active'` → deny**;
  3. on deny, renders `templates/pages/access.html` with **HTTP 403** (HTML for a browser
     navigation; a JSON `{"detail": …}` 403 for `Accept: application/json` / `HX-Request`
     so HTMX/fetch callers get a clean error, not an HTML blob).
- **Allow-list (reachable without an active role):** `/static/*`, `/api/health`,
  `GET /access`, `POST /access/request`. Nothing else.
- **When is enforcement active?** When `settings.auth_backend == 'iap'` (cloud). Under
  `dev`, the single configured operator (`dev_user_email`) is treated as **admin** so local
  development stays frictionless and no IAP code path is exercised (acceptance flow 9 of the
  06-13 spec). This avoids a footgun where local dev locks itself out with an empty
  `user_roles` table.
- **Bootstrap / root of trust:** a new setting `admin_emails: list[str]` (deploy env) is
  seeded **idempotently at startup** into `user_roles` as `admin`/`active`. The first admin
  therefore exists before the console does, and **no one can self-promote to the first
  admin from inside the app**. Re-seeding never downgrades an existing admin and never
  removes a manually-added one.

### Capability enforcement at routes (this work)

Small fail-closed helpers (FastAPI dependencies or inline guards, matching the inline
`get_core_ctx(request)` style already used):

- `require_role("admin")` — on the admin console page + every role-write endpoint.
- `require_permission("run")` — on the AI-run surfaces:
  - `POST /studio/runs` (`routes/studio.py`)
  - `POST /sync/run` (`routes/sync.py`)
  - `GET /live/session-config` (`routes/live.py`) — this is the route that ships the Gemini
    key to the browser, so it is gated as a run capability.

Each helper denies (403) on missing capability and on any error.

## The two surfaces

Both are re-expressed with **this app's** shared UI library and design tokens — **not** the
prototype's inline React/CSS (CLAUDE.md "explore before implementing"; the design-language
guard `tests/unit/test_design_language_guard.py` fails CI on hand-rolled primitives). The
prototype already converged on CatDV's dark/amber (`--accent`)/green language, so the token
mapping is direct.

### Admin console — `routes/pages/admin.py` + `templates/pages/admin.html`

- Extends `pages/layout.html`. A new **"Admin"** topbar nav link appears **only when**
  `current_user.has("manage")`.
- Page chrome via `ui.page_header` ("Access & Permissions", subtitle "Manage who can reach
  CatDV and what each member is allowed to do"). The two info cards: **Identity-Aware
  Proxy — Enforced** ("Google verifies every visitor's identity at the edge before any
  request reaches CatDV") and **Application roles · IAM** ("roles below are stored in the
  app and control in-app permissions"). Wording is corrected from the prototype's
  misleading "added to the IAP access list."
- Members table (HTMX-driven; filters and row actions return partials — **never**
  `location.reload()`):
  - **Member**: avatar (initials), name + `YOU` chip on the current user's row, email (mono).
  - **Role**: a role pill; change via `ui.menu` / `ui.menu_item` (+ `popover()`), **never a
    new `*-menu` class**.
  - **Permissions**: V·P·A·M dots — a new small partial `templates/pages/_perm_dots.html`
    built from tokens (lit = `--accent`/green, dimmed = muted). Derived from role via
    `ROLE_CAPS`; display-only.
  - **Last sign-in**: relative time from `last_seen_at` (reuse `format.js` helpers; `—`
    when never).
  - **Status**: `ui.status_pill` (active / invited / requested).
- **Add member** → `ui.modal` + `.modal-body` / `.modal-actions`, fields via `ui.field`
  (email, optional display name) + a role picker. Validates + dedupes the email. Copy makes
  the Group caveat explicit. On submit: `POST /admin/users` → upsert `invited` row → HTMX
  partial swap + `Alpine.store('toast').push(..., {level:'success'})`.
- Row actions: change role (`PATCH /admin/users/{email}`), revoke (`DELETE
  /admin/users/{email}`). Bulk-revoke over selected rows.
- **Safety (server-side, not just hidden in UI):**
  - **Self-protection** — the current user cannot change or revoke their own row; the
    endpoints reject it (403) even if the UI is bypassed.
  - **Last-admin guard** — `count_admins()` is checked before any demotion/revoke of an
    admin; removing/demoting the final admin is refused (409 + clear message). This is what
    makes "you can't lock everyone out" true.
- Errors surface via the toast store; never `alert()` / silent `.catch()`.

### Access denied / request access — extend `access.py` + `access.html`

- Keep the standalone page (does **not** extend `layout.html` — an unauthorized user sees
  no nav/topbar). Rebrand "Archive AI" → "CatDV Annotator".
- Add the design's **identity card** (avatar + name + email + "Signed in") so the user sees
  it's an *authorization* gap, not a failed login.
- **Request access**: a button → `POST /access/request` (allow-listed) → upserts a
  `requested` row keyed by the verified email → returns the quiet confirmation partial:
  *"Request sent. An admin will review it."* Plus **Try again** (reload) and **Switch
  account** (`?gcp-iap-mode=CLEAR_LOGIN_COOKIE` — verify this IAP param at wiring time).
- Keep the existing `error` state. The route stays on the gate's allow-list (gating it would
  loop).
- An admin contact line (`mailto:`) using the first configured `admin_emails` entry.

## Security guards (each backed by a test)

| Risk | Guard | Test |
|---|---|---|
| Spoofed identity | Trust only the verified JWT; plaintext `X-Goog-Authenticated-User-Email` ignored | existing `test_iap_adapter.py` |
| Fail-open on error | Gate denies on **any** exception → 403 | new gate test |
| Privilege escalation | Role read server-side by verified email; all writes `require_role("admin")` | new |
| Self-lockout | Self-protection rejected server-side | new |
| No admins left | Last-admin guard (refuse final-admin demote/revoke) | new |
| Forgotten protected route | Default-deny middleware + explicit allow-list (assert a sample route 403s without a role; assert each allow-listed path is reachable) | new |
| AI cost / seat / key abuse | `require_permission("run")` on studio/sync runs + live key route | new |
| Seam leak (IAP specifics escape adapters) | existing boundary test + import-linter contract | existing, extended |
| Bootstrap integrity | `admin_emails` seed is idempotent; never downgrades an admin | new |

## Slicing (tracer-bullet vertical slices)

- **PR2b — roles + gate + AI-run enforcement.** Migration `0020_user_roles.sql`,
  `repositories/user_roles.py`, `auth/roles.py` (`ROLE_CAPS`), `CurrentUser.permissions` +
  `has()`, role lookup in `resolve_user`, `admin_emails` setting + idempotent startup seed,
  the gate middleware + allow-list, `require_role`/`require_permission`, AI-run enforcement
  on the three routes. Active only under `auth_backend == 'iap'`; `dev` operator = admin.
  Per-user **attribution** stamping (Goal 4 of the 06-13 spec) can ride here or as its own
  slice. Fully tested; behaviour-neutral in `dev`.
- **PR3 — admin console + access-request.** `admin.py` + `admin.html` (+ `_perm_dots.html`),
  HTMX CRUD with self-protection + last-admin guard, the topbar Admin link, and the
  `access.html` request-access extension. Reuses `ui.modal` / `ui.field` / `ui.menu` /
  `ui.button` / `ui.status_pill` / `ui.page_header`.
- **Fast follow (not this work):** wire Publisher-gated publish (P) and Viewer read-only
  (V) onto the existing publish/read routes using the same helper.
- **PR4 — cutover** (`--iap` enable + IAM grants + CI verify rework): unchanged, already
  scoped in the 06-13 spec.

## Open items to confirm at implementation time

- Exact next migration number (`0020` expected; runner refuses sentinel collisions).
- The `?gcp-iap-mode=CLEAR_LOGIN_COOKIE` sign-out param (verify against a live IAP session,
  same discipline as the JWT audience discovery).
- Whether per-user attribution stamping lands in PR2b or its own slice.

## Manual acceptance flows

1. **Roled user uses the app.** Setup: email in the Google Group + an `active` role row.
   Action: open the app URL. Expected: app loads; topbar shows the right email; an admin
   additionally sees the "Admin" link.
2. **Reached-but-unroled user is denied (fail-closed).** Setup: email in the Group, **no**
   `user_roles` row. Action: open any app URL. Expected: 403 + the Access Denied page (not a
   stack trace, not a blank app); a JSON/HTMX caller gets a 403 JSON body.
3. **Request access.** Continuing (2): click **Request access**. Expected: quiet "Request
   sent. An admin will review it." confirmation; a `requested` row now exists; **no** email
   is claimed.
4. **Admin grants the request.** Setup: sign in as admin. Action: open Admin → find the
   requested user → assign a role → save. Expected: HTMX partial swap + success toast (no
   `location.reload()`); the user's next request is admitted.
5. **Add member (pre-assign).** Action: admin clicks Add member, enters a new email + role,
   submits. Expected: `invited` row created; the modal copy made clear a Google-Group add is
   still required; on that person's first verified sign-in the row flips to `active`.
6. **Self-protection.** Action: an admin tries to revoke or demote **their own** row (incl.
   via a crafted request bypassing the UI). Expected: refused (403); their access is intact.
7. **Last-admin guard.** Setup: exactly one admin. Action: try to demote/revoke that admin.
   Expected: refused (409 + clear message); at least one admin always remains.
8. **AI-run capability.** Setup: sign in as a **Viewer** or **Publisher**. Action: attempt a
   Studio run / `POST /sync/run` / fetch `/live/session-config`. Expected: 403 — no run
   starts, no Gemini key is served, the CatDV seat is untouched. As **Annotator/Admin**: the
   run proceeds.
9. **No forgotten protected route.** Action: with no active role, hit a representative
   protected route directly. Expected: 403. Then hit each allow-listed path (`/api/health`,
   `GET /access`, `POST /access/request`, a `/static` asset): reachable.
10. **Bootstrap.** Action: deploy with `ADMIN_EMAILS` set, empty `user_roles`. Expected: the
    listed emails are `admin`/`active` at first boot; re-deploy does not duplicate or
    downgrade them.
11. **Local dev still works.** Action: run locally with `AUTH_BACKEND=dev`. Expected: the
    app is fully usable as admin on `127.0.0.1`; no IAP path exercised; the gate does not
    lock the operator out.
12. **Cold start preserves roles.** Setup: let the service scale to zero. Action: a roled
    user returns after idle. Expected: no re-login (Google holds the session); their role is
    intact (restored from Litestream); only cold-start latency is paid.
