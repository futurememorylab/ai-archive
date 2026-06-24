# 0024. Browser-triggered graceful shutdown (shutdown button)

- **Date:** 2026-05-25
- **Status:** Accepted
- **Lifespan:** Invariant

## Context

Releasing the CatDV license seat required a terminal: `kill -TERM <pid>`
runs the FastAPI lifespan teardown → `AppContext.aclose()` →
`CatdvClient.logout()` → `DELETE /session`. A leaked seat is held
server-side until idle timeout, locking out the next session (the install
has a 2-seat limit, one usually taken by the human web client). We want to
release the seat and stop the server from the browser.

## Alternatives

- **Standalone "logout" that keeps the app running.** Rejected: the app
  auto-re-authenticates (`_call_json` re-logs in on AUTH; `ConnectionMonitor`
  health-probes every ~30s through the same path), so the seat would be
  re-grabbed within 30s unless we added a re-login suppression latch.
  Logged-out-but-running only helps offline cache browsing — a developer
  activity — so the latch isn't worth it. See [[ADR 0015]], [[ADR 0023]].
- **Owned-server entrypoint** (`uvicorn.Server` in a custom module, set
  `server.should_exit = True`). Rejected: not more bulletproof — uvicorn's
  signal handler literally just sets `should_exit`, so both converge on the
  same `aclose()` path. Costs a new launch module, a systemd `ExecStart`
  change, and losing `--reload` (or maintaining two launch paths).

## Decision

Add `POST /api/connection/shutdown`. It schedules a self-`SIGTERM`
(`os.kill(getpid(), SIGTERM)`) ~0.5s after the response flushes, via an
isolated `backend/app/shutdown.py` seam (so tests can swap it without
signalling pytest). uvicorn's existing signal handler runs the graceful
shutdown. The browser gets a full-screen screen that polls `/api/health`
until the connection is refused, then reports the seat released and tries
`window.close()`.

Safety rests on the existing `aclose()` ordering: stop prefetcher → LRU →
sync engine → **connection monitor → logout → close DB**. The monitor (the
only periodic re-prober) is stopped before logout, and uvicorn refuses new
requests during shutdown, so nothing re-authenticates after the seat is
released. All writes live in the durable `pending_operations` queue, so an
interrupted sync simply retries on next boot — no data loss.

The button is disabled when `DEV_RELOAD` is set: under `uvicorn --reload`
the reloader supervisor may respawn the worker (re-grabbing the seat), and
that developer has a terminal for Ctrl-C anyway. `logout()` now logs a
WARNING on a failed `DELETE /session` instead of swallowing it, so a leaked
seat is diagnosable.

## Consequences

- Operators release the seat from the UI; no terminal needed for the common
  case. Production (systemd `Restart=on-failure`) treats the clean exit as
  success and does not respawn — restart is a manual `systemctl start`.
- `window.close()` only works for script-opened tabs/PWAs; otherwise the
  "you can close this tab" message stands.
- The button is intentionally unavailable under `--reload`.
