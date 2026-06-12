# Connectivity pill + dropdown redesign

**Date:** 2026-06-12
**Status:** Draft (approved for planning)
**Prototype:** `~/Downloads/connectivity-handoff.zip` → `connectivity/project/` (Claude Design handoff; variant **A "Status"** chosen)

## Summary

Replace the flat text connection chip in the topbar with a **Status pill +
dropdown**, recreating the chosen prototype variant pixel-for-feel while driving
it entirely from the *existing* backend surface. No new state model: the seat /
VPN discipline stays server-side (connect = CatDV login takes the seat,
disconnect = logout releases it, VPN disable logs out first then drops the
tunnel). One small honest backend addition — an on-demand VPN health re-probe —
is needed so the VPN "Retry" affordance is real rather than a no-op.

## Goals

- Single topbar **pill**: status dot · overall label · separator · weakest-link
  subtext · chevron. Opens a dropdown.
- **Dropdown** with one row per service (VPN tunnel, CatDV Annotator), each a
  toggle switch (or **Retry** when in a persistent error state), a VPN→CatDV
  dependency gate, a hint line, and a footer meta line (catalog · read-only ·
  cached/live).
- Cover the connecting and error states the prototype demonstrates, mapped
  honestly onto the real synchronous backend.
- Preserve every existing rule: seat release on disconnect/VPN-off, app's hard
  dependency on the VPN for CatDV, graceful behaviour when fully offline.

## Non-goals

- No new persistent "connecting/disconnecting" server state. "Connecting…" is
  represented by the in-flight HTMX request (`hx-indicator`) only.
- No optimistic client-side state machine — the pill never shows a state the
  server does not actually hold.
- No change to the cache layers, the "Showing cached clips only" content banner,
  the job indicator, the DEV env pill, or the shutdown button.

## What is reused (do not re-invent)

- **Stable `#connection-chip` container** + its 5s `GET /ui/connection-chip`
  poll. Routes target this id; it is never destroyed (innerHTML swap only).
- **Existing action endpoints**, unchanged in contract:
  `POST /api/vpn/enable`, `POST /api/vpn/disable`,
  `POST /api/connection/connect`, `POST /api/connection/disconnect`,
  `POST /api/connection/retry`. Each already returns `_connection_chip_inner.html`
  for HX requests.
- **Toast bridge**: responses carry `HX-Trigger: {"toast": {...}}` →
  `static/toast.js` → `Alpine.store('toast')`. (design-language: never `alert()`.)
- **`popover()`** behaviour (`static/popover.js`) for open / click-outside / Esc
  — per design-language §8. The panel is a `popover-panel` with custom content,
  **not** a new `*-menu` vocabulary.
- **`window.htmxAlpine.reinit()`** (single lifecycle helper) to rebind Alpine in
  the re-rendered panel after each innerHTML swap.
- **Existing design tokens** (`app.css :root`): `--good` (green / connected),
  `--bad` (red / error), `--accent` (amber / connecting), `--text-3` (slate /
  deliberately off). No new colour tokens.

## State model & derivation

Two real inputs, both already available to the partial via `request`:

- **VPN** — `live.vpn_supervisor.status()` → `VpnStatus(managed, desired ∈
  {on,off}, process_running, healthy)`. `managed` is false locally (no
  WireGuard) and true in cloud.
- **CatDV** — `live.connection_monitor` → mode ∈ {online, disconnected, offline,
  forced_offline} (existing `_mode()` derivation in `_connection_chip_inner.html`).

### Overall pill (Variant A — Status)

| Condition | Pill label / colour | Subtext |
|---|---|---|
| (VPN healthy **or** unmanaged) **and** CatDV online | `● Online` — green | All connected |
| any connect/disconnect/retry POST in flight | `● Connecting` — amber, pulsing | *(via `hx-indicator`)* |
| VPN `desired=off` | `● Offline` — slate | VPN off |
| VPN `desired=on` but `healthy=false` | `● Offline` — red | VPN unreachable |
| VPN up, CatDV disconnected | `● Offline` — slate | CatDV disconnected |
| VPN up, CatDV offline/unreachable | `● Offline` — red | CatDV unreachable |
| `forced_offline` (`CATDV_OFFLINE=true`) | `● Offline` — red | Offline (forced) |

When `vpn.managed` is false the overall status is derived from **CatDV alone**
(local dev shows no VPN layer).

### Service rows (dropdown)

**VPN tunnel row** — rendered only when `vpn.managed`:

