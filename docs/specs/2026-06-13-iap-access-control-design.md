# IAP access control (team authentication + in-app authorization)

**Date:** 2026-06-13
**Status:** Draft (design) — decisions marked **OPEN** below need confirmation.
Login states 3–4 (the app-rendered cards) are implemented on branch
`feat/iap-access-control` — see [Implemented so far](#implemented-so-far).
**Supersedes the "auth is deferred" stance of** `2026-06-09-cloud-run-deployment-design.md`
(which named this work *"phase 5: multi-user auth (IAP or in-app login)"*).
**Decision record:** ADR 0078.

## Problem

The Cloud Run service is private (`--no-allow-unauthenticated`); the only
way in is `gcloud run services proxy` with `roles/run.invoker` granted to
one operator. That is unusable as a team experience: there is no browser
sign-in, no notion of *who* a user is, and no way to grant a colleague
access without an IAM edit.

We want a **known team** to open a URL, sign in with Google, and have the
app know who they are and what they may do (roles / permissions, managed
from an in-app admin page) — without lowering the reliability or security
of the deployment, and without closing doors on futures we can't yet see
(multiple archives, multiple Gemini keys, teams, cloud *or* local
deployment).

## Goals

1. A team member opens the app URL, authenticates with Google, and is
   admitted iff they are on an allowed list managed **in Google** (Group
   or Workspace domain).
2. The app receives a **verified** identity (email) for every request.
3. **Authorization** (roles / permissions) lives in the app and is editable
   from an **admin page**.
4. **Per-user attribution**: actions (annotations, runs, jobs) record *which
   person* performed them — CatDV cannot, because every write goes through
   one shared `klientAI` seat.
5. The auth mechanism sits behind **one seam** so that IAP↔app-OAuth and
   cloud↔local stay swappable with *bounded, localized* effort.
6. The access gate **cannot fail open** and **survives scale-to-zero**.

## Non-goals

- **Horizontal scale / per-user backend isolation.** Three deliberate
  singletons cap concurrency: one CatDV seat, one Litestream writer + one
  in-process write queue, and `--max-instances=1` (ADR 0066, load-bearing).
  The target is *a known team sharing one backend*, not internet-scale.
  Scaling out needs the Postgres migration first; out of scope here.
- **Per-user CatDV logins.** The 2-seat license forbids it; writes stay
  shared-seat, attribution is app-side (Goal 4).
- **Managing the allow-list from inside the app.** Who-can-*reach*-the-app
  stays in Google (Group/domain). The app manages *roles*, not the gate.
- **The Gemini-Live key fix.** Separate work, but a **blocking prerequisite**
  — see [Dependencies](#dependencies--blocking-prerequisites).
- **UI / visual design of the admin page.** Owned by a separate design track.

## Decisions already fixed (cross-cutting)

- **Mechanism: Google Cloud IAP (Option A)**, not app-level OAuth (Option B).
  Full rationale in **ADR 0078**. In one line: the gate is operated by
  Google, independent of our code, so it cannot fail open, survives cold
  starts, and minimizes the security-critical code we own.
- **Two layers, cleanly separated:**
  - *Authentication + coarse gate* → IAP + a Google Group/domain
    (Google-managed; the app holds nothing here).
  - *Authorization* → an **app-side roles table** (SQLite, Litestream-
    replicated). IAP says *who you are*; the app decides *what you may do*.
- **The seam.** The whole app reads identity through a single
  `get_current_user()` dependency. **Only the auth-adapter module may touch
  IAP/OAuth specifics.** Enforced by a guard test + import-linter contract,
  in the same spirit as `tests/unit/test_context_delegation.py` and the
  `.importlinter` contracts.
- **Fail closed.** Any error resolving identity or role → deny (403), never
  admit. Mirrors the error discipline of ADR 0042.

## Architecture

```
  Browser
    │  HTTPS
    ▼
  IAP  (enabled DIRECTLY on the Cloud Run service: `gcloud run … --iap`)
    │   Google runs the login + the coarse gate (email ∈ Google Group/domain)
    │   fronts ALL ingress paths incl. the auto-assigned *.run.app URL
    │   injects signed X-Goog-IAP-JWT-Assertion
    ▼
  Cloud Run  (--no-allow-unauthenticated; IAP service agent holds run.invoker)
    │
    ▼
  app: get_current_user()  ── verifies the signed IAP JWT ──►  CurrentUser(email, role)
    │                                                              │
    │                                            roles table (SQLite / Litestream)
    ▼                                                              │
  routes / services  ── authorize on role ──►  admin page edits roles table
```

- **Edge.** IAP is enabled **directly on the Cloud Run service**
  (`gcloud run services update catdv-annotator --iap`) — **no load balancer,
  no serverless NEG, no managed cert, no added cost** (GA 2026; Google's
  recommended approach over the LB path). IAP fronts *every* ingress path,
  including the auto-assigned `*.run.app` URL, so there is no public bypass to
  lock down. The service stays `--no-allow-unauthenticated`; the **IAP service
  agent** (`service-PROJECT_NUMBER@gcp-sa-iap.iam.gserviceaccount.com`) holds
  `run.invoker`. The JWT audience is derived from the project/service (exact
  format for *direct* Cloud Run IAP confirmed against a real token at
  implementation time, not guessed).
- **Identity.** IAP injects a **signed** `X-Goog-IAP-JWT-Assertion`. The app
  verifies its signature (against Google's IAP public keys) and audience
  (the exact backend-service id) before trusting it, and extracts the email.
  The plaintext `X-Goog-Authenticated-User-Email` header is **never** trusted
  on its own.
- **Authorization.** A `user_roles` table maps `email → role`.
  `get_current_user()` returns a `CurrentUser(email, role, permissions)`.
  Routes/services authorize on that; unknown emails default-deny.
- **Attribution.** The authenticated email is stamped onto records the app
  already writes (annotations / runs / jobs), since CatDV sees only `klientAI`.

## The seam (how doors stay open)

A new `backend/app/auth/` package:

- `identity.py` — the `CurrentUser` dataclass and the `get_current_user()`
  FastAPI dependency. This is the *only* symbol the rest of the app imports.
- `adapters/iap.py` — reads + cryptographically verifies the IAP JWT (cloud).
- `adapters/dev.py` — local/dev backend (trusted header or single-operator
  no-auth-behind-VPN; **OPEN** which).
- (future) `adapters/oauth.py` — app-level OAuth, if Option B is ever wanted.

The backend is chosen by a settings value (`AUTH_BACKEND`: `iap` | `dev` |
…). **Enforced rule:** only files under `backend/app/auth/adapters/` may
reference IAP/OAuth specifics; everything else goes through
`get_current_user()`. A guard test + an `.importlinter` contract fail CI on
violation.

**Why this keeps the named doors open:**

| Future | What it costs, given the seam |
|---|---|
| Multiple archives | Nothing here — already a port (`ArchiveProvider` + registry). |
| Multiple Gemini keys | App-side config/lookup; auth-orthogonal. |
| Teams | The `user_roles` table grows into tenancy; the gate is untouched. |
| Cloud **or** local | Pick a different adapter behind the seam (IAP in cloud, `dev`/OAuth locally) via `AUTH_BACKEND`. One small adapter, not a rewrite. |
| Swap IAP → app-OAuth wholesale | Add `adapters/oauth.py` + login routes, flip the flag, tear down the LB. No route changes. |

## Reliability & scale-to-zero

- IAP holds the session, so a **cold start (min-instances=0, ADR 0077)** does
  not re-prompt login and cannot lose session state — the app is stateless
  with respect to authentication.
- The roles table is restored from the Litestream replica on cold start;
  it is low-churn, so the single-writer model is a non-issue. It inherits
  the *same* drain-overlap accepted risk already documented in ADR 0077.
- Because the gate is Google's and lives **in front of** the instance, an app
  crash or mid-cold-start cannot fail open.

## Security — the can't-fuck-up requirements

These are hard requirements, not nice-to-haves. Each gets a guard or an
acceptance flow.

1. **`/api/health` + platform probes.** Cloud Run's own startup/liveness
   probes hit the container *internally* and are **not** routed through IAP,
   so enabling IAP does not break the platform health checks. The affected
   caller is the *external* CI verify step (see CI section). Keep `/api/health`
   cheap and unauthenticated at the app layer regardless.
2. **No public bypass.** With direct IAP, IAP fronts *all* ingress paths
   (including the auto-assigned `*.run.app` URL), the service stays
   `--no-allow-unauthenticated`, and `run.invoker` is granted only to the IAP
   service agent — so there is no unprotected path to the container. Revoke the
   broad operator `run.invoker` grant after cutover (keep one break-glass).
   (Optional defense-in-depth: ingress restrictions may still be layered.)
3. **Verify the signed IAP JWT** (signature + audience = the backend-service
   id). Do **not** trust `X-Goog-Authenticated-User-Email` alone — defense in
   depth with #2.
4. **Fail closed.** Any exception in `get_current_user()` or the role lookup
   returns 403; it must never admit on error.
5. **Default-deny.** An authenticated email with no role row gets the lowest
   privilege (or no access — **OPEN**, see roles model), never implicit admin.

## CI / deploy interaction (must resolve before cutover)

Today the workflow's *Verify /api/health* step curls the Cloud Run URL
directly with an impersonated ID token (works because the service requires
auth). Once `--iap` is on, IAP fronts that URL too, so the call is
**intercepted** unless it carries an IAP-accepted credential.

- **Plan (pick one):** (a) grant the deployer SA
  `roles/iap.httpsResourceAccessor` on the service and have the verify step
  mint an OIDC token for the IAP OAuth-client audience; or (b) move health
  verification to a Cloud Run **startup probe** (internal, unaffected by IAP)
  and drop the external curl.
- **Deploy:** add `--iap` to the `gcloud run deploy` command so IAP stays
  declaratively on across revisions; add the IAP-service-agent `run.invoker`
  grant to one-time setup.
- **Guards:** extend `tests/unit/test_deploy_workflow_scaling.py` (or a
  sibling) to assert `--iap` is present, mirroring how it pins `min=0/max=1`.
- **OPEN:** confirm the exact IAP programmatic-access token mechanics for (a)
  in the GitHub Actions environment (WIF → impersonation → IAP audience).

## Dependencies / blocking prerequisites

- **ADR 0043 (Gemini-Live key in the browser).** Multi-user breaks its
  explicit single-operator-on-VPN threat model: the raw `GEMINI_API_KEY` is
  shipped to *every* signed-in browser. This **must** be resolved before the
  app is opened to real multiple users — via the backend-WSS-proxy redesign
  (Alternative 2 in ADR 0043). Tracked as separate spec + ADR. **Interim
  option:** disable the Live assistant for non-operator roles until the proxy
  lands. This spec assumes one of those two before cutover.

## Open decisions (need confirmation — some await the design chat)

1. **Roles model.** Proposed minimal start: `admin` and `member`
   (admin = manage roles + everything a member can do; member = use the app).
   Alternative: `admin / editor / viewer`. Need the permissions matrix.
2. **Coarse gate.** Google **Group** vs whole **Workspace domain** — depends
   whether the team is on a Workspace domain or personal `@gmail.com`
   accounts. Group is the flexible default if a Workspace exists.
3. **First-admin bootstrap.** Proposed: an `ADMIN_EMAILS` setting seeded
   idempotently into `user_roles` at startup, so the first admin exists
   before anyone can use the admin page.
4. **Custom domain (optional now).** Direct IAP works on the auto-assigned
   `*.run.app` URL, so a custom domain + cert is a nice-to-have, not a
   requirement. (The OAuth consent screen still needs configuring if the
   project has no Google Workspace org / has external users.)
5. **Local (`dev`) auth backend.** Trusted-header shim vs no-auth-behind-VPN.
   Defer the concrete choice until local-with-auth is actually wanted.

## Implemented so far

The app-rendered login states from the Claude-Design handoff
(`Login.dc.html`) — **state 3 "Access not granted"** and **state 4 "Error"** —
are built. The sign-in + "Redirecting" states are *not* app pages: Google/IAP
owns them upstream (so an app-rendered "Sign in with Google" button would be
dead UI).

- `backend/app/templates/pages/access.html` — standalone page (does **not**
  extend `pages/layout.html`, so an unauthorized user sees no nav rail /
  topbar). One template, two states via a `state` var.
- `backend/app/static/app.css` — `.auth-*` block. The design was mocked dark
  with an **indigo** accent against the *Archive-AI-PoC* (React/Tailwind) repo;
  re-expressed here with this app's **tokens** (amber `--accent`), the shared
  **`.btn`** system, and no class names that trip the design-language guard.
- `backend/app/routes/pages/access.py` — `GET /access?state=denied|error&email=`
  render endpoint (so the page is reviewable now). When the gate lands,
  `get_current_user` renders `pages/access.html` directly with a 403; `/access`
  must then be on the gate's allow-list.
- "Use a different account" → `?gcp-iap-mode=CLEAR_LOGIN_COOKIE` (IAP sign-out
  → fresh Google sign-in). **Verify this IAP param at wiring time**, same as
  the JWT audience.
- Branding: the card says **"Archive AI"** (matching the design), while the
  rest of the app chrome says "CatDV Annotator" — flagged for the brand call.
- Tests: `tests/integration/test_access_page.py` (3 cases). Full suite green.

### PR2a — IAP verification + identity in the topbar (✅ code done)

- `adapters/iap.py` now **cryptographically verifies** the signed
  `X-Goog-IAP-JWT-Assertion` (signature via Google's IAP public keys +
  `settings.iap_audience`) and returns `CurrentUser(email=...)`. Fail-closed:
  missing header / bad signature / no-email → `NotAuthenticated`; unset audience
  → `RuntimeError` (never verify against an empty audience). `auth/errors.py`
  adds `NotAuthenticated`. `google-auth` is now an explicit dep.
- Settings: `iap_audience: str | None`.
- `main.py` `_attach_current_user` middleware resolves identity once per request
  (narrow except: only `NotAuthenticated`/`RuntimeError` degrade to anonymous —
  display-only; gating is PR2b) and stashes it on `request.state.current_user`.
- `pages/layout.html` renders **"signed in as …"** in the topbar (`.topbar-user`).
- Tests: `tests/unit/test_iap_adapter.py` (6, verify_token mocked) +
  `tests/integration/test_topbar_current_user.py`. Two pre-existing live-route
  test fakes (`type("S", …)`) updated to include the new settings fields. Full
  suite green (1443).
- **Cloud-activation remaining (one value):** the exact JWT **audience** for
  *direct* Cloud Run IAP is not authoritatively documented (Google's docs give
  the load-balancer `backendServices` form, which may not apply). So the value
  is **discovered from a real token**, not guessed: deploy with `AUTH_BACKEND=iap`
  + empty `IAP_AUDIENCE`, do a one-time signature-only decode of a live assertion
  to log its `aud`, then set `IAP_AUDIENCE` and redeploy. Until then cloud shows
  no identity (app otherwise unaffected). `cloudrun.env.yaml` is **not** flipped
  yet to avoid a half-configured deploy.

## Implementation plan (slices — for a later session, not this one)

> Tracer-bullet vertical slices; each independently reviewable. PR3 (admin
> page) coordinates with the separate design track.

- **PR1 — seam. ✅ DONE.** `backend/app/auth/` — `models.py` (`CurrentUser`),
  `identity.py` (`resolve_user` + the `get_current_user` dependency, fail-closed
  dispatch), `adapters/dev.py` (local single-operator identity from
  `dev_user_email`), `adapters/iap.py` (fail-closed placeholder; reads the
  `X-Goog-IAP-JWT-Assertion` constant but refuses to trust it unverified — PR2).
  Settings: `auth_backend` (`dev`|`iap`, default `dev`) + `dev_user_email`.
  Boundary enforced by `tests/unit/test_auth_seam_boundary.py` (IAP user-identity
  markers only inside `auth/adapters/`); behaviour tests in
  `tests/unit/test_auth_seam.py`. **Not yet wired into routes** — behaviour-neutral.
- **PR2 — IAP + roles.** `adapters/iap.py` (JWT verify), `user_roles` table +
  migration, wire `get_current_user()` into routes with default-deny, behind
  a feature flag. Per-user attribution stamping (Goal 4) can ride here or in
  its own slice.
- **PR3 — admin page.** Roles CRUD against `user_roles`, reusing the shared
  UI library (`ui.modal`, `ui.field`, `ui.menu`, `ui.button`, …). Design from
  the separate track.
- **PR4 — IAP enable + cutover.** Enable IAP directly on the service
  (`--iap`); grant the IAP service agent `run.invoker`; grant the
  Group/domain `iap.httpsResourceAccessor`; configure the OAuth consent
  screen; add `--iap` to the deploy workflow + the CI-verify auth change.
  Revoke the broad operator `run.invoker` grant. **No load balancer.**
- **Prereq PR — ADR 0043 Gemini fix** (or interim Live gating) before PR4
  cutover.

## Manual acceptance flows

1. **Allowed user signs in.** Setup: email is in the Google Group; a `member`
   role row exists. Action: open the app URL in a fresh browser. Expected:
   Google sign-in → redirected back → app loads; a "signed in as
   `<email>`" indicator shows the right person.
2. **Disallowed user is blocked.** Setup: an email *not* in the Group. Action:
   open the URL. Expected: IAP refuses; the app is never reached.
3. **Authenticated-but-unroled user.** Setup: email is in the Group but has no
   `user_roles` row. Action: open the URL. Expected: default-deny behaviour
   per decision #1 (lowest privilege or a clear "no access" page) — never
   implicit admin.
4. **Admin manages roles.** Setup: sign in as an `admin`. Action: open the
   admin page, change a colleague's role, save. Expected: HTMX partial swap +
   success toast (no `location.reload()`); the colleague's effective
   permissions change on their next request.
5. **No public bypass.** Action: open the auto-assigned `*.run.app` URL in a
   fresh browser with no IAP session. Expected: IAP intercepts and demands
   sign-in; the container is never reached unauthenticated.
6. **Platform health + deploy verifies.** Action: run the deploy workflow.
   Expected: Cloud Run's startup probe (internal, not via IAP) passes; the
   external *Verify* step authenticates to IAP and gets 200 (or has been
   replaced by the startup probe).
7. **Cold start preserves access.** Setup: let the service scale to zero.
   Action: an already-signed-in user returns after idle. Expected: no
   re-login (Google holds the session); their role is intact (roles table
   restored from Litestream); first request just pays the cold-start latency.
8. **Per-user attribution.** Action: two different signed-in users each
   accept a draft / run a job. Expected: each record shows the acting user's
   email, even though CatDV saw only `klientAI`.
9. **Local dev still works.** Action: run the app locally with
   `AUTH_BACKEND=dev`. Expected: the app is usable on `127.0.0.1` without
   IAP; `get_current_user()` is satisfied by the `dev` adapter; no IAP code
   path is exercised.
