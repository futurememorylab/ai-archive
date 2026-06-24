# 0032. Bound uvicorn graceful shutdown so open streams can't leak the seat

- **Date:** 2026-05-26
- **Status:** Accepted
- **Lifespan:** Invariant

## Context

The shutdown button ([[ADR 0024]]) sends a self-`SIGTERM`; uvicorn's graceful
shutdown then runs the lifespan teardown (`AppContext.aclose()` → CatDV
`logout()`). In practice the button "sometimes didn't work": the browser screen
sometimes never reached "Stopped", and the terminal sat at
`Waiting for connections to close. (CTRL+C to force quit)`.

Root cause (reproduced): uvicorn runs lifespan shutdown **only after every HTTP
connection closes**, and with no graceful-shutdown timeout configured it waits
*indefinitely*. The app holds long-lived connections that never close on their
own and don't react to the exit signal:

- `/api/connection/events` and `/api/studio/runs/{id}/events` — raw Starlette
  `StreamingResponse` generators blocked on `await queue.get()`.
- the Gemini live `WebSocket`.

When any of those is open at shutdown, uvicorn blocks forever → `aclose()` never
runs → the seat is never released (the exact failure the button exists to
prevent). A minimal repro confirmed it: with `/api/connection/events` open the
process hung; with no stream open, or with the sse_starlette-backed
`/api/jobs/{id}/events` (which *does* honour the exit signal), it exited in
~0.2s.

## Alternatives

- **Make every stream react to shutdown** (poll `is_disconnected()` / a
  shutdown event, bound `queue.get()`). Rejected as the primary fix: touches
  several endpoints plus the WebSocket, is easy to get wrong, and a future
  stream could silently reintroduce the hang. It treats N symptoms, not the
  cause.
- **Migrate the raw SSE endpoints to sse_starlette `EventSourceResponse`**
  (which handles graceful exit). Helps SSE but not the WebSocket, and is more
  churn than the problem warrants.
- **Owned-server entrypoint** to set `timeout_graceful_shutdown` in code.
  Rejected for the same reasons as in [[ADR 0024]] — a new launch module and
  systemd/reload complexity.

## Decision

Pass `--timeout-graceful-shutdown 3` to uvicorn at both launch points
(`run.sh` and `deploy/catdv-annotator.service`). After 3s of waiting for
connections to drain, uvicorn force-closes the stragglers and proceeds to the
lifespan teardown, so `aclose()` (seat release) always runs and shutdown is
bounded. This is a single connection-agnostic backstop covering every current
and future stream/WebSocket, with zero changes to the `aclose()` critical
section.

3s lets ordinary in-flight HTTP requests finish while force-closing event
streams promptly; total shutdown stays well under the screen's 15s
"taking longer than expected" threshold. Force-closing an event stream during
an explicit operator shutdown is harmless — the browser's `EventSource`/health
poll simply sees the connection drop, which the shutdown screen already treats
as success.

## Consequences

- The shutdown button now reliably stops the server and releases the seat even
  when a clip-annotation, studio-run, or live session is open.
- A subprocess regression test (`test_shutdown_graceful_timeout.py`) spawns a
  seat-safe server, holds an SSE connection open, SIGTERMs it, and asserts a
  bounded exit with `Application shutdown complete`; two cheaper tests assert
  both launch files keep the flag.
- The fix lives only in launch config, so any other launch path (ad-hoc
  `uvicorn …`) must pass the flag itself to get the guarantee.
