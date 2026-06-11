# Durable GCS-backed thumbnail cache — design

**Date:** 2026-06-11
**Status:** Draft
**Branch:** `cloud-run-deployment` (PR #42)
**Related:** ADR 0069 (cloud media cache → ai_store), ADR 0070 (content-aware GCS
upload), ADR 0065 (thumbnail service skips network without clip cache).

## Problem

In the Cloud Run deployment, clip-list thumbnails render as broken images
whenever the service is `disconnected` (the manual-connect default, no CatDV seat
held) and after every instance restart.

Verified live on rev `00005-g25` (2026-06-11):

| Request | Connection | Result |
|---|---|---|
| thumb 888700 | online | `200 image/jpeg` (lazy fetch from CatDV) |
| thumb 888700 again | online | `200` (now a `/data` cache hit) |
| thumb 888700 | disconnected | `200` (still on `/data` — survives *within* instance) |
| thumb 888733 (never fetched) | disconnected | `404` (can't fetch, not cached) |

**Root cause:** thumbnails have no durable or offline-safe cache. Unlike proxies
— which ADR 0069 routed through GCS (`MEDIA_CACHE=ai_store`) so they survive
restarts and serve while CatDV is offline — thumbnails are:

- fetched **lazily from CatDV** per `<img>` request
  (`routes/media.py::stream_thumbnail` → `ThumbnailService.get_or_fetch`),
  gated by `is_online()`, and
- cached **only** on the instance's ephemeral `/data` tmpfs (Cloud Run
  `volumes: None` — wiped on every cold start / restart / scale event).

No thumb→GCS persistence exists, and `media_prefetcher` only handles proxies, so
even Connect+prefetch won't populate thumbs. A thumb therefore renders only if
**both**: the instance is currently Connected **and** that exact thumb was
already pulled into this instance's RAM since its last restart.

**Uploaded-clip posters have the same bug**: they are pre-stored on `/data` at
ingest and served from there (offline, via the core ctx), but `/data` is
ephemeral so they 404 after restart too — and the upload pushes the *proxy* to
GCS but never the poster.

## Goals

- A thumbnail (CatDV clip **and** uploaded-clip poster) renders offline and after
  an instance restart **as long as it was ever fetched once** while connected —
  the same guarantee proxies got from ADR 0069.
- Zero behavior change in `MEDIA_CACHE=local` (dev): no GCS, persistent disk,
  byte-for-byte today's behavior.
- Reuse the existing cache-layer architecture (a service with
  `get_or_fetch`-style semantics + an `is_online` gate) rather than a new
  god-path. Per CLAUDE.md "add a new cache in the same shape".

## Non-goals

- Thumbnail prefetch / batch warming (lazy-on-request is sufficient; YAGNI).
- A DB index for thumbnails (GCS is the index; see Alternatives).
- GCS bucket lifecycle / eviction of orphan thumbs (same out-of-scope note as
  ADR 0069 for proxies).
- Changing the `/thumb` serving content type or the list-page `<img>` markup.

## Architecture

A new thin **`ThumbnailStore`** durable layer (GCS-backed), injected into the
existing `ThumbnailService`. `/data` remains a **hot cache in front of GCS**;
GCS is the durable source of truth. This is the "new cache in the same shape"
pattern, mirroring how `media_cache.py` injects the proxy `ai_store`.

### Components

- **`ThumbnailStore` protocol** (new):
  - `async get(clip_id: int, dest: Path) -> bool` — download the JPEG from GCS to
    `dest`; return `False` on miss (404) or transient failure. Validates
    `dest` is non-empty after write.
  - `async put(clip_id: int, src: Path) -> None` — upload `src`,
    **unconditional overwrite**. JPEGs are tiny (~4 KB), so overwriting on every
    write kills the ADR-0070 stale-blob / clip-id-reuse risk at write time — no
    md5 comparison needed.
- **`GcsThumbnailStore`** (new impl): blobs at
  `gs://<bucket>/thumbs/{clip_id}.jpg`, reusing the existing `catdv-proxies`
  bucket with a new `thumbs/` prefix. Backed by two new methods on `GcsService`
  (`download_thumb`, `upload_thumb`). All blocking SDK calls wrapped in
  `asyncio.to_thread`.
- **`ThumbnailService`** gains an optional constructor arg
  `durable_store: ThumbnailStore | None = None`. `None` (local/dev mode) → today's
  behavior verbatim.

### No DB index — rationale

GCS itself is the index (a blob either exists or does not). This deliberately
avoids the DB-row-vs-orphan-blob drift that bit proxies (ADR 0070). The cost is
one GCS round-trip per *cold* thumb; negligible for ~4 KB files, and `/data`
absorbs every subsequent hit. Offline-safety does **not** require a no-network
`status()` here (as it did for proxies) because the durable store *is* GCS, and
GCS is the network that stays up when the CatDV tunnel is down.

## Data flow

### Read — `ThumbnailService.get_or_fetch(clip_id)` (CatDV clips)

1. `/data` hit (`{clip_id}.jpg` exists, size > 0) → return it. *(unchanged)*
2. `/data` miss → **`durable_store.get()` (GCS)** → on success, file is now on
   `/data`, return it. **This step is NOT gated by CatDV `is_online()`** — GCS is
   a separate network, usually up even when the tunnel is down. *This is the
   offline-safety + restart-durability win.*
3. GCS miss **+ CatDV online** → fetch from CatDV (existing
   `download_thumbnail` / image-poster build) → save `/data` → **`durable_store.put()`
   (GCS)** → return it.
4. GCS miss **+ CatDV offline** → `None` → `404`. *(unchanged terminal)*

When `durable_store is None` (local mode), steps 2 and the `put` in step 3 are
skipped entirely → identical to today.

### Read — uploaded-clip posters (`stream_thumbnail` uploaded branch)

1. `/data` hit (via `get_core_ctx`) → serve. *(unchanged — keeps fs-mode posters
   working with no live wiring.)*
2. `/data` miss → if a `durable_store` is available, **`durable_store.get()`
   (GCS)** → serve. No CatDV, ever (unchanged invariant), now restart-durable.
3. `/data` miss + no durable store → `404`. *(unchanged terminal)*

**Context wiring (resolves the CoreCtx/LiveCtx split):** the durable store lives
on `LiveCtx` (it holds `GcsService`); per ADR 0047 a network service must not be
bolted onto `CoreCtx`, which stays DB-first/offline. The uploaded branch keeps
its `get_core_ctx` `/data` fast-path (so fs-mode, where there is no `LiveCtx`
durable store, is unchanged), and on a `/data` miss defensively consults the
`LiveCtx` durable store when one is wired. This is sound because the only mode
with a GCS thumbnail store — cloud `ai_store` — always has `LiveCtx` present
(even while `disconnected`, manual mode keeps `LiveCtx` wired; `get_live_ctx`
does not 503 there, confirmed live: the `/thumb` endpoint returns 404 not 503
when disconnected). fs-mode has neither a `LiveCtx` store nor needs one.

### Write — uploaded-clip poster ingest (`routes/studio.py`)

At upload ingest, after the poster is written to `/data` (today's behavior), in
ai_store mode also `await durable_store.put(clip_id, poster_path)` — alongside
the existing proxy push.

## Wiring & mode gating

- **`context.py`**: build `GcsThumbnailStore` in the same `MEDIA_CACHE=ai_store`
  branch that builds the media cache backend (the `GcsService` + bucket are
  already in hand there). Inject into `ThumbnailService(durable_store=...)`.
  `local` mode passes `None`.
- **`routes/studio.py`**: poster `put` in ai_store mode, guarded so local mode is
  untouched.
- **Settings / env**: none new — reuse the `catdv-proxies` bucket already
  configured for proxies.

## Error handling & edge cases

- **GCS `get` failure** (transient / network) → treated as a miss; fall through to
  CatDV (if online) or 404. Never raised into the request. `log.debug`.
- **GCS `put` failure** → best-effort; never mask the thumbnail already being
  served. `log.warning` (mirrors the `AiStoreBackend` cleanup pattern).
- **Blocking SDK calls** wrapped in `asyncio.to_thread` (CLAUDE.md: no sync I/O
  inside `async def`; enforced by `test_no_sync_fs_in_async.py`).
- **`is_online()` gating**: only the CatDV download step is gated. GCS read/write
  is not — that is the whole point of the feature.
- **Empty / corrupt GCS download** → validate `dest` size > 0 after `get`; on
  zero/failure, unlink and treat as a miss.
- **clip-id reuse / stale blob** → neutralized by unconditional overwrite on
  `put` (no ADR-0070 stale-bytes replay).
- **404 `max-age=300`** (`_THUMB_MISS_CACHE`) unchanged — after a thumb first
  lands in GCS, a previously-cached 404 can linger ≤ 5 min in the browser;
  acceptable, not a regression.

## Testing (TDD)

- **`GcsThumbnailStore`** unit (fake bucket, mirroring existing `gcs` tests):
  `put` then `get` round-trips; `get` on absent blob → `False`; `put` overwrites
  an existing blob.
- **`ThumbnailService.get_or_fetch`**, one test per branch:
  - `/data` hit → durable store never consulted.
  - `/data` miss + **GCS hit while CatDV offline** → served from GCS, **no CatDV
    call** (the headline offline test).
  - `/data` miss + GCS miss + CatDV online → CatDV fetch, then `put` to GCS.
  - `/data` miss + GCS miss + CatDV offline → `None`.
  - `durable_store=None` → byte-for-byte today's behavior (local-mode regression
    guard).
- **Uploaded poster**: ingest pushes to GCS; read path `/data` miss → GCS `get` →
  200.
- **Route**: `stream_thumbnail` returns 200 after a simulated restart (wipe
  `/data`, GCS populated) for both a CatDV clip and an uploaded poster.
- Full suite + `lint-imports` green before deploy.

## Manual acceptance flows

Run against the deployed Cloud Run service via the gcloud proxy
(`http://localhost:8080`). Project `catdav`, rev with this change deployed.
Mind the single CatDV seat — Connect/Disconnect via the API, always Disconnect
while the tunnel is up.

1. **CatDV thumbnail survives disconnect (offline serve).**
   - Setup: fresh instance, `GET /api/health` → `mode:disconnected`. Pick a clip
     id from `/` whose thumb is *not* yet cached (cold instance → all are).
   - Actions: `POST /api/connection/connect`; load `/` in the browser so the
     thumb lazily fetches (or `curl .../thumb`); confirm `200 image/jpeg`. Then
     `POST /api/connection/disconnect`; `curl .../thumb?cb=1` again.
   - Expected: still `200 image/jpeg` while disconnected — served from GCS, not
     just `/data`. (Distinguish from in-instance `/data` cache via flow 2.)

2. **CatDV thumbnail survives restart (durability — the headline proof).**
   - Setup: a thumb fetched once (flow 1), confirmed in
     `gs://catdv-proxies/thumbs/<id>.jpg`.
   - Actions: force a new revision (`gcloud run services update … --update-env-vars
     FORCE=1` or redeploy the same image) so `/data` is wiped; do **not** Connect.
     `curl .../thumb`.
   - Expected: `200 image/jpeg` on the fresh, disconnected instance with empty
     `/data` — proves GCS durability, not RAM.

3. **Uploaded-clip poster survives restart.**
   - Setup: upload a clip via the studio UI; confirm its poster renders and a
     `gs://catdv-proxies/thumbs/<uploaded-id>.jpg` object appears.
   - Actions: force a restart (wipe `/data`); load the clip list while
     disconnected.
   - Expected: the uploaded clip's poster still renders (`200`) — no CatDV
     involved.

4. **Local-mode regression (dev Mac).**
   - Setup: `MEDIA_CACHE=local`, CatDV connected, no GCS thumb store wired.
   - Actions: load the clip list; thumbnails render.
   - Expected: unchanged behavior; no GCS calls in logs; `durable_store is None`.

5. **Cold-instance, never-fetched, offline (terminal 404 still correct).**
   - Setup: fresh instance, `mode:disconnected`, a clip whose thumb is in neither
     `/data` nor GCS.
   - Actions: `curl .../thumb`.
   - Expected: `404 "no thumbnail"` — the offline terminal still holds; the
     feature adds durability, it does not invent thumbnails offline.
