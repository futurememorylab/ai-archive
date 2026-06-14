# 0078. Access control via Google Cloud IAP + an app-side roles layer (not app-level OAuth)

**Date:** 2026-06-13
**Status:** Proposed
**Spec:** `docs/specs/2026-06-13-iap-access-control-design.md`

> **Numbering note:** a parallel design chat may also be adding an ADR. If
> two land on 0078, renumber the later one (the worktree/parallel-branch
> collision hazard noted in earlier ADR work).

## Context

The Cloud Run deployment is private and reached only via
`gcloud run services proxy` + `roles/run.invoker` on one operator account
(`2026-06-09-cloud-run-deployment-design.md` explicitly deferred multi-user
auth to "phase 5"). We now want a **known team** to sign in through a browser,
with an admin page and per-user permissions.

Three constraints shape the choice:

- **Reliability + security are the stated priorities** ("dead reliable, no
  exceptions, 100% secure"). The realistic bar is minimal attack surface,
  defense in depth, and **fail-closed** — and the biggest lever on that is
  *how much security-critical code we own*.
- **The deployment scales to zero** (ADR 0077) and is pinned to one instance
  (ADR 0066). Any auth design must survive cold starts and must not assume a
  second instance or a shared session store.
- **Doors must stay open** for futures that are not yet decided (multiple
  archives, multiple Gemini keys, teams, and crucially **cloud *or* local**
  deployment).

## Alternatives

- **A — Google Cloud IAP (direct on Cloud Run) + app-side roles (chosen).**
  Google operates the login and the coarse allow-list (a Group/domain); IAP is
  enabled **directly on the Cloud Run service** (`--iap`, GA 2026) — **no load
  balancer, no serverless NEG, no added cost**, and Google's recommended
  approach. IAP injects a signed identity the app verifies. Authorization
  (roles/permissions, admin page, attribution) is a small app-side layer.
  *Cost:* Google-accounts-only; IAP does not exist when running locally.
  (An earlier draft of this ADR assumed an external HTTPS LB + serverless NEG
  at ~$18–25/mo; the direct-IAP GA removes that — see the spec.)
- **B — App-level OAuth (Authlib + signed-cookie sessions).** The app runs the
  OAuth flow and gates itself; Cloud Run flips to `--allow-unauthenticated`.
  *Portable (cloud or local), provider-flexible*, but the app's own code
  becomes the only guard (can fail **open** on a bug), the app is publicly
  exposed, and we own more security-critical code. Stateless cookie sessions
  are mandatory because scale-to-zero wipes any server-side session store.
- **C — Keep IAM + `gcloud` proxy.** No browser sign-in; not a team
  experience. Rejected against the goal.

## Decision

Adopt **A**: IAP for authentication and the coarse gate, an **app-side
`user_roles` layer** for authorization and an admin page, and **per-user
attribution** stamped app-side (CatDV writes are shared-seat `klientAI`).

Make the mechanism swappable behind **one seam** so A is not a one-way door:

- A `backend/app/auth/` package exposes a single `get_current_user()`
  dependency returning `CurrentUser(email, role, …)`. **Only**
  `backend/app/auth/adapters/*` may reference IAP/OAuth specifics; everything
  else depends on the seam. The active backend is selected by `AUTH_BACKEND`
  (`iap` in cloud, `dev` locally, future `oauth`).
- This boundary is **enforced by a guard test + an `.importlinter` contract**,
  in the same spirit as `test_context_delegation.py` and the existing layer
  contracts — turning "doors stay open" from a promise into a CI gate.

Hard requirements (each backed by a guard or an acceptance flow in the spec):

1. Cloud Run's own startup/liveness probes are internal and unaffected by IAP;
   `/api/health` stays unauthenticated at the app layer, and the *external* CI
   verify step is reworked to authenticate to IAP (see spec).
2. Direct IAP (`--iap`) fronts **all** ingress paths incl. the auto-assigned
   URL; the service stays `--no-allow-unauthenticated` with `run.invoker`
   granted only to the IAP service agent — so there is no public bypass.
3. The signed `X-Goog-IAP-JWT-Assertion` is **verified** (signature +
   audience); the plaintext email header is never trusted alone.
4. **Fail closed** and **default-deny** everywhere in auth/role resolution.

Explicitly **out of scope / not promised**: horizontal scale or per-user
backend isolation (blocked by the single-instance / one-seat / one-writer
design — ADR 0066; needs the Postgres migration first), and per-user CatDV
logins (2-seat license).

**Blocking prerequisite:** ADR 0043 (the raw Gemini-Live key shipped to the
browser) assumes a single-operator-on-VPN threat model that multi-user
violates. It must be fixed (backend WSS proxy) — or the Live assistant gated
to the operator role — before cutover. Tracked separately.

## Consequences

- **Reliability:** the gate is Google-operated and lives in front of the
  instance, so it cannot fail open on an app bug or mid-cold-start, and it
  survives scale-to-zero (the session is Google's, the roles table is
  Litestream-restored).
- **Security:** we own minimal auth code; the attack surface is the (small,
  enforced) adapter plus the role checks. The four hard requirements close the
  common IAP foot-guns (open health route, ingress bypass, header spoofing,
  fail-open).
- **Cost / lock-in:** direct IAP adds **no load-balancer cost** (GA 2026) — a
  single `--iap` flag. IAP remains Google-only and cloud-only. The seam bounds
  the lock-in: switching to app-OAuth or running locally is a new adapter
  behind the same interface, not a rewrite — but it *is* a non-zero, bounded
  effort, not free.
- **New surface to maintain:** a `user_roles` table + migration, an admin
  page, the auth package, and the IAP enablement in `deploy/` (the `--iap`
  flag + IAM grants — no LB/NEG infra). The CI *Verify* step must change, since
  `--iap` fronts the auto-assigned URL it currently curls.
- **Attribution becomes possible** at the app layer even though CatDV cannot
  distinguish users.