| `desired` / `healthy` | State line (colour) | Control |
|---|---|---|
| `off` | Off (slate) | switch **off** → `POST /api/vpn/enable` |
| `on` + healthy | Connected (green) | switch **on** → `POST /api/vpn/disable` (keeps existing `hx-confirm`) |
| `on` + not healthy | Unreachable (red) | **Retry** → `POST /api/vpn/retry` (new) |

**CatDV Annotator row** — always rendered:

| Condition | State line (colour) | Control |
|---|---|---|
| VPN down (managed & `desired=off`/unhealthy) | Requires VPN (slate, disabled) | switch **disabled**; hint shown |
| CatDV online | Connected (green) | switch **on** → `POST /api/connection/disconnect` |
| CatDV disconnected | Disconnected (slate) | switch **off** → `POST /api/connection/connect` |
| CatDV offline/unreachable | Unreachable (red) | **Retry** → `POST /api/connection/retry` |
| `forced_offline` | Offline (forced) (red) | read-only, no control |

A VPN→CatDV connector line renders between the rows (green when both are up).
A hint line — "CatDV can only connect once the VPN tunnel is up." — shows when
the CatDV row is gated by a down VPN.

### Errors & toasts

- **Persistent** error states (VPN unreachable, CatDV unreachable) render a red
  row with **Retry** (real re-probe — see below).
- **Transient, attempt-time** failures the backend only learns on connect —
  **CatDV seat busy** (`CatdvBusyError` → 409), **login rejected**
  (`CatdvAuthError` → 401), **unreachable** (→ 502) — already surface as
  **toasts** via `HX-Trigger`; the row falls back to its Connect action (which
  doubles as retry). No new persistent state is invented for these.
- Add matching **success** toasts for CatDV connect/disconnect (VPN enable/disable
  already toast) so every transition gives feedback, matching the prototype.

## The VPN "Retry" — small honest backend addition

VPN `healthy=false` means the tunnel **process is up** but the health probe
through it fails (path-MTU blackhole / seat / CatDV server). The supervisor
already self-heals (`_health_loop` re-probes every `health_interval_s`;
`_supervise` auto-respawns a dead proc with backoff) and the 5s chip poll
surfaces recovery — but there is **no on-demand re-probe**. Wiring a Retry
button to `/api/vpn/enable` would be a **no-op** (enable() does nothing when
`desired` is already `on`). So:

- **`VpnSupervisor.probe_now() -> VpnStatus`** — under the existing `_lock`: if
  the proc is running, set `self._healthy = await self._probe_health()`; return
  `self.status()`. Same probe the loop runs, just on demand. Mirrors the existing
  health-loop shape; `_probe_health` failures are already swallowed best-effort
  there, so `probe_now` catches the same way and reports `healthy=false`.
- **`POST /api/vpn/retry`** — 409 when not VPN-managed (mirrors enable/disable);
  otherwise `await sup.probe_now()` and return `_connection_chip_inner.html`
  with a toast (`"VPN reachable"` / `"VPN still unreachable"`).

It does **not** bounce the tunnel: a wedged proc is already auto-respawned, and a
deliberate fresh tunnel is "Turn off → Turn on" (the existing disable/enable).
This keeps the seat/VPN discipline intact.

## Open/close lifecycle (interaction with the 5s poll)

- `x-data="popover()"` lives on the **stable** `#connection-chip` container
  (not the swapped innerHTML), so the `open` flag survives every poll and every
  action-triggered innerHTML swap. The dropdown therefore stays open while the
  user acts inside it.
