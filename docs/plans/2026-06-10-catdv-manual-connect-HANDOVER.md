# Handover — CatDV manual connect/disconnect + Cloud Run testing

**Date:** 2026-06-10
**Branch:** `cloud-run-deployment` (PR #42), **20 commits ahead of `origin` — NOT pushed**
**Spec:** `docs/specs/2026-06-10-catdv-manual-connect-design.md`
**Plan:** `docs/plans/2026-06-10-catdv-manual-connect.md`
**ADR:** `docs/adr/0068-catdv-manual-connect.md`

## TL;DR

Two bodies of work landed on this branch this session, both local-only:

1. **Cloud Run deployment (PR #42 itself)** — an offline-first deploy was
   pushed to Cloud Run earlier (project `catdav`, `europe-west3`). See the
   live-deploy notes below.
2. **CatDV manual connect/disconnect feature** — built TDD via the plan
   (11 tasks) + 6 follow-up fixes found during live manual testing. This is
   the **testing strategy** for PR #42's Phase 3 (WireGuard): boot
   disconnected (no seat), click Connect to spend a seat, Disconnect/idle to
   release it.

Full suite: **1303 passed, 4 skipped** (locally `--ignore` the 3 modules
that need `PIL`/`radon`, which aren't in this `.venv`; CI has them).
`lint-imports`: 5 contracts kept, 0 broken.

## Current running state (READ FIRST — CatDV seat discipline)

- **A dev server is RUNNING**: `uvicorn backend.app.main:app --reload` on
  `127.0.0.1:8765`. Parent PID **85602**, worker reparented on each reload.
  Log: `/tmp/catdv-annotator-dev.log`.
- Connection mode is **manual** (the new default), so boot does NOT take a
  seat. A seat is held only between a UI **Connect** and **Disconnect**
  (or until idle auto-disconnect, default 900s).
- **Seat caveat:** if you Connect and the WireGuard tunnel then drops, the
  app stays "logged in" but can't send `DELETE /session` (tunnel down), so
  the seat can linger server-side until CatDV times it out. To release
  cleanly: Disconnect **while the tunnel is up**, then stop the server.
- **Stop the server gracefully (never `kill -9`):**
  `/bin/kill -TERM 85602` — confirm `Application shutdown complete.` in the
  log (that line means `aclose()` ran the CatDV logout). Use the
  `server-stop` skill. At handover time the state was `offline` (tunnel
  down); verify in the CatDV admin UI whether a cloud session lingers.

## What the manual-connect feature does (architecture)

- **Settings** (`settings.py`): `catdv_connect_mode: "auto"|"manual"`
  (default `manual`), `catdv_idle_logout_s` (default 900). `auto` = legacy
  login-at-startup (kept for local dev); `CATDV_OFFLINE=true` still = fully
  disabled, no client.
- **Seat truth is `CatdvClient.logged_in`, NOT the probe.** `/catdv/api/info`
  is **public** (returns 200 logged-out — confirmed live), so a probe-only
  model would falsely read "connected". `ConnectionMonitor.probe_once`
  (manual mode): `online` only when `logged_in() AND ok`; reachable-but-
  logged-out → new `disconnected` state; transport error → `offline`
  (Unreachable). `ProviderHealth.reachable` distinguishes the last two.
- **Endpoints** (`routes/connection.py`): `POST /api/connection/connect`
  (login → online; failures → 409/401/502 + HX-Trigger toast, never a
  seat), `/disconnect` (logout → disconnected). `/retry` re-probes.
- **Idle auto-disconnect** (`services/idle_disconnector.py`): logs out after
  inactivity; the background health probe is excluded from the activity
  clock (`CatdvClient.last_activity`, `track_activity=False` on `health()`).
- **UI — the topbar chip is the live control** (`_connection_chip.html`
  container + `_connection_chip_inner.html`): Connect / Disconnect / Retry,
  self-polls `/ui/connection-chip` every 5s.

## Live-testing fixes (the hard-won part — read before touching the chip UI)

The plan assumed the **connection pill** was the live control surface. It
was NOT — the pill (`connection_pill.html`, `/ui/connection-pill`) is
**orphaned scaffolding, never mounted on any page**. Only the topbar chip
renders. Fixes, in order:

1. **`ea1158b`** Unreachable pill offered a dead disabled button → gave it a
   working Retry. (Pill — now superseded by the chip work.)
2. **`fceb99a`** Made the **chip** the live interactive control; found
   `layout.html` had a DUPLICATE `mode` computation with no `disconnected`
   branch → it set `mode="offline"` and leaked into the chip include (Jinja
   includes inherit parent context; the chip only self-computes `mode` when
   undefined) → permanent stale "Unreachable". Fixed layout's mode too.
3. **`e6ebed6`** `htmx:swapError` "Cannot read properties of null
   (querySelector)" + needing a double-click: the action button lived inside
   `#connection-chip` and swapped it via **outerHTML**, destroying the
   polling element. Fixed with a **stable container + `innerHTML`** swap
   (inner partial). `/ui/connection-chip` returns the inner with
   `Cache-Control: no-store`.
4. **`d84f9a0`** Top progress bar (`#app-progress` / `nav-feedback.js`) stuck
   "loading": the `innerHTML` swap removes the triggering button before
   `htmx:afterRequest`, so it fires on a detached node, never bubbles to the
   body listener, and the paired `done()` is missed → counter leak. Fixed by
   opting the chip out of the global bar (like background pollers).

**Lesson for the next session:** the chip is `#connection-chip` (stable
container) + `_connection_chip_inner.html` (swapped innerHTML). Action
buttons target `#connection-chip` with `hx-swap="innerHTML"`. Don't go back
to outerHTML self-swaps.

## Verified vs not (manual acceptance flows, spec §"Manual acceptance flows")

Tested **locally** (`./run.sh`-style dev server, real CatDV via the
operator's local WireGuard tunnel), NOT yet on the Cloud Run deploy:

- ✅ Boots **Disconnected** (no seat); chip shows `○ Disconnected` + Connect.
- ✅ **Connect** (single click) → `● Connected`, real CatDV clip data loads.
- ✅ **Unreachable** when the tunnel is down → `● Unreachable` + Retry;
  Retry re-probes and recovers without restart.
- ⬜ **Disconnect frees the seat** — confirm in CatDV admin (was mid-test).
- ⬜ **Idle auto-disconnect** (shorten `CATDV_IDLE_LOGOUT_S` to test).
- ⬜ All flows on the **deployed Cloud Run service** with the cloud
  WireGuard tunnel (Phase 3 proper — see below).

## Remaining work

1. **Retire the orphan pill** (cleanup): delete `connection_pill.html`,
   `/ui/connection-pill` (`routes/ui.py`), `_pill_context`, the pill branch
   in `routes/connection.py::_pill_or_json`, and the pill tests
   (`test_connection_pill_render.py`, the pill cases in
   `test_routes_connect_disconnect.py`). The chip is the single renderer now;
   two renderers violate the repo's "don't parallel-evolve" rule. Left
   undone deliberately to keep the live-debugging diffs tight.
2. **Push to update PR #42** — 20 commits ahead of origin; nothing pushed.
   Confirm the branch/rebase before pushing (git workflow rule).
3. **Cloud Run + WireGuard (PR #42 Phase 3, operator):** stand up the office
   WG peer + `wg-private-key` secret + the 3 `WG_*` values in
   `deploy/cloudrun.env.yaml`; set `CATDV_OFFLINE=false` (the yaml already
   has `CATDV_CONNECT_MODE: "manual"`); redeploy; walk the acceptance flows
   on the proxied service (`gcloud run services proxy catdv-annotator
   --region europe-west3`). See `docs/plans/2026-06-09-cloud-run-deployment-HANDOVER.md`
   and `cloud-run-deploy-state` memory.
4. **CI/CD** (WIF + github-deployer + GitHub repo secrets) — still deferred;
   the deploy workflow's `--set-secrets` references `wg-private-key:latest`,
   so it can't go green until Phase 3 lands.

## Cloud Run deploy state (from earlier this session)

Offline-first deploy is **live**: `catdv-annotator` in `europe-west3`,
private, `min=max-instances=1`. URL
`https://catdv-annotator-204842536530.europe-west3.run.app`. Litestream
replicating to `gs://catdv-annotator-db`. Secrets `catdv-password` +
`gemini-api-key` created (lowercase, matching the deploy config). The PR's
Dockerfile was fixed (`e11b836`) — onetun has no container image; it's
fetched as a pinned release binary. Full detail in the
`cloud-run-deploy-state` memory file.

## Suggested continuation prompt for the next session

> We're testing PR #42 (`cloud-run-deployment`). Read
> `docs/plans/2026-06-10-catdv-manual-connect-HANDOVER.md` first. A dev
> server may still be running on :8765 (manual connect mode) — check with
> the `server-start` skill's pre-flight before launching, and mind the
> CatDV seat. Continue the manual acceptance flows (Disconnect-frees-seat,
> idle auto-disconnect), then decide on: retiring the orphan pill, pushing
> the 20 commits to PR #42, and the cloud WireGuard deploy. Keep `pytest` +
> `lint-imports` green per change; run Python via `.venv/bin/python`; stop
> the server only with `kill -TERM` (never -9).
