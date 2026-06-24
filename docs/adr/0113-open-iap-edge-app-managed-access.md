# 0113. Open the IAP edge to `allAuthenticatedUsers`; the app is the sole authorization authority

> Renumbered 0109 → 0113: `origin/main` independently took 0109–0112 (Gemini Live
> handshake/token work) between this branch's creation and its PR.

**Date:** 2026-06-22
**Status:** Accepted
**Lifespan:** Invariant

## Context

Access control shipped in two stacked gates (ADR 0084 IAP mechanism, ADR 0085
roles + admin console): **Google IAP** authenticates at the edge, and the app's
**default-deny gate** (`main.py`) + `user_roles` table authorize. The whole
app-side onboarding flow already exists and is merged — the `/access` denial page
with a "Request access" button, the admin console's add-member / accept-request /
change-role / revoke, and the invited→active flip on first sign-in (ADR 0102).

But in prod the IAP edge was in **per-user allowlist mode**: each user needed an
individual `roles/iap.httpsResourceAccessor` grant in the Google console before
they could reach the app at all. That made the entire app-side flow **unreachable
for new users** — a new person was blocked by Google *at the edge* before ever
seeing the denial page. The app even said so in its own UI ("an admin must also
add them to the Google Group to let them in"). The operator's ask: *"whole user
management needs to be done from the admin panel"*, under the constraint *"the app
needs to stay as secure as possible."*

The friction is structural: with no shared Google Workspace domain (the team is on
personal Gmail accounts), a *narrow* edge that only known people can reach must be
maintained somewhere — either by a human in the console (today's pain) or by the
app holding a Google credential that can mutate the edge.

## Alternatives

- **Disable IAP; app-level OAuth (Authlib + sessions, `--allow-unauthenticated`).**
  Rejected — throws away IAP's can't-fail-open edge, forces us to own all
  login/session/verification code (the highest-stakes code to get wrong), and
  worsens the ADR 0043 browser-key exposure. Directly contradicts "as secure as
  possible."
- **Keep a narrow edge, app-managed** (a dedicated Google Group the app edits via
  Cloud Identity, or app-automated per-user IAM bindings). Rejected — keeps the
  edge narrow but requires the app to hold a Google credential able to **widen its
  own front door**, a new privilege-escalation surface, plus Cloud Identity setup
  and IAM-propagation handling. It also reverses ADR 0085's deliberate non-goal
  ("the app never modifies the Google Group … that surface is deliberately not
  taken on").
- **Keep the per-user allowlist.** Rejected — it *is* the friction being removed.
- **Tighten to admin-invite-only** (drop the self-service "Request access" button,
  so there is no public write surface at all). Considered and deferred: the
  operator chose to keep self-service ("this feature is way too important"); the
  only new exposure is request-row spam, which `record_request` already dedupes
  to one row per identity. Rate-limiting is a future follow-up if abuse appears.

## Decision

Widen the **edge**, keep the **app** as the sole *authorization* authority:

- **Edge binding:** grant `roles/iap.httpsResourceAccessor` to
  **`allAuthenticatedUsers`** on the Cloud Run service (replacing the per-user
  grants). Any signed-in Google account can *reach* the app; the app's default-deny
  gate decides who is *admitted*. **Never `allUsers`** — that is the anonymous
  internet; `allAuthenticatedUsers` still requires a real Google login. This is an
  IAM/ops change, not application code.
- **Division of labour unchanged in code:** IAP = authentication (verified JWT,
  fail-closed, `auth/adapters/iap.py`); the app = authorization (default-deny gate
  + `user_roles`). Widening the edge changes *who can knock*, not *who gets in*.
- **The app still never edits the Google edge** — no Cloud Identity / Admin SDK
  credential is introduced. ADR 0085's non-goal stands.
- **Self-service request kept** (the denial page's "Request access" → pending row →
  admin Accept), alongside admin add-by-email.
- **Denial-page 405 fix (found during cutover).** Once the flow became reachable, a
  real bug surfaced: after **Request access**, `POST /access/request` re-rendered
  the page in place, leaving the browser on the POST-only `/access/request` URL;
  the page's path-relative "Use a different account" link then resolved to
  `GET /access/request?…` → **405**. Fixed with **POST-Redirect-GET** (the POST
  records the request, then 303-redirects to `GET /access?state=requested`; `GET
  /access` falls back to the gate-attached identity for the email) and by making
  the logout link **absolute** (`/?gcp-iap-mode=CLEAR_LOGIN_COOKIE`, matching the
  topbar) so it can never depend on the current path.
- **Stale copy removed:** the admin console no longer instructs admins to "also add
  them to the Google Group"; `deploy/README.md` + `deploy/cloudrun.env.yaml`
  document the `allAuthenticatedUsers` binding, the never-`allUsers` invariant, the
  org-policy caveat, and the revert path.

## Consequences

- **Per-user management is fully in `/admin` → Access & Permissions** — add /
  approve / change-role / revoke, with zero Google-console steps. Deploy-time
  owners still come from `ADMIN_EMAILS` (seeded idempotently at boot).
- **New exposure:** the `/access` denial + request page is reachable by any Google
  account (not the anonymous internet). The only new write is a deduped pending
  request row; rate-limiting is deferred.
- **Org-policy dependency:** the model requires the GCP org policy to permit
  `allAuthenticatedUsers`; verified accepted on the prod service during cutover.
- **ADR 0043 (raw Gemini key to the browser):** was the *other* multi-user
  security gap; **since closed by ADR 0112** (#92, server-side ephemeral tokens),
  which this work rebases on top of. No longer a blocker; out of scope here either
  way.
- **Tests:** `test_request_access_redirects_to_access_get` (303 → `/access?state=
  requested`), the absolute-logout-link assertion in `test_access_denied_renders`,
  and `test_access_console_has_no_google_group_step` guard the 405 fix and the copy
  change. Existing `test_access_request` / `test_auth_gate` stay green (the PRG is
  redirect-transparent to clients that follow redirects).
- Supersedes the per-user-allowlist assumption in ADR 0084/0085 and the
  2026-06-13/06-14 specs; those remain the historical record of the mechanism.
