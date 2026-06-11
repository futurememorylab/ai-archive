# 0075. onetun is app-supervised (VPN status + toggle), default off

**Date:** 2026-06-11
**Status:** Accepted

## Context

ADR 0066/0067 ran onetun from `entrypoint.sh` in a restart loop, opaque to
the app. The operator needed (a) visibility into the tunnel and (b) the
ability to cede it to their Mac (the shared-peer-key collision behind issue
#43, see ADR 0074). "Disconnect" only logs out of CatDV; it does not stop
the tunnel, so the collision persisted.

## Decision

Move onetun supervision into the app (`services/vpn_supervisor.py`,
`LiveCtx`-scoped): spawn/restart/kill as an asyncio subprocess plus a
health-probe loop. Desired on/off state is persisted in `app_meta`
(`vpn_desired`, **default off** — opt-in, mirrors manual-connect ADR 0068).
`/api/vpn` exposes status/enable/disable; the connection chip gains a VPN
row. The feature is gated on `Settings.vpn_managed` (WireGuard configured)
so local dev is unaffected. `disable` is a master switch: the route logs out
of CatDV over the live tunnel and pins the monitor offline before the
supervisor drops the tunnel. The WG private key is passed to onetun via
`ONETUN_PRIVATE_KEY` env (not argv) — closes the "key on CLI" exposure.

## Consequences

- Fresh cloud instances boot with the tunnel **off**; CatDV is unreachable
  until the operator enables it. Intended (no auto-collision).
- onetun lifecycle is tied to the app lifespan — cleaner shutdown than the
  detached entrypoint loop.
- Amends ADR 0066/0067 (entrypoint no longer runs onetun). MTU stays at 1380
  (ADR 0074), now passed as a flag by the supervisor from `Settings.onetun_mtu`.
