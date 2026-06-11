# VPN (onetun) supervisor — status + on/off toggle

**Date:** 2026-06-11
**Status:** Draft (brainstorm output, pending review)
**Scope:** Cloud Run deployment only (where WireGuard is configured). Local
dev is unaffected.

## Context

On Cloud Run, CatDV is reached over a userspace WireGuard tunnel run by
`onetun` (ADR 0066/0067). Today `deploy/entrypoint.sh` spawns onetun in a
`while true; do onetun … ; sleep 2; done` loop, in a separate process from
the app. The app has no visibility into or control over the tunnel.

Two problems motivate this feature:

1. **No visibility.** The existing connection chip shows CatDV
   session/seat state (login/logout via the connection monitor's health
   probe), which *conflates* "tunnel up" with "CatDV reachable". When the
   tunnel is misbehaving (e.g. the observed `boringtun REKEY_TIMEOUT`), the
   UI just says "offline" with no indication that the VPN layer is the
   culprit.

2. **No control.** The cloud onetun reuses the operator's personal
   WireGuard peer key (`cloudrun.env.yaml` warns: "the cloud tunnel and
   that Mac's tunnel cannot be up at the same time — one endpoint per peer
   key"). When the operator works locally, the two tunnels collide and
   thrash the handshake. Today the only way to cede the tunnel is to stop
   the whole Cloud Run service. "Disconnect" (CatDV logout) does **not**
   help — onetun keeps running and keeps colliding.

The dedicated-peer-key hardening (separate cloud key, `AllowedIPs=
192.168.1.41/32`) is the eventual structural fix for the collision and is
tracked separately; it requires a change on the office WireGuard gateway.
This spec delivers operator-facing **status + an on/off toggle** so the
tunnel can be observed and ceded from the UI in the meantime, and as a
permanent operational control.

## Goals

- Show real VPN/tunnel state in the UI, distinct from CatDV session state:
  **Off**, **On · healthy**, **On · tunnel down**, **Error**.
- Let the operator turn the cloud tunnel **off** (cede to their Mac) and
  **on** from the UI, with the choice **persisted** across instance
  restarts. **Default is off** — a fresh instance boots with the tunnel down
  and the operator opts in (see Persistence).
- Make the master-switch relationship explicit: **VPN off ⇒ CatDV offline**
  (no tunnel = no seat). Turning VPN off releases the CatDV seat first
  (graceful logout), then stops the tunnel.

## Non-goals

- Local dev behaviour. When WireGuard is not configured, the feature is
  dormant and the UI control is not rendered. Local keeps talking to CatDV
  directly on the LAN.
- Fixing the peer-key collision itself (dedicated cloud key) — separate
  work; this feature is what makes the collision *manageable* until then.
- Per-route or partial tunnelling, multiple tunnels, or any onetun config
  editing from the UI. The toggle is binary.

## Architecture

### Gating: `vpn_managed`

The whole feature keys off a single derived flag. New `Settings` fields
hold the WireGuard config that today lives only in the entrypoint env:

```
wg_private_key: SecretStr | None   # WG_PRIVATE_KEY (Cloud Run secret)
wg_endpoint: str | None            # WG_ENDPOINT
wg_peer_pubkey: str | None         # WG_PEER_PUBKEY
wg_source_ip: str | None           # WG_SOURCE_IP
wg_keepalive_s: int = 25
onetun_mtu: int = 1380             # see ADR 0074; tunable
onetun_local_forward: str = "127.0.0.1:18080:192.168.1.41:8080:TCP"
```

`vpn_managed` ≔ all required WG fields present. **True only on the cloud
deployment.** When False: no supervisor is constructed, the `/api/vpn`
endpoints return `409 not managed`, and the UI omits the VPN element.

### `VpnSupervisor` service (`services/vpn_supervisor.py`)

A `LiveCtx`-scoped service that owns the onetun subprocess. One clear
purpose: keep onetun's actual state in line with the persisted desired
state, and report status.

State it tracks:
- **desired**: `on` | `off` — persisted (see below). The single source of
  truth for "should the tunnel be up".
- **process**: derived — is the onetun child alive (PID + returncode).
- **health**: derived — does the tunnel carry traffic. Reuses the existing
  connection monitor's unauthenticated `health()` probe result (which
  already flows through the tunnel) rather than adding a parallel probe.

Public interface:
- `await start()` — lifespan startup. Read desired state from the DB
  (default `on`). If `on`, spawn onetun and start the supervisor task.
- `await enable()` — set desired=`on` (persist), spawn onetun, return
  status. Idempotent.
- `await disable()` — set desired=`off` (persist). **Master-switch order:**
  (1) gracefully log out CatDV (release the seat) and pause the connection
  monitor; (2) SIGTERM onetun, await exit (SIGKILL fallback after a bound).
  Return status. Idempotent.
- `status() -> VpnStatus` — `{managed, desired, process, healthy}`.
- `await aclose()` — stop the supervisor task and SIGTERM onetun.

