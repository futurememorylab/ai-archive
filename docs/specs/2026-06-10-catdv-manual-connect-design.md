# CatDV on-demand connect/disconnect + connection indicator

**Date:** 2026-06-10
**Status:** Approved (design)
**Builds on:** `docs/specs/2026-06-09-cloud-run-deployment-design.md`
(Phase 3 WireGuard), ADR 0066 (single-instance Cloud Run), ADR 0067
(onetun packaging).

## Problem

On Cloud Run the service runs `min-instances=1` and never stops, so the
current CatDV lifecycle — **log in at startup, log out only at
shutdown** — would hold one of CatDV's two session seats **24/7**. In
practice only one seat is free (the human web client usually holds the
other), so a permanently-held cloud seat locks the office out of CatDV.

We also have no way, while the app is running, to:

- bring CatDV up or down on demand (login is wired once at lifespan
  startup; there is no runtime connect/disconnect — see
  `context.py::_build_archive_subsystem` and `main.py` lifespan), or
- tell the operator whether the **WireGuard tunnel** is even up,
  separately from whether we are logged in.

This matters most while **testing the Phase 3 WireGuard tunnel**: the
tester needs to connect a seat deliberately, confirm it, release it, and
see at a glance whether the tunnel is reachable.

## Goal

Make CatDV connectivity **manual and on-demand**: the app boots
**disconnected** (no seat), the operator clicks **Connect** to spend a
seat, clicks **Disconnect** (or lets an idle timer fire) to release it,
and a single connection indicator shows tunnel/session state at a
glance.

Non-goals: changing the offline-degradation contract (the app must stay
navigable when CatDV is down — unchanged); Phase 5 optimistic
concurrency; a second CatDV seat.

## What already exists (reused, not rebuilt)

- **`ConnectionMonitor`** (`services/connection_monitor.py`): periodic
  `provider.health()` probe, `ConnectionState` machine, persistence to
  `connection_events`, `EventBus` `"connection"` broadcasts,
  `manual_offline`, `retry_now()`, halt-on-offline loop.
- **Routes** `/api/connection/{state,retry,offline,online,events,shutdown}`
  (`routes/connection.py`), including the SSE stream.
- **UI**: topbar **connection chip** (`templates/_connection_chip.html`,
  server-rendered) and **connection pill** (`templates/connection_pill.html`,
  polls `/ui/connection-pill` every 5 s) with manual online/offline
  toggles.
- **Client primitives** (`services/catdv_client.py`): `login()` (POST
  `/session`, takes the seat), `logout()` (DELETE `/session`, **frees
  the seat**, best-effort with a warning), and the **seat-free**
  `health()` probe — `GET /catdv/api/info` with `reauth=False`
  (`catdv_client.py:282`), which deliberately never logs in.

## Key insight: one seat-free probe yields both signals

`health()`'s `/api/info` probe (no login, no seat) already returns three
distinguishable outcomes. Today `ConnectionMonitor.probe_once` collapses
the last two into `offline`; we stop collapsing them:

| Probe outcome | Meaning | New `ConnectionState` |
|---|---|---|
| success (envelope OK) | logged in, seat held | `online` (Connected) |
| `CatdvAuthError` (AUTH envelope) | tunnel up, **not** logged in | `disconnected` (new) |
| transport error / timeout | **tunnel/VPN down** | `offline` (Unreachable) |

So a single probe tells us both "is the WireGuard tunnel up?" and "do we
hold a seat?" — no separate tunnel pinger is added.

## Design

### 1. Boot modes (settings)

New setting `catdv_connect_mode: Literal["auto", "manual"] = "manual"`
in `settings.py`. Three combinations, only the middle one is new:

