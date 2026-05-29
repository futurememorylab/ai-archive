# 0041. Bound the boot-time CatDV login with a short, separate timeout

**Date:** 2026-05-29
**Status:** Accepted

## Context

`_build_archive_subsystem` forces one `CatdvClient.login()` round-trip at
startup so an unreachable host or bad credentials degrade us to offline
cleanly (see [0023](./0023-boot-login-failures-keep-client-for-retry.md)).
That login was awaited directly, inside the FastAPI lifespan, *before*
the server starts serving. The `CatdvClient` carries a 60s httpx timeout,
so a silently unreachable CatDV (VPN drop, server off, no route) stalled
**every** dev restart for up to 60s before the offline-boot path ran.

The periodic and manual probes were already bounded — `ConnectionMonitor`
wraps `provider.health()` in `asyncio.wait_for(..., timeout=health_probe_timeout_s)`
(5s). Only the boot login was unbounded, an asymmetry that fell entirely
on restart latency.

## Alternatives

- **Lower the global `CatdvClient` timeout (60s → ~2s).** Rejected: that
  timeout is sized for `download_proxy` / `list_clips` / `get_clip`,
  which legitimately run long. Shrinking it globally would break real
  fetches against a healthy-but-slow server.
- **Move the login fully off the startup path into the background
  monitor** so the lifespan never awaits it. Deferred: the bigger,
  correct-but-riskier change — `login_failed` currently drives the
  resolver mode (cache-only vs rest) and the monitor's initial state, so
  making it async-resolved-later touches the careful offline-boot wiring.
  The bounded-wait gets ~92% of the benefit for ~5 lines; revisit if a
  truly-instant restart floor is needed.

## Decision

Wrap the boot login in `asyncio.wait_for(ctx.catdv.login(), timeout=
settings.catdv_startup_login_timeout_s)`, default **2.0s**, a new setting
distinct from both the 60s client timeout and the 5s probe timeout. On
timeout, `TimeoutError` falls through to the existing generic `except`
branch — client kept alive, `login_failed=True`, booted offline — so the
monitor can recover via the Reconnect button or the next background probe
with no code branch added. The setting is env-tunable
(`CATDV_STARTUP_LOGIN_TIMEOUT_S`) for snappier or stricter restarts.

## Consequences

- Restart without CatDV connectivity drops from ~60s to ≤2s (often
  near-instant when the host actively refuses the connection).
- The 60s client timeout is untouched, so large downloads/listings keep
  their headroom.
- A healthy-but-slow CatDV that takes >2s to answer at boot now boots
  offline and reconnects on the next probe/Reconnect, instead of holding
  startup. Acceptable: reconnection is automatic and the seat is freed by
  the same offline-boot path.
- A timed-out boot login leaves the client mid-flight (the `wait_for`
  cancels the coroutine); the `_login_lock` is released on cancellation
  and the kept-alive client is re-probed by the monitor — covered by
  `test_hanging_login_at_boot_is_bounded_and_recovers`.
