# Cloud Run scale-to-zero with graceful seat + VPN release

**Date:** 2026-06-12
**Status:** Implemented (see ADR 0077). Durability + wall-clock budget are
verified via the manual acceptance flows rather than CI tests — the timeout
budget is guarded by asserting the trimmed constants (a timing test would be
flaky) and the Litestream round-trip needs the real binary + GCS.
**Scope:** Cloud Run deployment only (`APP_ENV=prod`). Local dev and the
`--reload` dev server are unaffected.

## Context

The Cloud Run service runs pinned at `--min-instances=1 --max-instances=1
--no-cpu-throttling`. We want to switch to **scale-to-zero**
(`--min-instances=0`) so the service stops billing a running instance when
nobody is using it. The instance must, on the way down, **release the CatDV
license seat first, then disconnect the WireGuard (onetun) tunnel** — in
that order — so neither resource leaks when the instance is gone.

### What already exists (do not rebuild)

The ordered teardown the title describes is **already implemented and
deliberately ordered** (ADR 0075):

- Cloud Run sends `SIGTERM` on scale-down. The container entrypoint is
  `exec litestream replicate -exec "$UVICORN"`, so **litestream is PID 1**
  and uvicorn is its child. litestream forwards the signal to uvicorn and,
  after uvicorn exits, performs a **final WAL sync to GCS** before exiting.
- uvicorn (`--timeout-graceful-shutdown 3`) runs FastAPI's `lifespan`
  `finally`, which calls `LiveCtx.aclose()` (`context.py:351-372`):
  stop background services → **`catdv.__aexit__()` → `DELETE
  /catdv/api/9/session`** (releases the seat, over the still-live tunnel) →
  **then `vpn_supervisor.aclose()`** terminates onetun. "Seat before
  tunnel" is load-bearing and commented as such.
- The UI **Shut down** button (`_topbar_pills.html`) triggers this same
  path via a self-`SIGTERM` (`shutdown.py`). It is already disabled under
  `--reload` (`dev_reload`).

So signal capture and ordered disconnect are **done**. This spec does not
add a new signal handler or reorder anything.

### Why scale-to-zero needs three narrow changes

1. **The shutdown button is meaningless and slightly harmful in cloud.**
   Cloud Run owns the lifecycle; a manual instance kill just forces a cold
   start on the next request. Hide the button; guard the route.
2. **The teardown must reliably finish inside Cloud Run's fixed 10-second
   `SIGTERM`→`SIGKILL` window.** At `min=1` the instance effectively never
   stops (only on deploy), so a too-slow teardown was rarely exercised. At
   `min=0` the instance stops on **every idle cycle**, so any teardown that
   overruns 10s — getting SIGKILL'd mid–final-sync — means **silently lost
   writes** on the next cold start. The current worst-case budget is too
   tight (below).
3. **The instance floor flag itself** must flip, while preserving the
   `max=1` correctness invariant.

### Current Cloud Run facts (verified June 2026)

- **Fixed 10s grace** between `SIGTERM` and `SIGKILL` (container contract).
  CPU **is** allocated and billed during this window.
- `--no-cpu-throttling` (CPU always allocated / instance-based billing) is
  **compatible with `min-instances=0`**: the instance still scales to zero
  when idle; while up, it bills per instance-second regardless of requests.
  This is required here so litestream replication and the background loops
  (sync engine, health probe, idle disconnector, LRU) keep running
  *between* requests, and so the SIGTERM teardown reliably gets CPU.
- litestream "attempts to synchronize all outstanding WAL changes to the
  replica before terminating" on graceful `SIGTERM` (litestream docs).
  **Do not** add a manual `wal_checkpoint` — litestream owns WAL
  checkpointing and external checkpointing is a documented corruption
  footgun.

## Goals

- Switch the service to `--min-instances=0`, keeping `--max-instances=1`
  and `--no-cpu-throttling`.
- Hide the **Shut down** button in the cloud deployment and reject the
  shutdown route there.
