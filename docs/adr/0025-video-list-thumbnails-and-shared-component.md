# 0025. Unified video-list component + CatDV poster thumbnails

**Date:** 2026-05-25
**Status:** Accepted

## Context

The clips list (`/`) and the cache list (`/cache`) both show "a list of
videos" but were built as two independent tables (`.tbl` vs `.cache-tbl`),
so they looked unrelated and drifted. Neither showed thumbnails, and the
clips table's columns were misaligned because it rendered
`<tr class="row">` while a global `.row { display:flex }` helper turned
each row into a flex container.

## Alternatives

- **Thumbnails from ffmpeg frames of the cached proxy** — works offline but
  only for clips with a cached proxy (most rows would be placeholders).
- **Fully data-driven column spec for the shared list** — a flat
  `columns=[{value}]` config can't express the cache rows' layer-dot badge,
  hover action buttons, or per-row attributes without passing raw HTML
  through config, which defeats the uniformity it promises.
- **Shared CSS only, two table templates** — lowest risk but not "one
  component"; the two skeletons can drift again.

## Decision

- Thumbnails come from CatDV posters via the singular image renderer
  `GET /catdv/api/9/thumbnail/{id}` (the plural `/thumbnails/{id}` is JSON
  metadata — not the image), cached as plain `cache/thumbs/{clip_id}.jpg`
  files and served by `GET /api/media/{clip_id}/thumb` with a graceful
  404→placeholder fallback. The poster id is the clip's `posterID`
  (fallback: first `thumbnailIDs`), resolved from cached clip metadata.
- Both lists render through one `pages/_video_list.html` scaffold that owns
  the shared chrome (checkbox, cache badge, thumbnail+name cell, row
  height); each page injects only its trailing columns via small
  `head_cells` / `row_cells` partials.
- The cache list's exact-bytes (`comma B`) subline is dropped.
- The `.row` flex helper is scoped to `.row:not(tr)` to fix the alignment
  bug.

## Consequences

- One source of truth for list chrome; the cache list keeps its bespoke
  columns (workspace, byte totals, Re-fetch/Purge actions, orphan
  highlight) as ordinary markup in its `row_cells` partial.
- Thumbnails are tiny, regenerable sidecar files with no DB table and no
  eviction UI (out of scope). The first cold view of a list fetches posters
  per-cell (small, lazy-loaded, per-cell shimmer); subsequent views serve
  from the local cache.
- The prefetch *queue* table (`_cache_queue_table.html`) still uses the
  `.cache-tbl` styles — only the cache *inventory* table migrated — so the
  `.cache-tbl` rule family was retained while the dead `.tbl` family was
  removed.
