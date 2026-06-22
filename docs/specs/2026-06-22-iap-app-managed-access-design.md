# App-managed access: open the IAP edge, let the app be the sole bouncer

**Date:** 2026-06-22
**Status:** Draft (design) — approved in brainstorm 2026-06-22, pending spec review.
**Extends:** `docs/specs/2026-06-13-iap-access-control-design.md` (IAP mechanism) and
`docs/specs/2026-06-14-iap-roles-admin-console-design.md` (the `user_roles` authorization
layer, default-deny gate, admin console, and access-request flow). This spec changes **one
mechanism decision** from the 06-13/06-14 work — the *shape of the IAP edge binding* — and
the UI/docs copy that depended on it. It does **not** change the seam, the JWT verification,
the roles model, or the admin console.
**Decision record:** ADR 0103 (to be written this session).

## Why this spec exists

The whole user-management flow the operator wants — self-service sign-up, request access,
admin approve, add-by-email, roles, revoke — **is already built and merged** (06-14 spec /
ADR 0085). The reason it doesn't work today is a single operational fact: the **IAP edge is
in per-user allowlist mode**. A new person is blocked by Google *at the edge* before they
can ever reach the app's denial page, so the entire app-side request/approve flow is
unreachable. To add a user today you must grant them
`roles/iap.httpsResourceAccessor` in the Google console first — the exact friction the
operator wants gone. The app's own UI even says so ("an admin must also add them to the
Google Group to let them in").

The operator's ask, verbatim: *"whole user management needs to be done from the admin
panel"*, and the governing constraint, verbatim: *"the app needs to stay as secure as
possible."*

This spec resolves that by **widening the IAP edge to all authenticated Google users** and
letting the app's existing default-deny gate + roles be the **sole authorization
authority**. After this, every per-user action happens in the admin panel; the Google
console is never touched to add or remove a user.

## The security model (why this stays secure)

The division of labour is the textbook authentication/authorization split:

- **IAP = authentication.** Google still fronts all ingress, forces a real Google login, and
  injects a *cryptographically signed* assertion that the app verifies (`adapters/iap.py`).
  We never trust a plaintext email header; we never own login/session/refresh code. The
  edge still **cannot fail open** — a request with no valid Google login never reaches our
  process.
- **The app = authorization.** The default-deny `_auth_gate` middleware (`main.py`) decides
  who is *actually* allowed in, and as what role, from the `user_roles` table. Reaching the
  edge ≠ being admitted.

Widening the edge changes **only** *who can knock*, not *who gets in*. The critical
distinction: the edge is bound to **`allAuthenticatedUsers`**, which is **not** `allUsers`.
`allAuthenticatedUsers` requires a real, logged-in Google account; the anonymous internet
is still bounced at the edge with no token. The app's authorization layer (default-deny,
fail-closed, already hardened by issue #73 and the 2026-06-17 outage regression guard)
remains the real gate.

### Why not the alternatives

- **Disable IAP / app-level OAuth (rejected).** Throws away IAP's can't-fail-open edge,
  forces us to own all login security code, and worsens the ADR 0043 browser-key exposure.
  Directly contradicts "as secure as possible."
- **App-managed narrow edge — a Google Group the app edits via Cloud Identity (rejected).**
  Keeps a narrow edge but requires the app to hold a Google credential that can widen its
  own front door — a new privilege-escalation surface — plus Cloud Identity setup and
  propagation-delay handling. This also reverses the 06-14 spec's deliberate non-goal ("the
  app never modifies the Google Group … that surface is deliberately not taken on"). We keep
  that non-goal intact.

The accepted trade-off of the chosen option: the `/access` denial + request page becomes
reachable by any Google account (someone could submit an access request — a single pending
DB row an admin must approve). See "Out of scope / deferred."

## Decisions locked (from the 2026-06-22 brainstorm)

1. **Keep IAP. Widen the edge.** Grant `roles/iap.httpsResourceAccessor` to
   **`allAuthenticatedUsers`** on the `catdv-annotator` Cloud Run service, replacing the
   per-user grants (which become redundant and should be removed for cleanliness).
