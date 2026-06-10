# Handover — Cloud WireGuard + AI-store-only media cache

**Date:** 2026-06-10
**Branch:** `cloud-run-deployment` (PR #42) — **pushed** (`origin` at `bf13de0`, was 39 commits behind, now fast-forwarded).
**Repo:** `futurememorylab/ai-archive`
**Specs:** `docs/specs/2026-06-10-cloud-media-cache-ai-store-design.md`
**Plan:** `docs/plans/2026-06-10-cloud-media-cache-ai-store.md`
**ADR:** `docs/adr/0069-cloud-media-cache-ai-store.md`
**Prior handover:** `docs/plans/2026-06-10-catdv-manual-connect-HANDOVER.md` (manual-connect feature, read for the chip/connection background).

## TL;DR

Two bodies of work shipped and went live on Cloud Run this session:

1. **Phase 3 WireGuard** — the onetun userspace tunnel is up; CatDV is reachable from Cloud Run. Live-verified connect → online → disconnect.
2. **AI-store-only media cache (PR #42, the headline feature)** — built TDD via subagent-driven development (8 tasks + reviews), full suite **1327 passed**, lint-imports 5/0. Deployed as revision `00003-xvh` with `MEDIA_CACHE=ai_store`. Live-verified: prefetch → GCS, playback → 307 signed URL, offline playback still 307.

**The "needs more testing" part is real** — several acceptance flows and edge cases were NOT walked on the live service. See "Still needs testing" below; that's the priority for the next session.

## Live deployment state

- **Service:** `catdv-annotator`, `europe-west3`, project **`catdav`** (NOTE: `catdav`, *not* `catdv` — a `--project=catdv` typo returns `PERMISSION_DENIED / CONSUMER_INVALID` and looks like an auth failure; it isn't. `peter.hora@gmail.com` is a real owner).
- **Running revision:** `catdv-annotator-00003-xvh`, image `europe-west3-docker.pkg.dev/catdav/catdv-annotator/app:bf13de0`. `min=max-instances=1`, `--no-cpu-throttling`, 1 GiB, private.
- **Env:** `MEDIA_CACHE=ai_store`, `CATDV_OFFLINE=false`, `CATDV_CONNECT_MODE=manual`. Secrets: `catdv-password`, `gemini-api-key`, `wg-private-key` (all `:latest`).
- **WireGuard:** reuses peter's **personal** pragafilm peer (`~/Documents/futurememorylab/sikl/secret/wg-pragafilm-hora.conf`). `WG_ENDPOINT=gw.pragafilm.cz:51820`, `WG_PEER_PUBKEY=ZM3v8…`, `WG_SOURCE_IP=192.168.3.5`. **Caveat:** one endpoint per peer key — keep peter's Mac pragafilm tunnel DOWN during cloud testing, or they fight.
- **Access:** `gcloud run services proxy catdv-annotator --region europe-west3` → `http://localhost:8080`. (A proxy may already be running on :8080 from this session.)

## CatDV seat discipline (unchanged, still critical)

Manual connect mode: boot takes NO seat. A seat is held only between a UI/API **Connect** and **Disconnect** (or idle auto-disconnect, `CATDV_IDLE_LOGOUT_S` default 900s). Always **Disconnect while the tunnel is up** so `DELETE /session` can run; otherwise the seat lingers server-side. Verify `health` returns `mode:disconnected` after. The whole connect/disconnect flow is API-drivable:
`POST /api/connection/connect`, `POST /api/connection/disconnect`, `GET /api/health`.

## What the feature does (architecture)

New `MEDIA_CACHE` setting (`local`|`ai_store`, replaces `PLAYBACK_SOURCE`) selects a `MediaCacheBackend` (`backend/app/services/media_cache.py`):
- **`local`** (dev default): `LocalProxyBackend` — download to local proxy cache, serve from disk, GCS signed URL as read fallback (today's dev behavior, unchanged).
- **`ai_store`** (cloud): `AiStoreBackend` — `ensure_cached` downloads the **full** proxy through the tunnel → `ai_store.ensure_uploaded` (GCS) → `finally` deletes the staging file + its `proxy_cache` row. `locate` → `ai_store.status` (DB) → GCS V4 signed URL → 307. **No CatDV in the read path** (offline-safe).

Call sites routed through the backend: the prefetcher (`media_prefetcher.py` → `ensure_cached`), `stream_media` (`routes/media.py` → `locate`), and studio upload ingest (`routes/studio.py` → `ensure_uploaded` in ai_store mode, additive to the local write). Wired on `LiveCtx.media_cache_backend` (`context.py`). The old orphaned `LiveCtx.media_locator` property was removed.

## Verified this session (live, via proxy)

- ✅ Boot `disconnected` (no seat).
- ✅ Connect → `online` (real CatDV login through the tunnel).
- ✅ **Prefetch clip 888700** → `gs://catdav-proxies/clips/888700.mov` (277 MiB) created; queue row `done`. (Full download staged on RAM-backed `/data`, no OOM at 277 MiB / 1 GiB.)
- ✅ **Playback** `GET /api/media/888700` → **307** to a `storage.googleapis.com` signed URL (GCS, not local).
- ✅ **Offline playback** after Disconnect (CatDV offline) → still **307** (offline invariant holds).
- ✅ Disconnect → seat freed (`mode:disconnected`).

## Still needs testing (PRIORITY for next session)

1. **Flow 3 — actual instance restart.** Proven by mechanism only (GCS object durable + offline playback works without local). Force a restart (`gcloud run services update … --update-env-vars FORCE=1` or redeploy the same image) and confirm `GET /api/media/888700` still 307 with the local disk wiped and **without** re-prefetching. This is THE durability proof the feature exists for.
2. **Flow 4 — studio upload → GCS, live.** Only unit/integration-tested, not walked on the live service. Upload a clip via the studio UI in the browser (`http://localhost:8080`), then verify an `ai_store_files` row / `gs://catdav-proxies/clips/<uploaded-id>.*` object appears AND the uploaded clip plays via 307. Also test the **offline-upload edge** (ADR 0069): upload while disconnected → GCS push is silently skipped (local copy only) — confirm that's the behavior and decide whether it needs a user-facing signal.
3. **Large-proxy / OOM headroom.** 277 MiB worked. Cloud prefetch downloads the FULL proxy to RAM-backed `/data` before upload. Find the biggest proxies in the catalog and prefetch one — confirm no OOM at 1 GiB, or bump `--memory`. Note: full-download prefetch is slow (≈60–90s for 277 MiB over the tunnel); a multi-GB master proxy could be a problem.
4. **Idle auto-disconnect** on the live service (shorten `CATDV_IDLE_LOGOUT_S`, confirm the seat frees itself).
5. **Browser UI flows** end-to-end on the deployed service: clip list thumbnails (cold instance → 404 placeholders until Connect+prefetch), the connection chip Connect/Disconnect, annotate, the player actually playing the 307'd GCS bytes (range requests hitting GCS directly).
6. **`MEDIA_CACHE=local` dev regression** — quick sanity on the dev Mac that local-mode playback/prefetch still serves 206 from disk (test-covered, but eyeball it).

## Remaining work (non-testing)

1. **CI/CD** — WIF + `github-deployer` SA + GitHub repo secrets `GCP_WIF_PROVIDER` / `GCP_DEPLOYER_SA` (deploy/README.md step 5). The `wg-private-key` secret now exists, so `.github/workflows/deploy.yml`'s `--set-secrets` is unblocked. Until this lands, deploys are manual: `gcloud builds submit --tag …/app:<sha> --region=europe-west3 .` then `gcloud run deploy … --image=…:<sha> --env-vars-file=deploy/cloudrun.env.yaml --set-secrets=…`.
2. **Retire the orphan connection pill** (from the manual-connect handover — still not done).
3. **Dedicated least-privilege WG cloud peer** — replace the reused personal key with a dedicated peer keyed to `AllowedIPs=192.168.1.41/32` (deploy/README.md Phase 3). Future hardening.
4. **GCS proxy lifecycle/eviction** — out of scope in ADR 0069 (sha256 dedup only). If `gs://catdav-proxies` grows unbounded, add a bucket lifecycle rule.

## Manual deploy cheatsheet (until CI/CD)

```bash
# build from HEAD
SHA=$(git rev-parse --short HEAD)
gcloud builds submit --tag europe-west3-docker.pkg.dev/catdav/catdv-annotator/app:$SHA \
  --project=catdav --region=europe-west3 .
# deploy
gcloud run deploy catdv-annotator --image=europe-west3-docker.pkg.dev/catdav/catdv-annotator/app:$SHA \
  --region=europe-west3 --project=catdav \
  --service-account=catdv-annotator@catdav.iam.gserviceaccount.com \
  --no-allow-unauthenticated --min-instances=1 --max-instances=1 --no-cpu-throttling \
  --memory=1Gi --cpu=1 --env-vars-file=deploy/cloudrun.env.yaml \
  --set-secrets="CATDV_PASSWORD=catdv-password:latest,GEMINI_API_KEY=gemini-api-key:latest,WG_PRIVATE_KEY=wg-private-key:latest"
```

## Continuation prompt for next session

> We're testing PR #42 (`cloud-run-deployment`, pushed, deployed as rev `00003-xvh` with `MEDIA_CACHE=ai_store`). Read `docs/plans/2026-06-10-cloud-media-cache-HANDOVER.md` first. The AI-store-only media cache is live and the core flows are verified; continue the "Still needs testing" list — start with (1) the actual instance-restart durability test and (2) live studio-upload→GCS. Mind the single CatDV seat: Connect/Disconnect via the API, always Disconnect while the tunnel is up. Project is `catdav` (not `catdv`). Run Python via `.venv/bin/python`; deploy manually via the cheatsheet (CI/CD still deferred).