| Config | Client built? | Login at boot? | Seat |
|---|---|---|---|
| `catdv_offline=true` | no | — | never (fully disabled) — **unchanged** |
| `catdv_connect_mode=auto` | yes | yes | held until shutdown (**today's behavior, now opt-in**) |
| `catdv_connect_mode=manual` *(default)* | yes | **no (deferred)** | held only between Connect and Disconnect |

`_build_archive_subsystem` builds the client in both `auto` and `manual`
(unchanged when `forced_offline`). In `manual` it **skips the startup
`asyncio.wait_for(catdv.login(), ...)`** and starts the monitor with
`initial_state=ConnectionState.disconnected`. The deployed service moves
to `catdv_offline=false` + `manual` once the tunnel is up; local
`./run.sh` may set `auto` to keep its current click-free boot.

### 2. ConnectionState + probe

Add `disconnected` to the `ConnectionState` enum (stored as a string in
`connection_events`, so no migration). `probe_once` gains an explicit
`except CatdvAuthError → disconnected` arm **before** the generic
`except Exception → offline`. `current_state()` returns `disconnected`
when reachable-but-logged-out; `is_forced`/`manual_offline` paths are
unchanged.

`retry_now()` stays a pure probe (never logs in) — it just refreshes the
reachable/unreachable signal.

**Loop cadence in `manual` mode.** The current `_loop` halts on any
non-`online` state (auto-mode behavior, preserved). In `manual` mode the
loop instead **keeps probing every `interval_s` regardless of state**, so
the indicator tracks `disconnected ↔ unreachable ↔ online` transitions
live (flow 5) — the probe is cheap and seat-free, so continuous polling
is safe. Connect/Disconnect each trigger an immediate `probe_once` (or
set state directly) so the pill flips without waiting a full interval.

### 3. Connect / Disconnect endpoints

Added to `routes/connection.py`, requiring `LiveCtx` (the client exists
in `manual` mode):

- **`POST /api/connection/connect`** — `await catdv.login()`. On success:
  `probe_once()` to flip state → `online` immediately (the manual-mode
  loop is already running). On `CatdvBusyError` → 409
  toast "CatDV seat busy (max 2 sessions)"; on `CatdvAuthError` → 401
  toast "CatDV login rejected"; on transport error → 502 toast "CatDV
  unreachable — check the VPN tunnel". **No seat is taken on any
  failure.** Returns the updated pill partial on `HX-Request`.
- **`POST /api/connection/disconnect`** — `await catdv.logout()` (frees
  the seat; already best-effort with a warning), stop the monitor loop,
  state → `disconnected`. Always returns the pill partial.

All user-facing errors go through `Alpine.store('toast')` and
`services/errors.py::humanise`, per the project rules.

### 4. Idle auto-disconnect (seat safety net)

`CatdvClient` stamps `self._last_activity` (monotonic) on every real API
call. The stamp lives in `_call_json` / `_call_json_with_params` /
downloads behind a `track_activity` flag, and **`health()` passes
`track_activity=False`** — so neither the 30 s background `health()`
probe, the 5 s pill poll, nor `/api/health` resets the idle clock (only
operator-driven CatDV calls do). Without this exclusion the probe would
reset idle forever and auto-disconnect would never fire. A background task (sibling of the monitor loop, started in
the same lifespan block, stopped in `aclose()` before logout) checks
every 60 s: if `logged_in` and `now - last_activity >
catdv_idle_logout_s` (new setting, default `900`), it calls the same
disconnect path — `logout()`, state → `disconnected`, publish a
`connection` event with `detail="idle auto-disconnect"` so the SSE/pill
reflect it and a toast informs the operator.

### 5. UI — one pill, sub-states

`connection_pill.html` renders four states from the one probe:

- ⚪ **Disconnected** (reachable, no seat) → primary **Connect** button
- 🟢 **Connected** (seat held) → **Disconnect** button
- 🔴 **Unreachable** (tunnel down) → **Connect disabled** with hint
  "VPN tunnel down"
- 🟡 **Connecting… / Disconnecting…** (transient, button shows spinner)

The pill keeps its 5 s `/ui/connection-pill` poll; Connect/Disconnect
POST returns the refreshed partial via `htmxAlpine.reinit`. The topbar
chip (`_connection_chip.html`) becomes **read-only** in `manual` mode
(its old "Reconnect → `/retry`" only probes and would mislead), showing
the same state label. Reuse `ui.status_pill` styling tokens; no new
`*-pill` vocabulary (design-language guard).

### Error handling & offline contract

Unchanged: when CatDV is `disconnected`/`offline`, `get_live_ctx`-gated
routes still return their typed 503, the clip list still renders
placeholders, and GCS-backed playback still works (Phase 4). Connect
failures never mark anything terminal and never hold a seat.

## Testing

TDD, `.venv/bin/python -m pytest` + `lint-imports` green per task:

- **probe mapping** — `probe_once` returns `online` / `disconnected` /
  `offline` for OK / `CatdvAuthError` / transport-error from a fake
  provider.
- **connect endpoint** — mocked `login()`: success → `online` + loop
  resumed; `CatdvBusyError`/`CatdvAuthError`/transport → mapped status +
  no state change to `online`; asserts no seat on failure.
- **disconnect endpoint** — mocked `logout()` called; state →
  `disconnected`; loop stopped.
- **idle task** — with a fake clock, no activity past the threshold
  triggers `logout()`; activity within it does not; pill poll does not
  count as activity.
- **pill render** — `connection_pill.html` shows the right
  button/label/disabled-state for each of the four states.
- **deferred boot** — `manual` mode builds the client but does not call
  `login()` at startup; `auto` mode preserves today's behavior.

## Manual acceptance flows

Run on the deployed service via
`gcloud run services proxy catdv-annotator --region europe-west3`
(serves `http://localhost:8080`), with the WireGuard tunnel configured,
`CATDV_OFFLINE=false`, `CATDV_CONNECT_MODE=manual`.

1. **Boots disconnected, no seat.** Open the proxied UI. The connection
   pill shows **⚪ Disconnected**. In the CatDV admin UI, **no** session
   originates from the cloud peer. (If the tunnel is misconfigured the
   pill instead shows **🔴 Unreachable** — see flow 5.)
2. **Connect takes exactly one seat.** Click **Connect**. The pill goes
   **🟡 Connecting…** then **🟢 Connected**; the clip list loads real
   CatDV data; the CatDV admin shows **exactly one** session from the
   cloud peer.
3. **Disconnect frees the seat.** Click **Disconnect**. The pill returns
   to **⚪ Disconnected**; the CatDV admin shows the cloud session
   **gone**. Cloud Run logs show the `DELETE /session` succeeded.
4. **Idle auto-disconnect frees a forgotten seat.** Click **Connect**,
   then leave the app untouched for the idle window (default 15 min,
   shorten via `CATDV_IDLE_LOGOUT_S` to test). The pill returns to
   **⚪ Disconnected** on its own, a toast says the session was released
   for inactivity, and the CatDV admin shows the seat freed.
5. **Tunnel down is visible and non-fatal.** While disconnected, disable
   the cloud peer on the office WireGuard server. Within the probe
   interval the pill shows **🔴 Unreachable** and **Connect** is disabled
   with the "VPN tunnel down" hint; the rest of the app stays navigable
   and a GCS-backed clip still plays (307 redirect). Re-enable the peer →
   the pill recovers to **⚪ Disconnected** without an app restart, and
   Connect works again.
6. **Local dev unchanged.** `./run.sh` with `CATDV_CONNECT_MODE=auto`
   against LAN CatDV still boots logged-in (no click), proving the
   auto-login path is intact behind the new opt-in.