2. **Hard invariant: `allAuthenticatedUsers`, never `allUsers`.** `allUsers` would expose
   the app to the anonymous internet. This invariant is documented loudly in `DEPLOY.md` and
   in the `cloudrun.env.yaml` comments; the deploy runbook calls it out as a check.
3. **Self-service request stays on (flavour A1).** The denial page keeps its "Request
   access" button; reached-but-unroled users self-request, admins approve in the console.
   No new public *write* surface is added beyond the one that already exists; rate-limiting
   is deferred (see below).
4. **The app remains the sole authorization authority and never edits the Google edge.**
   No Cloud Identity / Admin SDK credential is introduced. The 06-14 non-goal stands.
5. **Org-policy pre-check is a gate on this work.** If the GCP org policy
   (`iam.allowedPolicyMemberDomains` / domain-restricted sharing) forbids granting to
   `allAuthenticatedUsers`, this option is not deployable as-is and we revisit before
   spending implementation effort. This check happens first, on staging.

## What changes

### 1. Ops — the enabling change (one-time, not code)

On the Cloud Run service (staging first, then prod):

- Add an IAM binding granting `roles/iap.httpsResourceAccessor` to `allAuthenticatedUsers`.
- Remove the now-redundant per-user `iap.httpsResourceAccessor` grants.
- Leave the IAP→Cloud Run `run.invoker` grant for the IAP service agent **unchanged**.

No application code is required for the edge to widen; the app already enforces
authorization for every reached request.

### 2. Code — copy fixes (small, but user-facing-important)

`backend/app/templates/pages/_admin_access.html`:

