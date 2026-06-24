# 0068. CatDV connection is manual on-demand on Cloud Run

**Date:** 2026-06-10
**Status:** Accepted
**Lifespan:** Invariant

## Context

CatDV Enterprise has a 2-seat session limit and, in practice, one seat is
almost always taken by the human web client — so the app must assume a
single free seat. The seat is held server-side, bound to the
`JSESSIONID`, for as long as the session stays logged in.

Once the app runs on Cloud Run (ADR 0066) the instance is always-on: it
is pinned to a single instance that never scales to zero so Litestream
and the tunnel stay up. Under the legacy login-at-startup model, that
always-on instance would log in at boot and hold the one free CatDV seat
24/7 — locking the human web client out for the entire lifetime of the
deployment, whether or not anyone is using the app. A seat held by a
forgotten process is exactly the failure the project's session
discipline exists to prevent, and on Cloud Run there is no operator at a
terminal to `kill -TERM` it.

So connectivity has to become a deliberate, reversible action: spend the
seat only while someone is actually working, and free it the moment they
stop.

## Alternatives

- **Keep auto-login at startup.** Simple, and correct for a laptop where
  the dev starts the server only when working. But on an always-on
  Cloud Run instance it holds the seat indefinitely. Kept as an opt-in
  (`CATDV_CONNECT_MODE=auto`) for local dev, not the default.
- **A separate tunnel pinger / health daemon to decide connectivity.**
  Rejected — it adds a second source of truth that can disagree with the
  client. One seat-free `GET /api/info` probe for reachability plus the
  `CatdvClient.logged_in` flag for seat truth is sufficient; the probe
  never logs in, so it can run continuously without spending a seat.
- **Build onetun from source for the tunnel.** Not applicable to this
  decision — onetun packaging is ADR 0067; the seat question is
  orthogonal to how the WireGuard binary is shipped.

## Decision

`CATDV_CONNECT_MODE` defaults to `manual`. In manual mode the
`CatdvClient` is built at boot but `login()` is deferred — the instance
starts in a new `disconnected` `ConnectionState`, holding no seat.

- `POST /api/connection/connect` runs `login()` (spending the seat) and
  re-probes; `POST /api/connection/disconnect` runs `logout()` (freeing
  the seat) and re-probes. The connection pill is the operator's seat
  control.
- An `IdleDisconnector` background task logs out automatically once
  `CatdvClient.last_activity` is older than `catdv_idle_logout_s`
  (default 900s), so a forgotten Connect cannot hold the seat forever.
  Only operator-driven CatDV calls stamp activity — the health probe and
  the 5s pill poll deliberately do not.
- Seat truth comes from `CatdvClient.logged_in`, **not** from the probe.
  `ProviderHealth.reachable` distinguishes "tunnel up but logged out"
  (reachable, → `disconnected`) from "tunnel down" (unreachable, →
  `offline`), so the indicator tracks the tunnel without conflating
  reachability with a held seat.
- `auto` mode preserves the legacy startup login for local dev;
  `CATDV_OFFLINE=true` still wins (no client built at all).

## Consequences

- The deployed Cloud Run service must run `CATDV_CONNECT_MODE=manual`
  together with `CATDV_OFFLINE=false`: the client exists and the tunnel
  is reachable, but no seat is held until the operator clicks Connect.
  The deploy config (`deploy/cloudrun.env.yaml`) sets this.
- The connection pill is now the seat control: Connect spends the seat,
  Disconnect frees it, and the idle timeout frees a forgotten one.
- Whether `GET /api/info` requires auth is irrelevant to seat accounting,
  because the seat truth is `logged_in`, not the probe result. The probe
  only answers "is the tunnel up?".
- Auto mode is preserved for local dev, so the laptop workflow (start
  server → already logged in) is unchanged when `CATDV_CONNECT_MODE=auto`.
