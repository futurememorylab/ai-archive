# Handover — Investigate CatDV writeback failure (issue #43)

**Date:** 2026-06-11
**For:** a fresh session that will root-cause why applying a draft annotation
never reaches published CatDV from the cloud.
**Read first:** this doc, then ADR 0074 (the investigation so far) and ADR
0075 (the VPN supervisor you'll use as the investigation tool). Memory
[[cloud-writeback-readtimeout]] and [[cloud-run-deploy-state]] have the
condensed version.

---

## Continuation prompt (paste to start)

> Investigate why CatDV writeback (apply-draft → `PUT /clips/{id}`) times out
> from the Cloud Run deployment (issue #43). The "large payload" and "MTU"
> theories are already **disproven** (see below). The leading cause is a
> **WireGuard peer-key collision** between the cloud onetun tunnel and my
> Mac's WireGuard (both reuse the same peer key at `gw.pragafilm.cz`). The
> app now has a VPN supervisor (toggle the tunnel on/off via `/api/vpn`,
> default off, rev `00012-bsk`). Confirm or refute the collision, then fix
> it (dedicated cloud peer key). Do NOT write to production CatDV without my
> explicit go-ahead. CatDV has a 2-seat limit — be disciplined.

---

## Problem statement

Applying an accepted draft annotation in the cloud app enqueues a CatDV
`PUT /clips/{id}` writeback that **always times out** (`httpx.ReadTimeout`,
60 s), retries to `sync_max_attempts` (10), then goes `failed`. The
published CatDV record never changes. The UI shows a green "N proposals
applied — syncing to CatDV" toast that **lies** (it fires optimistically
before the PUT — see "Separate code defects" below), so the only honest
signal is `GET /api/sync/pending`.

## What is CONFIRMED / RULED OUT (don't re-investigate)

1. **NOT a large payload.** Clip `888894` full JSON ≈ **10.9 KB**; the PUT
   body is a *subset* (only changed markers/fields/notes —
   `archive/providers/catdv/payload.py::build_put_payload` is already
   minimal). A few KB.
2. **NOT the MTU.** With `ONETUN_MTU=1380` confirmed active on the serving
   revision, a live **90 KB inbound read** (`GET /api/catdv/clips?limit=20`)
   came back in **0.37 s**, while the few-KB **outbound** writeback PUT still
   timed out. Packet size is not it. (1380 is kept as correct GCP hygiene,
   not as the fix — ADR 0074.)
3. **The failure is direction-specific.** Inbound bulk (reads, even 90 KB)
   and tiny outbound (login `POST /session`) both succeed; only **outbound
   multi-segment request bodies** (the PUT) stall. This is the core clue.
4. **It's the PUT, not the pre-PUT GET.** `adapter.apply_changes` does a live
   `get_clip` (≈10 KB) *before* `put_clip`; an `httpx.ReadTimeout` from
   either surfaces identically as `"ReadTimeout"` (the sync engine's generic
   `except`). But same-size live GETs succeed, so the 60 s is burned on the
   PUT.
5. **`boringtun REKEY_TIMEOUT`** appears recurrently in the Cloud Run logs —
   the userspace WireGuard rekey handshake is failing.

## Leading hypotheses (ranked)

- **H1 — WireGuard peer-key collision (most likely).** `cloudrun.env.yaml`
  reuses the operator's **personal** WG peer key (`WG_SOURCE_IP=192.168.3.5`,
  key = secret `wg-private-key`) and warns verbatim that the cloud tunnel and
  the Mac's tunnel "cannot be up at the same time — one endpoint per peer
  key." During testing the Mac had multiple WireGuard tunnels up (`utun*` @
  MTU 1380). A WireGuard endpoint holds **one session per peer key**, so the
  two clients thrash: when the cloud must *send* (the PUT body) it needs a
  rekey, which collides with the Mac's session → `REKEY_TIMEOUT` → outbound
  stalls. Reads ride an already-valid session, so they slip through. Fits
  every symptom.
- **H2 — onetun v0.3.10 outbound (smoltcp) stall.** Structural inability to
  forward client→server request bodies reliably, independent of the
  collision. No newer onetun exists. If H1 is refuted, this is next.
