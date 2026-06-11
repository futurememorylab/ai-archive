# Cloud media cache: AI-store-only on GCP

**Date:** 2026-06-10
**Status:** Proposed
**Supersedes (partially):** Phase 4 of `docs/specs/2026-06-09-cloud-run-deployment-design.md`
(the playback-source preference). Builds on ADR 0047 (CoreCtx/LiveCtx) and
the three-cache-layer model in `CLAUDE.md`.

## Context

PR #42 shipped the **read** side of GCS playback (Phase 4): with
`PLAYBACK_SOURCE=gcs`, `MediaLocator` tries the AI store first and
`stream_media` returns a 307 redirect to a signed URL. But the
**write** side was never built:

- `MediaPrefetcher.tick_once()` caches a clip by calling
  `resolver.path_for_clip_id()` (`media_prefetcher.py:109`) — the local
  `ProxyResolver`, which downloads to the **ephemeral disk**. It never
  calls `ai_store.ensure_uploaded`.
- `ensure_uploaded` is called in exactly one place — `annotator.py:405`,
  as a side effect of running annotation, not as a caching action.
- Studio uploads are seeded into the **local proxy cache** at ingest and
  served from local disk via the `is_uploaded` branch
  (`media.py:67-76`).

Consequences on Cloud Run (`min=max-instances=1`, ephemeral RAM-backed
`/data`):

1. Prefetched and uploaded proxies die on every instance restart.
2. The GCS read path is almost never exercised — `_from_gcs` misses
   ("ai store: not uploaded") and the locator falls back to a local 206
   stream (observed live for clip 888899).
3. Each cold instance re-pays CatDV/tunnel bandwidth instead of caching
   once durably.

Spec line 307 of the original deployment design already stated the
intent — *"In cloud mode prefetch routes through the tunnel and
`ai_store.ensure_uploaded`, after which playback hits the GCS path"* —
but it was not implemented.

## Goal

On GCP, use **only the AI store (GCS)** for proxy media: caching writes
go straight to the AI store, playback reads from the AI store, and the
local ephemeral proxy cache is not used. Dev (`local`) behavior is
unchanged.

This is a deliberate tightening of the original Phase-4 design, which
kept the local cache as a playback fallback. The driver is correctness
on ephemeral storage, not just preference ordering.

## Non-goals

- **GCS proxy eviction / TTL.** Out of scope. Rely on the existing
  sha256 dedup (`adapter.py:54-57`); a GCS bucket lifecycle rule can be
  added later. `ai_store_files.expires_at` stays `None`.
