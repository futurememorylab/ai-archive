# Cache queue progress — design spec

**Date:** 2026-06-22
**Status:** Draft
**Issue:** [#78](https://github.com/futurememorylab/ai-archive/issues/78) — cache queue progress bar
**Surface:** Cache button on the clip detail page (`cacheActions.js`) + the cache queue page (`_cache_queue_active.html`); download path (`media_prefetcher.py` → `proxy_resolver.py` → `catdv_client.py`) + the `prefetch_queue` table.

## Goal

Caching a video currently gives no feedback on download progress. The user
wants to know, at a glance, how far along a download is:

- On the **clip detail page**, the cache button should show the percentage in
  parentheses while that clip downloads — e.g. `Caching… (45%)`.
- On the **cache queue page**, the currently-downloading row should show its
  percentage and its downloaded size — e.g. `45%   12.3 MB`.

The core use case is simply *information*: communicate the current caching
state and progress. Treatment is **text-only** (no progress bar), and updates
ride the **existing polling** both surfaces already do — no new transport.

## What already exists (reused, not rebuilt)

- **`prefetch_queue` table** (`migrations/0007_prefetch_queue.sql`) already has a
  `bytes_downloaded INTEGER NOT NULL DEFAULT 0` column — but it is **never
  populated** (`mark_done()` hard-codes `bytes_downloaded=0`). There is no
  `bytes_total` column.
- **`MediaPrefetcher`** (`services/media_prefetcher.py`) is the one-at-a-time
  worker: `tick_once()` claims the oldest `queued` row → `downloading`, calls
  `backend.ensure_cached(clip_id)`, then `mark_done()` / `mark_error()`.
- **Download path**: `ProxyResolver.path_for_clip_id()`
  (`services/proxy_resolver.py`) builds a downloader closure and awaits it;
  `catdv_client.download_proxy()` / `download_original()` stream bytes to disk
  via `_stream_to_file()`. The total size is **already parsed** by
  `_content_total_bytes()` from `Content-Length` / `Content-Range`. There is a
  resume loop that restarts stalled/partial downloads.
- **Polling, both surfaces already do it**:
  - Clip button — `cacheActions.js` polls `GET /api/cache/prefetch/queue` every
    1.5 s, finds the row by `provider_clip_id`, reads `status`.
  - Queue page — `_cache_queue_active.html` is server-rendered every 2 s via
    HTMX (`hx-trigger="every 2s"` → `GET /ui/cache/queue`).
- **Write queue + repo pattern** — DB writes route through
  `prefetch_queue_repo` on the write queue (no direct sync I/O in async).

No new endpoint, no SSE, no EventBus topic. The progress fields land on the
`prefetch_queue` row that both polls already fetch; the frontend just renders
two fields it now receives.

## Design

### 1. Data model

New migration (`migrations/0024_prefetch_progress.sql`— next free number after
`0023_clip_versions.sql`):

```sql
ALTER TABLE prefetch_queue ADD COLUMN bytes_total INTEGER NOT NULL DEFAULT 0;
```

`bytes_downloaded` already exists and is reused. Semantics:

- `bytes_total = 0` means **unknown** (no `Content-Length`) → drives the
  indeterminate fallback (no percentage shown).
- `bytes_total > 0` → percentage = `round(100 * bytes_downloaded / bytes_total)`.

`_row_to_dict_with_name()` in `repositories/prefetch_queue.py` adds
`bytes_total` to the emitted dict, so both polls receive it automatically.

### 2. Progress callback plumbed down the existing download path

A progress callback is threaded through the **existing** path — this is
instrumentation, not a new fetch path, and does not bypass any cache layer:

```
MediaPrefetcher.tick_once()                          ← owns throttle + DB write
  └─ ProxyResolver.path_for_clip_id(..., progress_cb)        ← forwards; default None
       └─ catdv_client.download_proxy/original(..., progress_cb)
            └─ _stream_to_file(...)  → progress_cb(bytes_on_disk, total) per chunk
```

- Signature: `progress_cb: Callable[[int, int], Awaitable[None]] | None`,
  defaulting to `None`. Every other caller of the resolver (playback, AI store
  ingest, etc.) is unaffected because they pass nothing.
- The callback reports **absolute bytes-on-disk**, not a delta — so the resume
  loop (which restarts a partial file and re-streams) stays correct. The
  reported value is `existing_on_disk + bytes_written_this_stream`.
- `total` is the value `_content_total_bytes()` already parses; passed to the
  callback on every call (`0` when unknown).

`_stream_to_file()` is shared by both `download_proxy` and `download_original`,
so adding the callback there covers video proxies and image originals in one
place.

### 3. Throttling (the one real risk)

`_stream_to_file()` fires per chunk — potentially thousands of times for a
multi-GB clip. A naïve per-chunk DB write would hammer the write queue.

The throttle lives in the callback that `MediaPrefetcher` supplies (not in
`catdv_client` — the client stays a dumb byte pump):

- Write at most **once per ~750 ms** of wall-clock, AND
- skip the write if neither `bytes_downloaded` nor `bytes_total` changed.

So a large download produces a few dozen row updates, not thousands. The write
goes through a new repo method:

```python
prefetch_queue_repo.update_progress(rid, bytes_downloaded, bytes_total)
# single UPDATE prefetch_queue SET bytes_downloaded=?, bytes_total=? WHERE id=?
```

`mark_done()` changes from hard-coded `bytes_downloaded=0` to
`bytes_downloaded=bytes_total` (the final size), so completed rows and the
Recent panel read sensibly rather than showing 0.

### 4. Frontend — render fields already in the poll payload

**Queue active panel** (`_cache_queue_active.html`, server-rendered every 2 s):
in the Size cell, when `status == 'downloading'`:
- if `bytes_total > 0`: render `45%   12.3 MB` (percentage + downloaded size via
  the existing `bytes_human` filter);
- else (unknown total): render just the downloaded size, no percentage.

Non-`downloading` rows are unchanged (done rows now show the real final size).

**Clip button** (`cacheActions.js`, polls every 1.5 s): the poller already
locates the row by `provider_clip_id` and reads `status`. Extend it: when
`status == 'downloading'` and `bytes_total > 0`, set
`busyLabel = "Caching… (" + pct + "%)"` (the label already drives the button
text); when total is unknown, keep the plain `Caching…`. No new DOM, no new
fetch.

### 5. Edge cases

- **Unknown total** (chunked transfer / missing `Content-Length`):
  `bytes_total` stays 0. Button shows plain `Caching…`; queue shows only the
  downloaded size. Graceful, no divide-by-zero, no `(NaN%)`.
- **Image originals** (`download_original`): same callback via the shared
  `_stream_to_file()`; small files, fall back to indeterminate if no length
  header.
- **Resume loop**: callback reports absolute bytes-on-disk, so a resumed
  download continues to climb from where it left off rather than resetting.
- **Restart / orphan requeue**: `requeue_orphans()` resets a crashed
  `downloading` row back to `queued`; both byte fields should reset to 0 on
  requeue so a restarted download starts its percentage from 0.
- **Throttle vs. completion**: the final state is set authoritatively by
  `mark_done()` (`bytes_downloaded = bytes_total`), independent of whether the
  last throttled tick fired — so the row never sticks at e.g. 98%.

## Components touched

| Unit | Change |
|---|---|
| `migrations/0024_prefetch_progress.sql` | new column `bytes_total` |
| `repositories/prefetch_queue.py` | `update_progress()`; `mark_done()` sets bytes; emit `bytes_total`; reset bytes on `requeue_orphans()` |
| `services/catdv_client.py` | `download_proxy`/`download_original`/`_stream_to_file` accept + invoke `progress_cb` |
| `services/proxy_resolver.py` | `path_for_clip_id` forwards optional `progress_cb` |
| `services/media_prefetcher.py` | builds throttled callback, passes it down, owns the DB write |
| `templates/pages/_cache_queue_active.html` | render `NN%   size` on downloading rows |
| `static/cacheActions.js` | set `busyLabel` to `Caching… (NN%)` |

## Testing (TDD)

1. **Repo** — `update_progress` writes both fields; `mark_done` sets
   `bytes_downloaded = bytes_total`; `requeue_orphans` zeroes both byte fields;
   `_row_to_dict_with_name` includes `bytes_total`.
2. **Download** — a fake stream of N chunks invokes `progress_cb` with
   monotonically increasing bytes and the correct total; with no
   `Content-Length`, total is reported as 0.
3. **Throttle** — N chunk callbacks (e.g. 1000) over a controlled clock produce
   ≤ M DB writes (proves no per-chunk write); a no-change tick produces no
   write.
4. **Render** — queue active panel shows `45%` + size when total known; shows
   size only (no `%`) when total is 0; done row shows final size.
5. **Resume correctness** — a callback sequence simulating resume (existing
   bytes + new stream) reports absolute bytes, never resets downward.

## Manual acceptance flows

1. **Percentage on the clip button.** Setup: a clip not yet cached whose proxy
   is several hundred MB, CatDV online. Actions: open the clip detail page,
   click **Cache**. Expected: the button label changes to `Caching…` and then
   shows a climbing `Caching… (NN%)` that increases roughly every 1.5 s until it
   reaches 100% and the button flips to the cached state.

2. **Progress on the queue page.** Setup: same as flow 1, with the cache queue
   page open in another tab. Actions: trigger the cache, watch the active queue
   row. Expected: the downloading row shows `NN%   <downloaded> MB` updating
   about every 2 s, the size climbs, and on completion the row moves to Recent
   showing the full final size (not `0 MB`).

3. **Graceful fallback when total is unknown.** Setup: a clip whose download
   response carries no `Content-Length` (chunked). Actions: cache it and watch
   both surfaces. Expected: the button shows plain `Caching…` (no `(NN%)`), the
   queue row shows only the downloaded size with no percentage, and the
   download still completes normally with no `NaN%`/`(undefined%)` artifacts.

4. **Existing behaviour intact.** Actions: with a clip already cached, load its
   detail page; play it. Expected: playback resolves the proxy from cache as
   before (the added optional `progress_cb` defaults to `None` and changes
   nothing for non-prefetch callers); the cache button shows the cached state.
