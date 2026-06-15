# 0082. App-side roles + admin console on top of IAP (4-role model, default-deny gate, app-never-touches-the-Group)

**Date:** 2026-06-14
**Status:** Accepted (implemented on `feat/iap-access-control`)
**Spec:** `docs/specs/2026-06-14-iap-roles-admin-console-design.md`
**Builds on:** ADR 0081 (IAP as the gate + an app-side roles layer). This ADR records the
authorization decisions left **open** in 0081 and the Claude-Design handoff
(`admin-panel-ai-archive`: `Admin Console.dc.html` + `Access Denied.dc.html`).

## Context

ADR 0081 fixed the *mechanism* (Google IAP gates the app; a small app-side roles table
decides authorization) but left several calls open: the roles model, what "add member" /
"request access" actually do, how enforcement is wired, and how cloud vs local differ. The
user's hard requirement was *"can't afford to fuck up — don't follow the happy path, this
needs to work at all times"*: fail-closed, default-deny, no UI that promises something the
backend can't deliver. The design handoff (mocked against CatDV's dark/amber/green language)
assumed the app owned the access list ("Add member → added to the IAP access list"), which
conflicts with 0081's non-goal of managing the gate inside the app.

## Alternatives

- **Who owns "who can reach the app"?** (a) **Google owns the gate, app owns roles only**
  (chosen) — keeps 0081's non-goal intact, smallest security-critical surface; (b) app owns
  the access list (IAP gates a wide domain, app table decides access) — makes the design's
  invite/request flows fully self-service but widens exposure and makes the app table
  load-bearing for *access*; (c) automate the Google Group from the app via the Admin SDK —
  real one-click invites but the app would hold a powerful group-admin credential. The user
  chose (a), and explicitly chose to **never** have the app modify the Group.
- **Role model:** 2 roles (`admin`/`member`, 0081's strawman) vs the design's **4 roles**
  (Admin/Annotator/Publisher/Viewer with V·P·A·M). Chose 4.
- **Enforcement scope now:** gate + admin only · gate + admin + **AI runs** · full (also
  Publisher-gated publish, Viewer read-only). Chose the middle.
- **Gate mechanism:** app-wide **default-deny middleware** (opt-out allow-list) vs per-route
  dependency (opt-in). Chose middleware — forgetting a *public* path is harmless; forgetting
  a *protected* route would fail open.
- **"Request access" notification:** real email vs in-console pending. Chose in-console (no
  email infra; don't promise what we can't deliver).

## Decision

1. **Google owns the gate; the app owns roles only. The app never modifies the Google
   Group.** "Add member" pre-assigns a role (`status='invited'`) and the console reminds the
   admin to also add the person to the Group; "Request access" records an in-console pending
   request (`status='requested'`) — no email is promised.
2. **Four roles, permissions derived from role** via one `ROLE_CAPS` map (`auth/roles.py`):
   Admin `V·P·A·M`, Annotator `V·P·A`, Publisher `V·P`, Viewer `V`. `CurrentUser.permissions`
   is a derived property — no stored permission state to drift.
3. **One `user_roles(email, role, status, display_name, granted_by, granted_at,
   last_seen_at)` table** (SQLite/Litestream), a leaf `UserRolesRepo`. `status`:
   `active`/`invited` admit at the gate; `requested` does not (denied until granted).
4. **App-wide default-deny gate** (`_auth_gate` middleware): under `AUTH_BACKEND=iap`, any
   non-allow-listed request without an active role → 403 (the access page for browsers, JSON
   for HX/fetch). Allow-list = `/static/`, `/api/health`, `/access`, `/favicon.ico`. **Fail
   closed:** any error resolving identity or the role → deny. Identity is resolved before the
   allow-list check (so `/access` shows who you are) but the DB role lookup happens only
   after it (health probes don't hit the DB).
5. **Enforce now:** the gate + Admin-only console (`require_role("admin")`) +
   **Annotator-required AI runs** (`require_permission("run")` on `POST /api/jobs`,
   `POST /studio/runs`, `GET /live/session-config` — the cost/seat/Gemini-key surface).
   Publisher-gated publish and Viewer read-only are a deliberate fast-follow (helper + dots
   already in place). `POST /sync/run` is writeback *drain* (publish), not gated here.
6. **Server-side safety:** self-protection (you can't re-role/revoke your own row) and a
   last-admin guard (refuse to demote/revoke the final admin) — enforced in the route, not
   just the UI.
7. **Cloud vs local via `AUTH_BACKEND`:** cloud = `iap` (verify JWT + enforce); local =
   `dev` (single operator is implicit **admin**, gate off, no IAP path — local dev is never
   locked out). A `Settings` validator refuses to boot when `app_env=prod` and
   `auth_backend!='iap'`, so the cloud can't accidentally run ungated. First admin(s) seeded
   idempotently at startup from `ADMIN_EMAILS` — the deploy-time root of trust.

## Consequences

- The app's role table is load-bearing for *authorization* but never for *access* (the gate
  stays Google's), keeping the security-critical surface small and the "Add member" UI
  honest about the Group step.
- The 4-role model + derived permissions give a single source of truth shared by the gate,
  the dots, the guards, and the role picker.
- Default-deny middleware means new routes are protected by default; only the tiny allow-list
  is opt-out. Enforced by tests (unroled → 403, allow-list reachable, run gate, self/last-
  admin guards) and the existing seam boundary + import-linter contracts.
- Publish/View enforcement is not yet wired on existing routes — a known, scoped follow-up.
- Cutover (PR4) still needs the live `IAP_AUDIENCE` discovered from a real token and
  `AUTH_BACKEND=iap` + `ADMIN_EMAILS` set in `cloudrun.env.yaml` (tracked in the 06-13 spec).
