# 0078. Connection pill redesign + on-demand VPN re-probe

**Date:** 2026-06-12
**Status:** Accepted
**Lifespan:** Feature

## Context

The topbar connection chip was a flat row of dots + text buttons. The
connectivity prototype (Claude Design handoff) redesigns it as a Status pill
that opens a dropdown with one row per service (VPN tunnel, CatDV Annotator),
toggle switches, a VPN→CatDV dependency gate, and Retry affordances. The
backend connect/disconnect is synchronous request→response with no persistent
"connecting" state, and VPN health is only re-probed on the supervisor's timer
(`_health_loop`) — there was no way for a user to force a re-check.

## Alternatives

- **Optimistic client-side connecting state** — rejected: it would show a state
  the server doesn't hold; we represent "connecting" via the in-flight HTMX
  request only.
- **Wire the VPN Retry to `/api/vpn/enable`** — rejected: `enable()` is a no-op
  when `desired` is already `on`, so the button would lie.
- **Make Retry bounce the tunnel** — rejected: a wedged proc is already
  auto-respawned by `_supervise`; a deliberate fresh tunnel is disable()+enable().

## Decision

- Add `VpnSupervisor.probe_now()` — an on-demand, lock-guarded, best-effort
  re-probe (same probe the health loop runs) — and `POST /api/vpn/retry` that
  calls it and returns the chip partial with a toast.
- Rewrite `_connection_chip_inner.html` as the pill + dropdown, deriving all
  state from `vpn_supervisor.status()` + `connection_monitor` mode. The stable
  `#connection-chip` container owns `x-data="popover()"`; htmxAlpine re-inits the
  swapped subtree so the dropdown survives the 5s poll.
- Surface attempt-time failures (seat busy, login rejected) as toasts, with the
  row falling back to its Connect action — no invented persistent state.
- Move CATALOG + READ-ONLY from standalone topbar pills into the dropdown footer.

## Consequences

- The VPN Retry is honest (forces a real re-probe) without restarting the
  tunnel, preserving the seat/VPN discipline.
- One new external-action endpoint; mirrors the existing enable/disable shape
  (409 when unmanaged, chip partial + toast on success).
- All seat-release rules are unchanged: disconnect = logout, VPN disable = logout
  then drop tunnel.