- **H3 — CatDV server-side PUT hang (unlikely).** LAN writeback worked
  historically, so the difference is the tunnel, not CatDV.

## Decisive next experiments (in order)

1. **Kill the collision, retest (cheapest).** Bring the Mac's WireGuard
   **fully down**, then in the cloud app: `POST /api/vpn/enable` → wait for
   `/api/vpn/status` `healthy:true` → `POST /api/connection/connect` →
   apply a draft → watch `GET /api/sync/pending`. If the op goes `applied`
   (not `ReadTimeout`) → **H1 confirmed**. (Applying writes to prod CatDV —
   get the user's OK first; use a throwaway clip.)
2. **If H1 confirmed → fix it: dedicated cloud WG peer key.** Create a new
   peer on `gw.pragafilm.cz` (separate private key, its own `WG_SOURCE_IP`,
   `AllowedIPs=192.168.1.41/32`), update the `wg-private-key` secret +
   `WG_SOURCE_IP`/`WG_PEER_PUBKEY` in `cloudrun.env.yaml`, rebuild+redeploy.
   Then cloud and Mac never collide and the tunnel can stay on. (This is the
   "eventual hardening" the env file + ADR 0074 already name; needs office
   gateway access — ask the user / network admin.)
3. **Isolate tunnel vs CatDV-server (only if H1 refuted).** From the Mac
   (LAN, no tunnel) do GET then `PUT /catdv/api/9/clips/{id}` directly to
   `192.168.1.41:8080` with a real payload. Fast success → CatDV is fine →
   it's onetun (H2). **Takes a seat + writes prod — explicit consent only.**
4. **onetun packet-level evidence.** Temporarily add `--log debug` (and/or
   `--pcap`) to the onetun argv in `context.py::_build_sync_subsystem`'s
   spawn closure, redeploy, toggle VPN on, attempt a writeback, and read
   `gcloud logging read` for whether the PUT body segments leave and ACKs
   return, and whether `REKEY_TIMEOUT` lines coincide with the PUT.

The **VPN supervisor shipped this session is the investigation harness**:
it lets you bring the tunnel up/down and read `process_running`/`healthy`
without redeploying.

## Current deployment state (your starting point)

- **Service:** `catdv-annotator`, region `europe-west3`, **project `catdav`**
  (NOT `catdv` — that typo returns a misleading `PERMISSION_DENIED`).
- **Live revision:** `catdv-annotator-00012-bsk`, image
  `app:681470118ba6596b269d5058daeb97eb6c373132` (commit `6814701`, pushed to
  `origin/cloud-run-deployment`).
- **VPN supervisor is live, default OFF.** Verified:
  `/api/vpn/status` → `{managed:true, desired:"off", process_running:false,
  healthy:false}`; `/api/health` → `{status:ok, mode:offline}`. So the cloud
  boots with **no tunnel** — CatDV is offline until you `POST /api/vpn/enable`.
- **Access:** `gcloud run services proxy catdv-annotator --region europe-west3`
  → `http://localhost:8080`. Or hit the private URL with
  `-H "Authorization: Bearer $(gcloud auth print-identity-token)"`.

## Key endpoints (all read-only except where noted)

- `GET /api/sync/pending` — the writeback queue (status / attempts / last_error). **The source of truth.**
- `GET /api/vpn/status` · `POST /api/vpn/enable` · `POST /api/vpn/disable` — tunnel control.
- `GET /api/connection/state` · `POST /api/connection/connect` (takes a seat) · `/disconnect`.
- `GET /api/catdv/clips/{id}` — live `get_clip` (may hit clip_cache; fast = cached).
- `GET /api/catdv/clips?limit=N` — always-live tunnel read (used to prove 90 KB inbound works).
- `GET /api/review/clips/{id}/draft-data` — the draft arrays.
- `POST /api/review/apply-batch` `{clip_ids:[...]}` — **WRITES PROD CatDV.** Accepts + enqueues.
- `POST /api/sync/pending/{op_id}/retry` — re-arm a `failed` op.

## Key files

- `archive/providers/catdv/adapter.py` — `apply_changes` (live get_clip → put_clip; only `CatdvError/Auth/Busy` are narrowed — `httpx.ReadTimeout` falls through to the sync engine).
- `archive/providers/catdv/payload.py` — `build_put_payload` (minimal body; markers replaced wholesale).
- `services/catdv_client.py` — `put_clip`/`get_clip`/`health`; **60 s default httpx timeout** (`__init__`).
- `services/sync_engine.py` — `_loop` (drain every 5 s + on notify), `_tick` (mark_retryable → mark_failed at `sync_max_attempts`).
- `services/write_queue.py` — `enqueue_apply_for_clip` (the optimistic mark_applied).
- `services/vpn_supervisor.py` + `routes/vpn.py` — the tunnel harness.
- `deploy/entrypoint.sh` (onetun NO LONGER here), `deploy/cloudrun.env.yaml` (WG_* + ONETUN_MTU).
- ADR 0074 (writeback investigation, MTU hygiene, real cause), ADR 0075 (app-supervised onetun).

## Separate code defects (track on #43, independent of transport)

These make a transport failure **silent + data-losing** — worth fixing
regardless of the root cause:

1. **Optimistic apply.** `write_queue.enqueue_apply_for_clip`
   (`write_queue.py:108-110`) marks `review_items` applied in the same commit
   as the pending-op insert, **before** the PUT; `review.js::applyDraft`
   shows the success toast the instant `POST /apply` returns. → the draft
   "vanishes" and looks applied even when the PUT later fails.
2. **No failure surfacing.** The PUT runs later in the sync engine; its
   `ReadTimeout`/`failed` is never pushed to the UI, and the published panel
   only invalidates inside a *successful* `apply_changes` (`adapter.py:289`).

## Constraints & gotchas

- **CatDV 2-seat limit;** assume 1 is free. Connect takes a seat; disconnect
  (or graceful SIGTERM) frees it. `mode:online`=connected, `disconnected`=no
  seat, `offline`=tunnel/CatDV unreachable.
- **Don't write to prod CatDV without explicit user consent.** The auto-mode
  safety classifier blocks `apply-batch`/`connect`-driven writes; the user
  declined the writeback test this session. Note-appends are non-idempotent.
- **No local Docker.** Build via **Cloud Build**: `gcloud builds submit --tag
  europe-west3-docker.pkg.dev/catdav/catdv-annotator/app:$(git rev-parse HEAD)
  --timeout=1200s .` (API enabled; `data/` is gitignored so the upload is
  small). Deploy with the flags in `.github/workflows/deploy.yml` (image,
  `--service-account`, `--no-allow-unauthenticated`, `--min/max-instances=1`,
  `--no-cpu-throttling`, `--memory=1Gi`, `--env-vars-file=deploy/cloudrun.env.yaml`,
  `--set-secrets=...WG_PRIVATE_KEY=wg-private-key:latest`).
- **Cloud Run deploys REPLACE env** via `--env-vars-file`; an ad-hoc
  `gcloud run services update --update-env-vars` is wiped by the next image
  deploy (this bit us — put config in the yaml).
- **`gcloud` works** as the bare command even though `which gcloud` prints a
  garbled installer path.
- **Cloud Run logs:** `gcloud logging read 'resource.type="cloud_run_revision"
  AND resource.labels.service_name="catdv-annotator"' --project=catdav
  --freshness=15m --format='value(timestamp,textPayload)'` — shows onetun /
  boringtun lines.
- **GitHub Actions deploy only triggers on push to `main`;** this branch is
  ~97 ahead of main, so deploys are manual (above). CI/WIF still deferred.

## Done this session (context)

Root-caused the writeback to the tunnel (not payload/MTU); shipped the VPN
supervisor + status/toggle (ADR 0075, default off) as both the operational
mitigation and the investigation harness; rewrote ADR 0074 to record the
honest conclusion; built + deployed rev `00012-bsk`; verified acceptance
flow 1 live. The structural fix (dedicated cloud peer key) and the two code
defects above remain open.