- The polled inner partial renders the pill trigger (`@click="toggle()"`) and the
  panel (`x-show="open"`) in **hosted mode** (no own `x-data` — they bind to the
  container's popover scope).
- After each innerHTML swap, `window.htmxAlpine.reinit(container)` re-processes
  the subtree so the new trigger/panel rebind. (Single-lifecycle rule — those
  calls live only in `static/htmxAlpine.js`.)

## Topbar consolidation

`_topbar_pills.html`: **remove** the standalone `CATALOG {id}` and `READ-ONLY`
env-pills — they move into the dropdown footer. Keep the job indicator, the
connection chip (now the pill), the shutdown button, and the `DEV · {netloc}`
env pill.

## Files touched

- `templates/_connection_chip_inner.html` — rewrite to pill + dropdown (bulk of
  the work; all derivation logic in Jinja from `vpn` + `mode`).
- `templates/_connection_chip.html` — add `x-data="popover()"` to the stable
  container; keep the id, poll, and innerHTML swap.
- `templates/pages/_topbar_pills.html` — remove CATALOG + READ-ONLY pills.
- `static/app.css` — add `.conn-pill`, `.conn-dropdown`, `.conn-svc`,
  `.conn-switch`, dependency-connector, and state-colour rules, all mapped to the
  existing tokens.
- `services/vpn_supervisor.py` — add `probe_now()`.
- `routes/vpn.py` — add `POST /retry`.
- `routes/connection.py` — add success-toast `HX-Trigger` on connect/disconnect.
- `docs/adr/NNNN-vpn-on-demand-reprobe-and-connection-pill.md` + `docs/decisions.md`
  — record the `probe_now`/`/api/vpn/retry` addition and the pill redesign.

## Testing (TDD-first)

Write failing tests before implementation:

1. **Partial render matrix** (render `_connection_chip_inner.html` with stub
   `vpn` + `mode`): assert pill label + colour class + subtext, each row's state
   line, switch on/off/disabled, Retry presence, the VPN→CatDV gate + hint, and
   VPN-row hidden when `managed=false`. One case per row in the tables above.
2. **`probe_now()`** (`vpn_supervisor`): proc running + probe returns true →
   `healthy=true`; probe raises → `healthy=false`; proc not running → unchanged
   /`healthy=false`. Done under the lock.
3. **`POST /api/vpn/retry`**: 409 when unmanaged; HX request returns the chip
   inner partial; carries the expected toast header.
4. **connect/disconnect success toasts**: assert the new `HX-Trigger` headers.
5. **design-language guard**: the new partial uses `popover()` / `popover-panel`,
   not a hand-rolled `*-menu`/`modal-*` vocabulary (extend / satisfy
   `test_design_language_guard.py`).

## Manual acceptance flows

Run against a dev server (`server-start` skill, port 8765) unless noted.

1. **Happy path (cloud / VPN-managed).** Open the app with VPN off. The topbar
   pill reads `● Offline · VPN off`. Click it → dropdown opens with a VPN tunnel
   row (switch off) and a disabled CatDV row showing "Requires VPN" + the hint.
   Toggle VPN on → the row spins while the POST runs, then shows `Connected`
   (green), a "VPN tunnel enabled" toast appears, the CatDV row enables, and the
   connector line turns green-ready. Toggle CatDV on → spins, then `Connected`,
   "CatDV connected" toast; pill now reads `● Online · All connected`. Footer
   reads `CATALOG <id> · READ-ONLY · live`.

2. **Seat release on disconnect.** From flow 1's connected state, toggle CatDV
   off → "CatDV disconnected" toast, pill returns to Offline, footer shows
   `cached`. Confirm in the server log that the seat was released
   (`DELETE …/session`). Toggle VPN off (confirm the `hx-confirm`) → CatDV stays
   down and the tunnel drops.

3. **VPN-gate.** With VPN off, confirm the CatDV switch is disabled and clicking
   it does nothing; the hint "CatDV can only connect once the VPN tunnel is up."
   is visible.

4. **CatDV unreachable → Retry.** Put CatDV into an unreachable/offline state
   (VPN up, CatDV server unreachable, or simulate). The CatDV row shows
   `Unreachable` (red) with a **Retry** button; clicking it re-probes
   (`/api/connection/retry`) and the row resolves to Connected or stays red.

5. **VPN unreachable → Retry.** With VPN `desired=on` but `healthy=false`, the
   VPN row shows `Unreachable` (red) with a **Retry** button; clicking it forces
   an on-demand re-probe (`/api/vpn/retry`) and the row updates to Connected or
   stays red with a "VPN still unreachable" toast. Confirm it does **not** bounce
   the tunnel.

6. **Seat-busy toast.** With the single CatDV seat already taken elsewhere,
   toggle CatDV on → a "CatDV seat busy" toast appears and the row stays
   Disconnected with its Connect/Retry action (no fake persistent state).

7. **Local dev (VPN unmanaged).** Run locally (`vpn.managed=false`). The dropdown
   shows **only** the CatDV row; no VPN row, no gate hint. The pill's overall
   status reflects CatDV alone. Connect/disconnect behave as in flow 1/2.

8. **Dropdown survives the poll.** Open the dropdown and leave it open past the
   5s poll boundary; it stays open and the row contents refresh in place without
   closing. Click outside / press Esc → it closes.

9. **Topbar consolidation.** Confirm the standalone CATALOG and READ-ONLY pills
   are gone from the topbar and that catalog + read-only now appear in the
   dropdown footer; the DEV env pill, job indicator, and shutdown button are
   unchanged.
