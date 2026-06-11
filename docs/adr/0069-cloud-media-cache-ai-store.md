# 0069. Cloud media cache: AI-store-only on GCP

**Date:** 2026-06-10
**Status:** Accepted

## Context

Phase 4 of the Cloud Run deployment shipped GCS *read* (signed-URL
playback) but left caching writing to the ephemeral local proxy cache, so
proxies died on every instance restart and the GCS read path was starved
(prefetch called `resolver.path_for_clip_id`, never `ai_store.ensure_uploaded`).

## Alternatives

- Keep local fallback (Phase-4 design): simplest, but proxies never
  persist on ephemeral Cloud Run disk.
- Write-through (local + GCS): durable, but keeps a useless local copy and
  doubles disk pressure on a 1 GiB instance.
- AI-store-only on cloud (chosen): one `MediaCacheBackend` boundary,
  selected by `MEDIA_CACHE`.

## Decision

Introduce `MediaCacheBackend` (`ensure_cached`/`locate`) with
`LocalProxyBackend` (dev) and `AiStoreBackend` (cloud). Cloud caching
downloads through the tunnel, uploads to GCS, deletes the staging file;
playback is a signed URL; the local proxy cache is unused. `PLAYBACK_SOURCE`
is folded into `MEDIA_CACHE` and removed. Studio uploads additionally push
to GCS in `ai_store` mode (additive to the local write, which remains a
transient within-instance copy; GCS is the durable copy).

## Consequences

+ Cached/uploaded clips survive instance restarts; CatDV hit once per clip.
+ `locate()` needs no CatDV, so playback works while the tunnel is down.
- Large proxies stage transiently on RAM-backed `/data`; one-at-a-time +
  delete-after-upload bounds peak usage to a single proxy. Bump memory if
  proxies exceed the headroom.
- In `ai_store` mode + offline (no live ctx), a studio upload's GCS push is
  silently skipped (local copy only); acceptable narrow edge.