- Remove the two "an admin must also add them to the Google Group" statements (the second
  info card body and the add-member form's `field-help`). Replace with copy that states the
  new reality: *adding a member here is all that's needed; people can also sign in and
  request access, which you approve here. No Google console step.*
- The IAP info card ("Google verifies every visitor's identity at the edge") stays — still
  accurate.
- Optional: the "Send invite" button label is fine to keep; no behavioural change.

`backend/app/templates/pages/access.html`: the denial *copy* ("your account isn't on the
access list yet…") is already correct (no change), **but** the "Use a different account"
link must become absolute — see §2b.

Beyond §2b, no changes to `main.py`, `identity.py`, `adapters/iap.py`, `user_roles.py`, or
`admin_access.py`. The core flow already works once the edge is widened.

### 2b. Fix — "Use a different account" → 405 after Request access (found during cutover)

Surfaced the moment the request flow became reachable on staging. **Repro:** on the "No
access" page, click **Request access**, then click **Use a different account** → *405 Method
Not Allowed*.

**Root cause:** `POST /access/request` re-renders the page *in place*, so the browser URL is
now `/access/request` — a POST-only route. The denial page's "Use a different account" link
is a path-relative, query-only href (`?gcp-iap-mode=CLEAR_LOGIN_COOKIE`), which resolves
against the *current* path → `GET /access/request?gcp-iap-mode=…` → 405. (On the normal
`/access` GET it resolves to `GET /access?…`, which is why it only breaks after a request.)

**Fix — POST-Redirect-GET, plus an absolute logout link:**

1. `POST /access/request` (`routes/pages/access.py`): after `record_request`, return a
   **303 redirect to `GET /access?state=requested`** instead of rendering inline. This (a)
   parks the browser on a GET route so relative links resolve correctly, (b) makes a refresh
   safe (no form re-POST / 405), and (c) keeps the no-JS robustness the page is designed for
   (a 303 needs no script). `GET /access` derives the email from
   `request.state.current_user` when no `email` query param is present, so identity still
   shows after the redirect.
2. `access.html`: change the "Use a different account" href to the **absolute**
   `/?gcp-iap-mode=CLEAR_LOGIN_COOKIE` (matching the topbar logout, ADR 0093 era), so it can
   never depend on the current path. Belt-and-suspenders against the same class of bug.

### 3. Config & docs

- `deploy/cloudrun.env.yaml`: fix the `ADMIN_EMAILS` comment that claims each entry "must
  ALSO be IAP-allow-listed (roles/iap.httpsResourceAccessor) or it never reaches the app."
  Under `allAuthenticatedUsers`, admin emails only need to be valid Google accounts.
- `docs/DEPLOY.md`: add the `allAuthenticatedUsers` binding step, the **never-`allUsers`**
  invariant, the org-policy pre-check, and how to revert to per-user allowlist if ever
  needed.
- `docs/adr/0103-*.md` + `docs/decisions.md`: record the decision and rationale (IAP =
  authN, app = sole authZ; rejected disable-IAP and app-managed-group; the accepted,
  deferred request-page exposure).

## Out of scope / deferred (operator's call)

- **Rate-limiting `POST /access/request`.** The only new exposure is that any Google account
  can submit an access request. `UserRolesRepo.record_request` already **dedupes** repeat
  requests into a single pending row (no-op if any row exists), so it cannot pile up per
  person. A determined single account could still churn writes; if abuse ever appears, add
  per-identity + global rate-limiting. Tracked as a follow-up, not built now.
- **ADR 0043 — raw Gemini API key shipped to the browser.** The real remaining multi-user
  security gap, but a separate backend-proxy project, orthogonal to access control. Named
  here so its deferral is conscious, not a surprise.

## Testing

- **Integration (extends `tests/integration/test_auth_gate.py`):** under `AUTH_BACKEND=iap`,
  a reached-but-unroled email gets the denial page (403, `access.html`), and `POST
  /access/request` now returns a **303 → `Location: /access?state=requested`** (was 200
  inline) while recording exactly one `requested` row; a second POST from the same identity
  is a no-op (still one row). Following the redirect renders the "Request sent" state with
  the user's email. Assert an `invited` user is admitted and auto-activated on first sight
  (existing guard — keep green).
- **405 regression guard (§2b):** a template/route test asserting the denial page's "Use a
  different account" link is absolute (`/?gcp-iap-mode=CLEAR_LOGIN_COOKIE`), and that `POST
  /access/request` redirects rather than rendering at the POST-only URL — so the
  "request-then-switch-account → 405" path can't return.
- **Guard:** a unit/template test asserting `_admin_access.html` no longer contains the
  "Google Group" instruction string (so the misleading copy can't silently return).
- No new tests for the edge binding itself — it's GCP IAM, verified manually on staging.

## Manual acceptance flows

1. **Org-policy / edge pre-check (staging).** On `catdv-annotator-staging`, grant
   `roles/iap.httpsResourceAccessor` to `allAuthenticatedUsers`. *Expected:* the binding is
   accepted by GCP (no org-policy rejection). If rejected, stop — Option A is not deployable
   and we revisit.
2. **Self-service request → approve (staging, end-to-end).** From a Google account that is
   **not** in `ADMIN_EMAILS` and has **no** `user_roles` row, open the staging URL. *Expected:*
   you reach the app's denial page (not Google's own "you don't have access" page), showing
   your signed-in email and a "Request access" button. Click it. *Expected:* the page shows
   "Request sent. An admin will review it.", **the URL bar reads `/access` (not
   `/access/request`)**, and clicking **Use a different account** opens Google's account
   chooser — *not* a 405 (the §2b regression). Then, as an admin, open `/admin` →
   Access & Permissions. *Expected:* the pending count incremented and the requester appears
   with an **Accept** button. Click Accept. *Expected:* the row becomes an active member; a
   success toast; no `location.reload`. Reload the app as the requester. *Expected:* full
   app access.
3. **Add-by-email (invite) → first sign-in (staging).** As an admin, click **+ Add member**,
   enter a Google email with no prior row, pick a role, submit. *Expected:* the member
   appears with status `invited`; the form copy makes **no** mention of a Google
   console/Group step. Sign in as that user. *Expected:* immediate access (auto-activated to
   `active`), with no console action taken anywhere.
4. **Revoke (staging).** As an admin, revoke a non-admin member. *Expected:* their row is
   removed; on their next request they get the denial page again. The last-admin guard still
   blocks revoking/demoting the final admin (regression check).
5. **Anonymous is still blocked (security check).** Hit the staging URL in a browser with no
   Google session (or incognito, no login). *Expected:* Google's IAP login challenge — never
   the app — confirming `allAuthenticatedUsers` ≠ `allUsers`.
6. **Prod cutover.** Repeat step 1 on the prod `catdv-annotator` service, remove the
   per-user grants, and smoke-test steps 2–3 with one real teammate account. *Expected:* a
   teammate is onboarded entirely from the admin panel, zero console steps.
