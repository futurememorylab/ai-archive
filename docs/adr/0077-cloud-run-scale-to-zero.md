# 0077. Cloud Run scale-to-zero with graceful seat + VPN release

**Date:** 2026-06-12
**Status:** Accepted
**Lifespan:** Invariant

## Context

The Cloud Run service ran pinned at `--min-instances=1`. To stop billing an
idle running instance we want scale-to-zero (`--min-instances=0`). On
scale-down Cloud Run sends `SIGTERM` and SIGKILLs after a **fixed 10s** grace.
The instance must, in that window, release the CatDV license seat and
disconnect the WireGuard (onetun) tunnel, and Litestream (PID 1) must flush
its final WAL to GCS — otherwise the next cold start silently loses writes.

The ordered teardown already exists (ADR 0075): `LiveCtx.aclose()` stops
background services, then `catdv.__aexit__()` issues `DELETE /session` over
the still-live tunnel, then `vpn_supervisor.aclose()` kills onetun, then the
DB closes; Litestream final-syncs after uvicorn exits. At `min=1` this ran
only on deploys; at `min=0` it runs on every idle cycle, so the timeout
budget must comfortably fit 10s.

## Alternatives

- **Keep `min=1`.** Simplest, but pays for an always-running instance even
  when nobody uses the app — the cost we set out to remove.
- **Drop `--no-cpu-throttling` (request-based billing).** Larger savings, but
  CPU is frozen between requests, which breaks Litestream replication and the
  background loops, and weakens the SIGTERM teardown's CPU guarantee.
  Rejected.
- **Add a manual SQLite `wal_checkpoint` on shutdown.** Litestream owns WAL
  checkpointing; external checkpointing is a documented corruption footgun.
  Rejected.

## Decision

Set `--min-instances=0`, keep `--max-instances=1` and `--no-cpu-throttling`.
Hide the in-app Shut-down button and reject `POST /api/connection/shutdown`
with 403 when `app_env == "prod"` (Cloud Run owns the lifecycle). Trim the
teardown budget so the final WAL sync has headroom: onetun kill 5s→2s,
CatDV logout 3s→2s (≈4s of `aclose()` work). The full sequence after
`SIGTERM` is uvicorn's `--timeout-graceful-shutdown 3` drain → `aclose()`
(~4s) → Litestream final sync. On the scale-to-zero path the drain is
~0s (no in-flight requests, since scale-down happens *because* traffic
stopped), leaving ≈6s for Litestream; in the worst case (a rolling deploy
draining live requests) the drain can take its full 3s, so 3 + 4 = 7s and
~3s remains — still comfortably inside the fixed 10s grace.

## Consequences

- Idle instances scale to zero and stop billing; the first request after idle
  pays a cold start (container boot + `litestream restore`). CatDV/VPN are
  connected on demand, so they do not add to cold start.
- `max-instances=1` still guarantees one seat / one writer / one queue.
- **Accepted risk — drain overlap:** a request arriving during a draining
  instance's 10s shutdown can make Cloud Run start a new instance while the
  old one's Litestream is still final-syncing — brief concurrent access to the
  same GCS replica. Same risk class as a rolling deploy (already accepted),
  sub-10s, and rare (scale-down happens because traffic stopped). Eliminating
  it would need draining semantics Cloud Run does not expose.
- Guards: `tests/unit/test_deploy_workflow_scaling.py` (min=0/max=1),
  `tests/unit/test_vpn_supervisor.py` + `tests/unit/test_catdv_logout_timeout.py`
  (timeout budget), `tests/integration/test_topbar_shutdown_visibility.py` +
  `tests/integration/test_routes_shutdown.py` (button hidden / route 403).
  The timeout-budget guards assert the two trimmed constants directly (a
  wall-clock teardown timing test would be flaky); the existing
  `tests/unit/test_aclose_ordering.py` already pins the seat→VPN→DB order.
  Litestream final-flush durability needs the real `litestream` binary + GCS,
  so it is not CI-automatable — it is verified by manual acceptance flow #2 in
  the spec (write a row → scale to zero → cold start → row survives).