- Guarantee the existing seat-release → VPN-disconnect → litestream
  final-sync sequence completes inside the 10s grace, with headroom.
- Prove durability (no lost writes across a `SIGTERM` → cold-restart cycle)
  with an automated test.

## Non-goals

- No new signal handler, no reordering of the teardown (already correct).
- No change to the billing model (`--no-cpu-throttling` stays).
- No change to `--max-instances` (stays 1).
- No manual SQLite checkpointing.
- Eliminating the drain-overlap race (see Risks) — out of scope; accepted
  and documented.

## The cloud gate

Use the **existing** flag `settings.app_env` (`settings.py:15`), which is
`"prod"` in `deploy/cloudrun.env.yaml:5` and `"dev"` locally. `"prod"` *is*
"the Cloud Run deployment" in this project. Gate on `app_env == "prod"`,
mirroring how the template already branches on `dev_reload`. No new env var.

## Design

### Component 1 — Flip to scale-to-zero (`.github/workflows/deploy.yml`)

- `--min-instances=1` → **`--min-instances=0`**.
- **Keep** `--max-instances=1` (one CatDV seat, one litestream writer, one
  in-process write queue — unchanged invariant).
- **Keep** `--no-cpu-throttling`.
- Rewrite the load-bearing comment (currently lines 52-54): `min=0` is now
  intentional (cost savings while idle); `max=1` remains correctness and
  must never be raised; no traffic-split.

### Component 2 — Hide the button + guard the route in cloud

- **Hide:** in `backend/app/templates/pages/_topbar_pills.html`, add an
  `app_env == "prod"` branch that renders **nothing** for the Shut-down
  control (the existing `dev_reload` branch renders a disabled button;
  prod renders none). Dev keeps the working button.
- **Guard:** in `backend/app/routes/connection.py::shutdown`, return an
  HTTP error when `app_env == "prod"` — mirroring the existing `dev_reload`
  → `409` guard. Use **403** ("shutdown is managed by Cloud Run") so a
  crafted POST cannot kill the instance.

### Component 3 — Fit the teardown inside the 10s grace

Current worst-case sequential budget after `SIGTERM` (litestream PID 1 →
uvicorn child → `aclose()`):

| Step | Current cap | Source |
|---|---|---|
| uvicorn in-flight drain | 3s | `--timeout-graceful-shutdown 3` |
| CatDV logout `DELETE /session` | 3s | `catdv_client.logout` timeout |
| onetun terminate → SIGKILL | 5s | `vpn_supervisor` `kill_timeout_s=5.0` |
| litestream final WAL sync | needs headroom | litestream PID 1 |

Logout (3s) + onetun (5s) ≈ 8s **before uvicorn exits**, leaving <2s for
the final sync → risks SIGKILL mid-flush.

Changes:

- Lower **onetun `kill_timeout_s` 5s → 2s**. The onetun supervisor only
  runs when `vpn_managed` is true (cloud), so dev is untouched. onetun has
  no state to preserve and the seat-release DELETE already went out
  *before* VPN teardown — it only needs to die fast.
- Lower **CatDV logout DELETE timeout 3s → 2s**. This applies in all
  environments (logout runs in dev too), but is harmless: it is a tiny
  request and over a healthy LAN/tunnel 2s is ample; if the tunnel is
  already dead the seat is moot anyway.
- New budget ≈ 2 + 2 = ~4s for `aclose()`, leaving ~5s headroom for
  litestream's final sync. Comfortable inside 10s.

No manual SQLite checkpoint.

### Component 4 — Verification (tests)

- **Durability test** (`tests/integration/`): write a row → drive the
  `aclose()` teardown path (or a real `SIGTERM` to the litestream-wrapped
  process) → `litestream restore` into a fresh DB → assert the row
  survives.
- **Budget test:** assert the teardown sequence completes under a time
  bound (~well below 10s) with the slow network paths mocked, so a future
  regression that re-inflates a timeout fails CI.
