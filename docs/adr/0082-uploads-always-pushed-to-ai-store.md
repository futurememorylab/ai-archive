# 0082. Studio uploads are always pushed to the AI store, not only in cloud mode

**Date:** 2026-06-15
**Status:** Accepted (supersedes the upload-gating part of [0069](./0069-cloud-media-cache-ai-store.md))
**Lifespan:** Invariant

## Context

ADR 0069 had the studio upload route push a clip to the AI store (GCS) only
when `media_cache == "ai_store"` (cloud mode), reasoning that local dev runs
on durable disk so the local file + `proxy_cache` row are home enough.

In practice the local `proxy_cache` row is **not** a durable home. A DB reset
or an LRU eviction drops the row while the on-disk file under
`data_dir/cache/uploads/<clip_id>.<ext>` remains — orphaning the bytes. The
annotator then can't resolve them: `proxy_resolver.path_for_clip_id` raises
`ProxyNotFound` (no row), `ai_store.status()` is also empty (never pushed in
local mode), and the run fails with *"clip … is not locally cached and not in
AI store"* even though the file is sitting on disk. Observed live: upload files
`1000000001`/`1000000002` existed on disk with no matching `proxy_cache` row.

GCS is reachable in local dev even when CatDV is offline (the common
"CatDV offline, GCS online" state — see the cache-layers section of
`CLAUDE.md`), so an AI-store push at upload time is feasible locally.

## Alternatives

- **Keep 0069's gating (status quo):** local uploads stay fragile; any DB
  reset/eviction silently orphans them. Rejected — this is the reported bug.
- **Self-healing resolver only:** on a `proxy_cache` row miss for an uploaded
  id, re-resolve from the deterministic on-disk path. Fixes local-disk
  orphans but gives no durability if `/data` itself is lost, and doesn't make
  the clip available to other instances. Useful but insufficient alone.
- **Always push to the AI store (chosen):** every upload gets a durable GCS
  home that the annotator's `status()` fast-path resolves regardless of local
  cache state or CatDV connectivity.

## Decision

The `/api/studio/uploads` route pushes the uploaded clip to
`live.ai_store.ensure_uploaded(("uploaded", <clip_id>), dest, mime)` whenever a
live AI store is available — independent of `media_cache`. The push is
**best-effort**: a transient GCS error is logged and swallowed, never failing
the upload (offline-graceful, per `CLAUDE.md`). The local file + `proxy_cache`
row remain as the fast local-playback path and as the fallback the annotator
re-pushes from on its first run.

The poster/thumbnail durable push is unchanged (still `ai_store`-mode only);
this ADR covers the clip media only.

## Consequences

+ Uploaded clips survive a DB reset / LRU eviction / `/data` loss and are
  annotatable even when CatDV is offline — the reported failure is fixed for
  all new uploads.
+ Annotation no longer depends on the fragile local `proxy_cache` row for
  uploaded clips.
- Every local upload now incurs a GCS write (storage + bandwidth). Accepted:
  the operator explicitly wants uploads durable in the AI store.
- Uploads created **before** this change remain orphaned (file on disk, no row,
  not in GCS); they need a one-time re-upload (or a backfill) to become
  annotatable.
