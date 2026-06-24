# 0071. Durable GCS-backed thumbnail cache

**Date:** 2026-06-11
**Status:** Accepted
**Lifespan:** Invariant

## Context

Cloud Run thumbnails — both CatDV clip poster images and uploaded-clip
poster frames — rendered as broken images under two routine conditions:
(a) when the instance was `disconnected` from CatDV (the manual-connect
default introduced in ADR 0068), and (b) after any instance restart.
Verified live on Cloud Run rev `00005-g25`.

Root cause: thumbnails had no durable or offline-safe cache. Unlike
proxies, which ADR 0069 routed through GCS via `MEDIA_CACHE=ai_store`,
thumbnails were fetched lazily from CatDV on every `<img>` request
(gated by `is_online()`) and cached only on the instance's ephemeral
`/data` tmpfs — wiped on restart and unavailable when the CatDV tunnel
is down. No thumb-to-GCS persistence existed; `media_prefetcher` only
handled proxies. Uploaded-clip poster frames had the same bug: written
to `/data` at ingest time, never pushed to GCS.

## Alternatives

- **Keep the instance always-connected (auto-connect):** eliminates the
  disconnect case, but burns the single CatDV seat permanently and a
  restart still wipes all cached thumbnails. Rejected.
- **Persistent volume mount for `/data` (GCS FUSE / Cloud Run volume):**
  survives restarts, but a never-fetched thumb still requires a live
  CatDV connection (no offline-fetch win), and brings its own infra
  caveats (mount latency, consistency guarantees). Rejected.
- **Thumbnail prefetch job:** thumbs would be warmed proactively, but
  the cache is still ephemeral — lost on restart — and the job still
  requires a CatDV connection. Insufficient on its own.
- **GCS-backed durable store mirroring the proxy pattern (chosen):** a
  thumb renders correctly while offline and after any restart, provided
  it was ever fetched once. Same durable guarantee proxies get from ADR
  0069, at negligible cost given thumbnail sizes (~4 KB per JPEG).

## Decision

Give the thumbnail cache the same GCS treatment proxies received. A thin
`ThumbnailStore` interface (concrete impl `GcsThumbnailStore`) stores
blobs at `thumbs/{clip_id}.jpg` inside the existing `catdv-proxies`
bucket and is injected into `ThumbnailService`. The store is wired only
in `MEDIA_CACHE=ai_store` mode; in local/dev mode it is `None` and
`ThumbnailService` behaviour is unchanged.

`/data` acts as a hot in-process cache in front of GCS. On a `/data`
miss, `get_or_fetch` consults the durable GCS store **before** the
CatDV-`is_online()` gate. Critically, the GCS GET is **not** gated by
the CatDV `is_online()` closure, because GCS is a separate network path
from the CatDV WireGuard tunnel — this is precisely what delivers
offline-serve and restart-durability. Only when GCS also misses does the
code fall through to the live CatDV fetch; after a successful CatDV
fetch the new JPEG is pushed to GCS. Uploaded-clip poster frames are
pushed to GCS at ingest time.

No DB index is used (unlike proxies' `ai_store_files` table in ADR
0069). GCS itself is the index. This deliberately avoids the
DB-row-vs-orphan-blob drift that bit proxies and motivated ADR 0070.
`upload_thumb` overwrites unconditionally: JPEGs are tiny, so an
unconditional overwrite kills the stale-blob / clip-id-reuse risk at
write time with no MD5 comparison needed.

Cross-reference: spec `docs/specs/2026-06-11-cloud-thumbnail-cache-design.md`,
ADR 0069 (proxy GCS pattern), ADR 0070 (content-aware upload, the drift
problem this ADR sidesteps by skipping the DB index).

## Consequences

+ Thumbnails survive instance restarts and are served while CatDV is
  offline, matching the proxy cache guarantee from ADR 0069.
+ No schema migration and no DB/blob drift risk — GCS is the index.
+ Local/dev mode is byte-identical to before; the durable store is `None`
  and the code path is unchanged.
- One GCS round-trip per cold thumbnail miss that is not yet in GCS;
  negligible for ~4 KB files, and `/data` absorbs all subsequent hits
  within the instance lifetime.
- GCS thumbnail-blob lifecycle and eviction are out of scope, the same
  caveat flagged for proxies in ADR 0069.
- A prior HTTP 404 (thumb not yet in GCS) is cached `max-age=300` by
  the browser; after a thumb first lands in GCS a stale cached 404 can
  linger up to five minutes before the browser re-fetches.
