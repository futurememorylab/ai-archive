# Shutdown button — design

**Date:** 2026-05-25
**Status:** Approved (pending implementation)

## Problem

Releasing the CatDV license seat today requires dropping to a terminal and
sending `SIGTERM` to the uvicorn process (`kill -TERM <pid>`), which runs the
FastAPI lifespan shutdown → `ctx.aclose()` → `CatdvClient.logout()` →
`DELETE /catdv/api/9/session`. If the seat is *not* released, CatDV holds it
server-side until its idle timeout (long — effectively locking the next session
out). The app should let an operator release the seat and stop the server
**from the browser**, without a terminal, while being bulletproof about (a)
actually releasing the seat and (b) never losing local data.

## Scope decisions

### Shutdown only — no standalone "logout"

We deliberately do **not** add a "log out but keep the app running" button.

The app auto-re-authenticates: any CatDV request re-logs in on an `AUTH`
envelope (`catdv_client.py` `_call_json`), and `ConnectionMonitor` health-probes
every ~30s through that same path. So "release the seat but keep running" is
*unstable* — the next probe (within 30s) or any page action would silently
re-grab the seat. Making it stable would require a re-login suppression latch.

Logged-out-but-running is only useful for offline cache browsing, which is a
developer activity. Not worth the latch complexity. **Shutdown is the only
button.**

### Self-SIGTERM — not an owned-server entrypoint

The handler triggers shutdown by sending `SIGTERM` to its own process
(`os.kill(os.getpid(), signal.SIGTERM)`). This invokes uvicorn's *own*
documented shutdown trigger — the same path as `kill -TERM` and Ctrl-C — and
runs the existing, tested `aclose()` seat-release sequence with **zero changes
to the critical section**.

The considered alternative (own the `uvicorn.Server` object in a custom
entrypoint and set `server.should_exit = True`) is not more bulletproof —
uvicorn's signal handler *literally just sets `should_exit = True`*, so both
converge on the same uvicorn code and the same `aclose()` path. The owned-server
version costs a new launch module, a systemd `ExecStart` change (a deployment
change), and either losing `--reload` or maintaining two launch paths. Not
justified for a convenience button. See ADR 0024.

## Why this is safe

### No data loss

All mutable state is durable in SQLite. Pending CatDV writes live in the
`pending_operations` table. `SyncEngine.stop()` waits up to 2s for the current
tick, then cancels; a row mid-flight is already marked `in_flight` *before* the
network call, and startup crash-recovery resets `in_flight → pending`
(`context.py` `_build_core`). So an interrupted write simply retries on the next
boot — no loss. This is the same guarantee `kill -TERM` already provides; the
button adds no new risk.

### No seat re-grab after logout

The re-login mechanism is real but is neutralized by the ordering inside
`aclose()` (`context.py`):

```
1. media_prefetcher.stop()
2. lru_eviction.stop()
3. sync_engine.stop()
4. connection_monitor.stop()   ← the only periodic re-prober is now stopped
5. catdv.__aexit__() → logout() → DELETE /session   ← nothing alive calls login() anymore
6. db close
```

By step 5 the monitor is stopped, and uvicorn refuses new HTTP requests during
graceful shutdown (in-flight requests drain *before* `aclose()` starts). So
after the logout there is no live code path that can call `login()`. No
re-grab. Then the process exits.

### Production won't auto-restart

The systemd unit uses `Restart=on-failure`. A SIGTERM-driven graceful shutdown
exits cleanly (code 0), which systemd treats as success — it will **not**
respawn. To bring it back, an admin runs `systemctl start`.

## Components

### Endpoint — `POST /api/connection/shutdown`

Lives in `backend/app/routes/connection.py` (reuses that module's Jinja
templates and connection-surface conventions).

Behavior:
- **Normal mode:** return the "shutting down" screen HTML, then schedule the
  signal *after the response flushes* via
  `loop.call_later(0.5, request_graceful_shutdown)` so the browser receives the
  screen before the process dies.
- **Reload mode** (`dev_reload` true — see below): refuse. Do **not** fire the
  signal. Return the button rendered in its disabled state (HTMX swap) so the UI
  reflects that shutdown isn't available.

### Shutdown trigger seam — `backend/app/shutdown.py`