Internal supervisor task (replaces the shell `while` loop): while
desired==`on`, ensure onetun is alive; on unexpected exit, restart with a
short backoff (mirrors the entrypoint's `sleep 2`). While desired==`off`,
stay down. Subprocess via `asyncio.create_subprocess_exec` with the WG env
built from settings — no blocking calls in the event loop.

### Persistence

Desired state must survive Cloud Run restarts/redeploys — otherwise a
toggle-off silently reverts and re-collides. Persisted in the existing
`app_meta(key, value)` KV table (`repositories/app_meta.py`, which already
holds `install_id`) under key `vpn_desired` ∈ {`on`,`off`}. Add
`get_vpn_desired` / `set_vpn_desired` helpers there (repos stay leaves —
no service imports). The DB is Litestream-replicated, so the choice is
durable across instances.

**Default is `off`.** When the key is absent (fresh deploy / fresh data
dir), the tunnel does **not** auto-start; the operator must turn it on. This
is deliberate: it keeps the cloud from grabbing the shared WireGuard peer
key on boot and colliding with the operator's Mac, and it mirrors the
manual-connect philosophy (ADR 0068: boot disconnected, opt in to spend a
seat). VPN-off-by-default extends that one layer down — boot with the tunnel
down too, opt in to bring it up.

> **Behaviour change vs today:** currently onetun always runs (entrypoint
> starts it unconditionally). After this change a fresh cloud instance boots
> with the tunnel **off** and CatDV therefore unreachable until the operator
> enables the VPN. This is intended.

### Entrypoint change

Remove the onetun block from `deploy/entrypoint.sh`; the app now owns
onetun. litestream/uvicorn lines unchanged. The onetun binary stays in the
image. On SIGTERM the app's lifespan `aclose()` stops onetun, so shutdown
is *cleaner* than today (onetun no longer "just dies with the container").
This amends ADR 0067 — recorded in a new ADR.

## API

New router `/api/vpn` (mirrors `/api/connection` conventions: HTMX partial
on `HX-Request`, JSON otherwise; toast via `HX-Trigger`):

- `GET  /api/vpn/status` → `VpnStatus`.
- `POST /api/vpn/enable` → spawn, returns updated chip partial.
- `POST /api/vpn/disable` → confirm-gated (see UI), returns updated chip.

All three return `409` with a clear detail when `vpn_managed` is False.
VPN state changes are also published on the existing `EventBus`
`"connection"` topic so the chip's SSE subscription updates live.

## Frontend

Reuse the existing connection surface — **no new component vocabulary**
(per `design-language.md` / the design-language guard):

- Extend the connection chip/pill (`_connection_chip*.html`,
  `connection_pill.html`) with a VPN row: a `ui.status_pill` showing
  **Off / On · healthy / On · tunnel down / Error**, and a `ui.button`
  toggle.
- Turning **off** uses a `ui.modal` confirm ("This drops the CatDV
  connection and cedes the tunnel — continue?"), because it releases the
  seat and stops sync.
- When VPN is **off**, the existing CatDV Connect control is disabled/greyed
  (VPN is the master switch above it).
- When `vpn_managed` is False (local), the entire VPN row is absent.
- Errors and outcomes use `Alpine.store('toast')`; no `alert()` / reload.

## Error handling

- onetun spawn failure → status `Error`, toast via `humanise()`, supervisor
  retries with backoff; the chip shows the error state.
- `disable()` when CatDV logout fails → proceed to stop onetun anyway (the
  seat will time out server-side), but surface a warning toast. Stopping the
  tunnel is the operator's explicit intent and must not be blocked by a
  logout hiccup.
- `enable()` while already on, `disable()` while already off → no-op +
  current status (idempotent).
- Narrow exceptions per ADR 0042 conventions; user-facing strings via
  `services/errors.humanise`.

## Testing

- **Unit (`VpnSupervisor`)** with an injected spawn function (no real
  onetun): desired-state-on-boot, enable/disable transitions, restart-on-
  unexpected-exit, no-restart-while-off, persistence round-trip, aclose
  kills the child. Assert the master-switch order in `disable()` (logout
  before kill) via a recording fake.
- **Endpoints**: status/enable/disable happy paths; `409` when not managed;
  EventBus publish on change.
- **Gating**: with WG settings absent, no supervisor is built and the chip
  template omits the VPN row.
- **Design-language guard** stays green (reuse `ui.*`, no new classes).

## Manual acceptance flows

1. **Fresh deploy boots VPN off.** Deploy a fresh cloud instance (no prior
   `vpn_desired`). The connection chip shows **VPN: Off**, CatDV Connect is
   disabled, and the operator's Mac WireGuard works without contention.
2. **Toggle on brings up the tunnel.** Click the VPN toggle on. The chip
   moves to **On · healthy** within a few seconds; CatDV Connect becomes
   available; a manual Connect spends a seat and reaches CatDV.
3. **Toggle off cedes the tunnel.** Click the VPN toggle → confirm modal →
   confirm. Observe: a success toast, the CatDV seat is released (no longer
   "online"), CatDV Connect becomes disabled, and the chip shows **VPN:
   Off**. The Mac's WireGuard reconnects without REKEY_TIMEOUT thrash.
4. **Persistence across restart.** With VPN toggled **off**, restart the
   Cloud Run revision. After boot the chip still shows **VPN: Off** (the
   tunnel did not auto-start). Toggle on; restart again; it comes back
   **On**.
5. **Local dev unaffected.** Run the app locally (no WG env). The VPN row is
   absent from the chip; CatDV works directly on the LAN exactly as before.
6. **Tunnel-down is distinguishable.** With VPN on but the handshake failing
   (e.g. peer-key collision active), the chip shows **VPN: On · tunnel
   down** (process alive, health probe failing) rather than a bare
   "offline" — making the VPN layer the obvious suspect.

## Decisions to record

- New ADR amending **0067** (onetun now app-supervised, not entrypoint-run)
  and capturing the cloud-only gating + master-switch model.