- **Thumbnails.** Unchanged — they stay CatDV-fetched with an ephemeral
  disk cache (original spec's stated non-goal). Only proxy *media*
  moves to GCS-only.
- **Dev `local` mode.** No behavior change; it remains the default.
- **Parallel prefetch.** The prefetcher stays one-at-a-time by
  construction (`media_prefetcher.py:10-13`).

## Design

### The boundary — `MediaCacheBackend`

A new protocol becomes the single authority for "where does this clip's
proxy media live, and how do I populate it":

```python
class MediaCacheBackend(Protocol):
    async def ensure_cached(self, clip_id: int) -> None:
        """Make the clip's proxy available in this backend's store.
        Idempotent: a no-op if already present. Needs CatDV (the tunnel)
        on a miss. Raises on transient failure (caller retries)."""

    async def locate(self, clip_id: int) -> LocalFile | RemoteUrl | None:
        """Where to serve playback bytes from, or None on cache miss.
        MUST NOT require CatDV — depends only on the store's own index
        and (for GCS) URL signing."""
```

`LocalFile` / `RemoteUrl` / `MediaNotAvailable` are reused from
`media_locator.py`.

Two implementations, selected by a new setting:

```python
media_cache: Literal["local", "ai_store"] = "local"   # env MEDIA_CACHE
```

**`LocalProxyBackend`** (dev default, `MEDIA_CACHE=local`):
- `ensure_cached(clip_id)` → `resolver.path_for_clip_id(clip_id)`
  (today's local download into `proxy_cache`).
- `locate(clip_id)` → today's `MediaLocator` logic: local proxy first,
  then `ai_store.status` → signed URL fallback. This preserves the
  current dev behavior (including "clips already in GCS play even when
  the local cache is cold").

**`AiStoreBackend`** (cloud, `MEDIA_CACHE=ai_store`):
- `ensure_cached(clip_id)`:
  1. `ref = await ai_store.status(("catdv", str(clip_id)))` — if present,
     return immediately (no tunnel hit; the `CLAUDE.md` status-first
     fast-path).
  2. On miss: `path = await rest_resolver.path_for_clip_id(clip_id)` —
     download the proxy through the tunnel into a **temp staging file**
     (reuses the existing CatDV download + dedup).
  3. `mime = mimetypes.guess_type(path)[0] or "video/quicktime"`.
  4. `await ai_store.ensure_uploaded(("catdv", str(clip_id)), path, mime)`.
  5. In a `finally`: delete the local staging file **and** its
     `proxy_cache` row, so peak disk is a single proxy and nothing
     accumulates on the ephemeral filesystem.
- `locate(clip_id)` → `ai_store.status` → `RemoteUrl(signed_url)` on hit,
  else `None`. **GCS only; never consults local disk.**

The staging download reuses `RestProxyResolver` precisely because
`ensure_uploaded` requires a full file on disk (`adapter.py:52,59` read
the path twice — sha256 + upload); there is no stream-from-URL path. The
one-at-a-time prefetcher plus delete-after-upload keeps peak `/data`
usage to one proxy.

### Call sites collapse onto the backend

1. **Prefetcher** (`media_prefetcher.py`): depends on
   `backend.ensure_cached(clip_id)` instead of the raw `resolver`.
   Construction in `context.py` injects the selected backend.
2. **`stream_media`** (`media.py`): both the CatDV branch and the
   `is_uploaded` branch call `backend.locate(clip_id)`:
   - `LocalFile` → existing range/FileResponse serving, unchanged.
   - `RemoteUrl` → `RedirectResponse(url, 307)`.
   - `None` → 404 placeholder (today's `ProxyNotFound`/`MediaNotAvailable`
     UX: placeholder + user-initiated prefetch).
   The hardcoded local `is_uploaded` block (`media.py:67-76`) is removed;
   uploaded clips resolve through the same `locate`.
3. **Studio upload ingest:** in `ai_store` mode, upload the proxy to the
   AI store (`ensure_uploaded`) instead of seeding the local proxy cache,
   so uploaded clips are durable and play via the same GCS path.

### Settings / config

- Add `media_cache: Literal["local","ai_store"] = "local"` to
  `settings.py`.
- Remove `playback_source` and its read at `context.py:348` — it is
  folded into `media_cache` (one knob, not two). The `LocalProxyBackend`
  keeps the local-first + GCS-fallback read order internally; the
  `AiStoreBackend` is GCS-only.
- `deploy/cloudrun.env.yaml`: replace `PLAYBACK_SOURCE: "gcs"` with
  `MEDIA_CACHE: "ai_store"`.

### Offline contract (invariant)

`locate()` must never require CatDV. `AiStoreBackend.locate` uses
`ai_store.status` (a DB lookup) + GCS URL signing (ADC `signBlob`),
neither of which needs the tunnel. Therefore already-uploaded and
already-cached clips stay playable when CatDV / the tunnel is down —
strictly better than today, because GCS playback is independent of the
seat-limited CatDV server. Only `ensure_cached` needs the tunnel.

Implication for the implementation plan: the read path
(`locate` → `stream_media`) must be reachable **without** `LiveCtx`'s
CatDV-liveness gate (which returns 503 offline). The AI-store read
backend depends only on `ai_store` + `gcs`; its exact placement relative
to `CoreCtx` / `LiveCtx` is a plan-level decision, but this invariant is
fixed: media playback of cached clips survives a CatDV outage.

### Error handling

- `ensure_cached` with CatDV or GCS unreachable → raises; the prefetch
  queue marks the row retryable (today's behavior). Narrow with
  `is_provider_not_found` where absence must be inferred; otherwise treat
  as transient.
- The staging temp file is deleted in a `finally` even on upload failure
  — no disk leak.
- `locate` miss → `None` → 404 placeholder. User-facing error strings go
  through `services/errors.py::humanise`.
- Catch `Exception`, not `BaseException` (per ADR 0042 / `CLAUDE.md`).

## Testing

TDD — failing test first for each unit.

**Unit**
- `AiStoreBackend.ensure_cached`: status-hit → no download, no upload
  (dedup fast-path); status-miss → downloads, uploads, deletes the temp
  file and its `proxy_cache` row; upload failure → temp still deleted,
  exception propagates.
- `AiStoreBackend.locate`: status-hit → `RemoteUrl` with a signed URL;
  status-miss → `None`; never calls CatDV (assert the CatDV client is
  untouched).
- `LocalProxyBackend`: `ensure_cached` downloads locally; `locate`
  preserves local-first + GCS-fallback (regression guard for dev).
- Backend selection from `MEDIA_CACHE` (`local` → `LocalProxyBackend`,
  `ai_store` → `AiStoreBackend`).

**Integration**
- `stream_media` returns 307 (`Location` → `storage.googleapis.com`) for
  an ai-store-backed clip; 404 placeholder on a both-miss.
- Prefetch of a CatDV clip in `ai_store` mode creates an `ai_store_files`
  row and leaves the local proxy cache directory empty.
- N+1 / query-count guards unaffected (no per-clip fan-out added).

## Manual acceptance flows

Run against the deployed Cloud Run service via
`gcloud run services proxy catdv-annotator --region europe-west3`
(`MEDIA_CACHE=ai_store`, WireGuard up, Connected).

1. **Cache writes go to GCS, not local disk.** Open a CatDV clip not yet
   cached → player shows the placeholder. Click prefetch (or the cache
   action). Wait for the queue row to complete. Verify: a new row exists
   in `ai_store_files` for that clip (or `gsutil ls
   gs://catdv-proxies/clips/<id>.*` shows the object), **and** the
   container's local proxy cache dir holds no file for that clip.

2. **Playback replays from GCS (307).** Press play on the now-cached
   clip. In devtools Network, the `/api/media/<id>` request returns
   **307** with a `Location` pointing at `storage.googleapis.com`, and
   the subsequent range requests hit GCS directly (not the app origin).

3. **Survives an instance restart.** Redeploy or restart the revision
   (ephemeral disk is wiped). Without re-prefetching, press play on the
   same clip → still **307** to GCS. (Pre-change, this would 404 →
   placeholder because the local proxy was lost.)

4. **Studio upload lands in GCS.** Upload a clip via the studio. Verify
   an `ai_store_files` row / GCS object appears for it, and playing it
   returns **307** to GCS — no local-disk dependency.

5. **Offline (CatDV down) playback still works.** Disconnect (or drop the
   tunnel). Press play on an already-cached clip → still **307** to GCS
   (read path needs no CatDV). Then attempt to prefetch a *new* clip →
   the queue row fails gracefully with an actionable, humanised error
   naming the AI-store/CatDV layer; the app stays navigable.

6. **Dev mode unchanged.** On the dev Mac with `MEDIA_CACHE=local`
   (default), prefetch + playback of a locally-cached clip still serves
   bytes from local disk (a 206 range stream, no redirect), exactly as
   before.

## Rollout

1. Land the backend + tests behind `MEDIA_CACHE` (default `local`, so
   dev and any non-flag deploy are untouched).
2. Flip `deploy/cloudrun.env.yaml` to `MEDIA_CACHE: "ai_store"`
   (replacing `PLAYBACK_SOURCE`), rebuild, redeploy.
3. Walk the manual acceptance flows on the deployed service.
4. ADR documenting the deviation from Phase-4 (local fallback dropped on
   cloud) and the `PLAYBACK_SOURCE` → `MEDIA_CACHE` rename.