A module-level `request_graceful_shutdown()` that does
`os.kill(os.getpid(), signal.SIGTERM)`. Isolated in its own module so tests
monkeypatch `backend.app.shutdown.request_graceful_shutdown` and assert it fired
**without killing the pytest process**. This is the non-negotiable test seam.

### Reload detection — `Settings.dev_reload`

New boolean Settings field mapped to the `DEV_RELOAD` env var that `run.sh`
already sets when launching with `--reload`. Under reload, uvicorn runs the app
as a child of a reloader supervisor; self-SIGTERM kills the worker but the
supervisor's behavior is murky and could respawn (re-grabbing the seat). Since
anyone using `--reload` has a terminal (Ctrl-C), the button is disabled there
rather than giving a false guarantee.

### Button — `_topbar_pills.html`

A small power-icon button added to the topbar pillset (new
`templates/icons/_power.svg`), styled distinct (danger-on-hover).

- `hx-confirm="Shut down the annotator and release the CatDV seat?"`
- `hx-post="/api/connection/shutdown"`
- `hx-target="body" hx-swap="innerHTML"` — the page becomes the shutting-down
  screen on success.
- Under reload mode: rendered disabled with tooltip "Reload mode — stop with
  Ctrl-C in the terminal."

### Shutting-down screen

HTML returned by the endpoint: "Shutting down — releasing CatDV seat…" with a
spinner and an inline poller:

- `fetch('/api/health')` every 500ms. While it responds OK → keep waiting.
- When the fetch **throws** (connection refused) → the process is gone →
  `aclose()` ran to completion (logout was its last step) → swap to "Stopped.
  Seat released — you can close this tab." and attempt `window.close()`.
- `window.close()` only works for script-opened tabs/PWAs; otherwise the message
  stands as the reliable fallback.
- Safety: after ~15s still responding → show "Taking longer than expected…" but
  keep polling (graceful shutdown waits for in-flight requests to drain).

The screen verifies seat release the same way the operator does by hand today:
process gone ⇒ `aclose()` completed ⇒ logout was attempted last with nothing
able to re-auth after.

### Bulletproofing add — surface logout failure

Today `CatdvClient.__aexit__` swallows a failing `logout()` silently
(`except Exception: pass`). Change `logout()` (or its caller) to log a WARNING
on a failed `DELETE /session`, so a leaked seat is at least diagnosable in the
journal instead of invisible. `_logged_in` is still cleared regardless.

## Data flow

```
[Shut down button] --hx-confirm--> POST /api/connection/shutdown
   |
   |-- reload mode? --> return disabled button, no signal
   |
   '-- normal: return shutting-down screen HTML
                 + loop.call_later(0.5, request_graceful_shutdown)
                          |
                          v
                   os.kill(getpid, SIGTERM)
                          |
                          v
              uvicorn graceful shutdown
                          |
                          v
                   lifespan finally -> ctx.aclose()
                   (stop prefetcher/lru/sync/monitor, logout, close db)
                          |
                          v
                   process exits (code 0)

[Browser] shutting-down screen polls GET /api/health
   |-- responds --> keep waiting
   '-- throws (refused) --> "Stopped. Seat released." + window.close()
```

## Error handling

- **Logout fails during shutdown:** logged as WARNING; process still exits.
  Operator can see the warning in the journal and free the seat via the CatDV
  admin UI if needed.
- **Server slow to stop (in-flight drain):** screen keeps polling, shows
  "taking longer than expected" after 15s.
- **Reload mode:** endpoint refuses; button disabled.
- **Already offline / forced-offline:** shutdown still valid; `logout()` is a
  no-op when not logged in. Button stays enabled.

## Testing

- `POST /api/connection/shutdown` returns the shutting-down screen and the
  monkeypatched `request_graceful_shutdown` fired exactly once — must never
  signal the test runner.
- Reload-mode gating: with `dev_reload` set, the trigger is **not** called and
  the disabled response is returned.
- `logout()` failure path logs the WARNING and still clears `_logged_in`.
- Regression guard: `aclose()` keeps `logout()` as the last external step
  (after `connection_monitor.stop()`).
- The browser health-poll behavior is verified manually (not easily
  automatable); flagged here explicitly.

## ADR

Append ADR 0024 capturing the two design calls (shutdown-only over logout;
self-SIGTERM over owned-server entrypoint) and update `docs/decisions.md`.
