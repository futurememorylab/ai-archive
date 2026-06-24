# 0023. Boot-time login failures keep the CatDV client alive for retry

- **Date:** 2026-05-25
- **Status:** Accepted
- **Lifespan:** Invariant

## Context

[[ADR 0015]] added auto-degrade + manual reconnect: when boot-time
login fails, the app should still come up and serve cached reads,
and the user can click "Reconnect" once CatDV is reachable again.

The original implementation collapsed all login failures into one
catch-all (`except Exception`), tore down the `CatdvClient` (`ctx.catdv
= None`), and logged the same message ("CatDV unreachable at startup")
regardless of cause. Three real-world failure modes hit this path:

1. **Bad credentials** — `CatdvAuthError` (server returns
   `status:"ERROR"`, "Invalid user name or password").
2. **Seat limit reached** — `CatdvBusyError` (server returns either a
   `BUSY` envelope or — on the web servlet — an HTML 502 *"Web Client
   session limit reached (Maximum:2)"* page).
3. **Transport failure** — DNS/connect error, VPN down, etc.

Cases 2 and 3 are transient: a seat clears, the VPN comes back. But
because the boot code nulled out the client and the catdv adapter's
`health()` short-circuited on `_client is None`, the `ConnectionMonitor`'s
`retry_now()` had no client to probe — recovery required a process
restart. The misleading "unreachable" log also pointed users at the
wrong root cause when the real problem was seat exhaustion.

## Alternatives

(a) Leave the boot code alone but make `retry_now()` re-construct the
client from settings — works, but duplicates client setup logic and
forces the monitor to know about credentials. (b) Keep the catch-all
but rename the log to "boot login failed" — fixes the message but not
the unrecoverability. (c) Split the exception handling: distinguish
auth / busy / transport, only tear down the client on auth (the only
non-transient case), and drop the `is_online` short-circuit in
`provider.health()` so the monitor's probe path can actually run.

## Decision

(c). Three changes, all in the boot path or the recovery path:

1. **`backend/app/context.py`** — three separate `except` branches.
   `CatdvAuthError` keeps the existing tear-down (bad creds won't fix
   themselves; preserving the client would be misleading). `CatdvBusyError`
   and the catch-all both keep the client alive and log a cause-specific
   message ("seat limit reached" vs. "unreachable") with a "click
   Reconnect" hint.
2. **`backend/app/context.py`** — the `_is_online` closure no longer
   latches `login_failed` permanently. It delegates to
   `connection_monitor.current_state()` once the monitor exists, so a
   successful `retry_now()` flips the app back to online for routes and
   services without a restart.
3. **`backend/app/archive/providers/catdv/adapter.py`** — `health()`
   drops the `not self._is_online()` short-circuit and only short-circuits
   on `self._client is None`. The probe is the recovery path; gating it
   on the cached "is online" answer made recovery impossible.

## Consequences

Boot-time seat exhaustion or transport failure becomes recoverable from
the UI's Reconnect button instead of requiring a process restart. The
log message points at the real cause. The proxy resolver still drops to
`cache-only` at boot on any failure (lines 304-311 of `context.py`); a
follow-up could hot-swap it to the full REST resolver on recovery so
proxy downloads also resume without a restart, but that's out of scope
here — metadata reads and marker writes are the typical recovery
priority.

`CatdvAuthError` semantics are unchanged: bad creds still tear the
client down. The accompanying test `test_auth_error_at_boot_drops_client`
pins this so a future refactor doesn't accidentally relax it.
