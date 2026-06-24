# 0072. Thumbnail poster cache + bounded download concurrency

**Date:** 2026-06-11
**Status:** Accepted
**Lifespan:** Feature

## Context

After the durable GCS-backed thumbnail cache shipped (ADR 0071), live testing
on Cloud Run rev `00006` showed thumbnails still 404 for many clips even when
the instance was online and connected to CatDV. The symptom was systematic, not
occasional: entire clip-list pages rendered placeholder-only thumbnails on first
load.

Root cause: the metadata gate introduced in ADR 0065 returns `None` for any
clip with no `clip_cache` row, on the assumption "no per-clip metadata ⇒
`posterID` unknowable ⇒ skip CatDV" — the gate was added to stop the `/cache`
page stalling 60 s per orphan when CatDV is slow. The problem is that listing
clips writes only `clip_list_cache`; a per-clip `clip_cache` row is written only
when a clip is individually opened via `get_clip`. So clips that have appeared
in the list but have never been opened — the common case for any newly synced
clip — have their thumbnails gated off entirely, even though the list payload
already carries the `posterID` needed to fetch them.

Confirmed by a clean reproduction: `GET /api/media/thumb/888709` → 404 →
`GET /api/clips/888709` (writes the `clip_cache` row) → `GET
/api/media/thumb/888709` → 200. The list payload does include `posterID` per
item.

## Alternatives

- **Write list data into `clip_cache`:** the list payload carries `posterID` so
  the gate would pass. Rejected: `get_clip` is cache-first and the list payload
  is a lighter projection — it lacks `thumbnailIDs`, detail fields, and other
  per-clip metadata. Seeding `clip_cache` from the list would cause `get_clip`
  to serve partial metadata forever and regress clip detail and annotation.

- **Relax the gate when the instance is online:** allow the metadata-gate miss
  to fall through to a live `get_clip` call. Rejected: this re-opens the ADR
  0065 concern. On the `/cache` page with many orphaned clips and a slow or
  seat-limited CatDV server, each live `get_clip` can stall, making the page
  unusable. The gate must be preserved for orphans.

- **Dedicated poster cache (chosen):** a lightweight
  `(provider_id, clip_id) → poster_id` table populated during `list_clips`.
  The thumbnail path consults it as a `get_clip`-free fallback on a
  metadata-gate miss. Clips that have never been listed — orphans — stay gated,
  preserving the `/cache` page protection from ADR 0065.

## Decision

Add a `poster_cache` table and a `PosterCacheRepo` with `upsert_many` /
`get_poster_id` operations. The CatDV adapter writes
`(clip_id, posterID)` for every listed item that carries a poster ID during the
live list write-through, additive to the existing `clip_list_cache` update and
with no change to `clip_cache`. The upsert is batched across all items in the
list response and is TTL-guarded via the same interval as the list refresh so it
does not run on every page view.

`ThumbnailService` gains a `poster_id_provider` callable. On a
metadata-gate miss it calls the provider, which reads the `poster_cache` row for
that clip. If a row is found, the service downloads the poster frame directly
from CatDV using only the poster ID — one targeted CatDV call, no `get_clip`
invocation, and no `clip_cache` row created. This keeps the two caches cleanly
separated: `clip_cache` remains the authoritative source of per-clip detail
metadata; `poster_cache` is a narrow, thumbnail-only index fed exclusively by
the list path.

A `_download_and_store` helper now wraps every CatDV thumbnail download in an
`asyncio.Semaphore` bounded to `download_concurrency=3`. A single page load
triggers one `<img>` request per visible clip card; without a cap these
arrive concurrently and can exhaust the single available CatDV session slot or
queue up enough in-flight connections to degrade CatDV responsiveness for all
other paths.

Finally, a single shared empty-thumbnail placeholder — a film-frame gradient
with a centred glyph, rendered via `.thumb--empty` / `.thumb-missing` CSS
classes — replaces the previous mix of broken-image icons and
`visibility: hidden` states that appeared across the clip list, clip-picker
modal, and studio clip cards.

Cross-reference: ADR 0065 (the metadata gate this ADR works around), ADR 0071
(durable GCS thumbnail cache this builds on), and the implementation plan
`docs/plans/2026-06-11-thumbnail-metadata-gate-fix.md`.

## Consequences

+ Clips that have been listed but never individually opened now show real
  thumbnails when the instance is online, without requiring a `get_clip` call.
+ `clip_cache` is not polluted by list-derived partial rows; `get_clip` still
  fetches and stores full per-clip detail on first open, as before.
+ The ADR 0065 orphan-protection gate is fully preserved: clips that have never
  appeared in a list have no `poster_cache` row and continue to be gated.
+ CatDV is shielded from thumbnail download stampedes by the semaphore
  concurrency cap.
+ Posterless clips and genuine cache misses render a clean, consistent
  placeholder across all surfaces instead of a broken-image icon.
- One extra small table is written on each live list refetch; the write is
  batched and TTL-guarded so the overhead is the same order as the
  `clip_list_cache` upsert it accompanies.
- The `poster_cache` can become stale if a clip's `posterID` changes upstream
  (e.g. a poster is reassigned in CatDV). This is acceptable: the row
  self-heals on the next live list refetch, and a stale `posterID` at worst
  fetches a wrong poster frame — never wrong playback media.
