# 0009. Media prefetch + cache UI wiring (PR 8)

- **Date:** 2026-05-20
- **Status:** Accepted
- **Lifespan:** Feature

1. Prefetch is a persistent SQLite queue (`prefetch_queue`), not in-memory. A
   long download must survive process restart. The same table powers the
   `/cache?tab=queue` UI panel.

2. Single-flight serialization lives in the worker, not in `RestProxyResolver`.
   The resolver remains request-driven; the prefetcher runs at most one
   `tick_once()` body at a time. On-demand `/api/media/{id}` requests do not
   queue behind it — the existing "file exists, skip download" check de-dups
   naturally once the file lands.

3. `RestProxyResolver` now records into `proxy_cache` after a successful
   download. Without this, `CacheInspector` reports `media-local: absent`
   even when the file is on disk. The prefetcher would have papered over
   this; we fix the underlying gap instead.

4. Cancellation is honored only for `queued` and `error` rows. A
   `downloading` row cannot be cancelled mid-stream — we do not want
   partial files that `curl -C -` would later treat as a resume target.
   `stop()` is still respected between rows.

5. Cache badges in the clips list are rendered server-side from a single
   bulk `CacheInspector.status_for_clips([keys])` lookup, not via per-row
   HTMX. The `/ui/cache-badge/{provider}/{clip_id}` route stays for
   post-evict refresh but is no longer the primary render path.

6. No new column on `proxy_cache`. The queue table's `status` is the queue's
   job. Once a file lands, `proxy_cache.record()` is called and the queue
   row goes to `done`. The two tables are joined on
   `(provider_id, provider_clip_id)` only at display time.