- **Guard tests** (`tests/unit/` or `integration/`):
  - `POST /api/connection/shutdown` → **403** when `app_env == "prod"`;
    still **409** under `dev_reload`; still **200** in plain dev.
  - The Shut-down control is **absent** from the `_topbar_pills` render
    when `app_env == "prod"`, **present** in dev.

## Risks

### Two-instance overlap during drain (accepted, documented)

At `min=0/max=1`, a request arriving *during* a draining instance's 10s
shutdown can make Cloud Run start a new instance while the old one's
litestream is still doing its final sync — brief concurrent access to the
same GCS replica. This is the **same risk class as a rolling deploy**
(already accepted), the window is sub-10s, and it is rare (scale-down
happens precisely because traffic stopped). Eliminating it would require
draining semantics Cloud Run does not expose. Captured in a new ADR
alongside the `min=0` decision.

### Cold-start latency

At `min=0`, the first request after an idle period pays a cold start
(container boot + `litestream restore`). CatDV and the VPN are default-off
and connected on demand by the operator, so they do **not** add to cold
start. Acceptable for this low-concurrency, operator-driven app.

## Files touched (anticipated)

- `.github/workflows/deploy.yml` — `min-instances`, comment.
- `backend/app/templates/pages/_topbar_pills.html` — prod hide branch.
- `backend/app/routes/connection.py` — `app_env=="prod"` → 403 guard.
- `backend/app/services/vpn_supervisor.py` — `kill_timeout_s` default 2.0.
- `backend/app/services/catdv_client.py` — logout DELETE timeout 2.0.
- `tests/integration/` + `tests/unit/` — durability, budget, guard tests.
- `docs/adr/0077-cloud-run-scale-to-zero.md` + `docs/decisions.md` index.

## Manual acceptance flows

1. **Scale-to-zero releases the seat and tunnel.**
   Setup: deployed cloud revision; in the UI, click **Connect** (spends a
   CatDV seat) and **Connect VPN** (onetun up). Confirm the CatDV admin UI
   shows the seat taken and the connection chip is green.
   Action: stop sending traffic and wait past the Cloud Run idle timeout
   (or trigger a scale-down). Watch the Cloud Run logs.
   Expected: logs show `LiveCtx.aclose()` running — CatDV `DELETE /session`
   logged **before** onetun teardown — then litestream's final sync, then
   process exit, **all within ~10s of SIGTERM** (no SIGKILL line). The
   CatDV admin UI shows the seat **released**. Instance count goes to 0.

2. **No lost writes across a stop/start cycle.**
   Setup: deployed cloud revision, scaled to an instance.
   Action: make a change that writes to the DB (e.g. save an annotation /
   create a job). Immediately let the instance scale to zero. Then send a
   new request to force a cold start.
   Expected: after the cold start (which runs `litestream restore`), the
   change from before the scale-down is present. Nothing silently lost.

3. **Shut-down button hidden + route guarded in cloud.**
   Setup: deployed cloud revision (`APP_ENV=prod`).
   Action: open the app; inspect the top bar. Then
   `POST /api/connection/shutdown` directly (authenticated).
   Expected: **no** Shut-down button in the UI; the POST returns **403**;
   the instance keeps running (not killed).

4. **Dev server still has a working Shut-down button.**
   Setup: local dev server (`APP_ENV=dev`, not `--reload`).
   Action: click **Shut down**.
   Expected: button is present; clicking it releases the CatDV seat and
   stops the server exactly as today (shutdown screen → connection refused
   → "seat released"). The 403 guard does **not** fire in dev.

5. **Reload dev server still disables the button.**
   Setup: local dev server started via `run.sh` with `--reload`
   (`dev_reload=true`).
   Action: observe the Shut-down control.
   Expected: button is present but **disabled** with the "stop with Ctrl-C"
   tooltip (unchanged); `POST /api/connection/shutdown` returns **409**.
