# 0065. Thumbnail service short-circuits when clip has no cached metadata

**Date:** 2026-06-09
**Status:** Accepted

## Context

Clicking the cache icon (`/cache`) felt slow even though the page handler
returns in ~13 ms server-side. The browser request for the HTML was fast;
what dragged was the 50 `<img src="/api/media/{cid}/thumb">` requests
that the inventory grid fires after the page paints.

Each thumb request runs through `ThumbnailService.get_or_fetch()`. When
the JPEG isn't already on disk, the service calls
`archive.get_clip(clip_id)` to find `posterID`, then downloads the
thumbnail from CatDV. For most pages this is fine — the listed clips are
in `clip_cache`, so `get_clip` returns instantly from the local cache.

`/cache` is the one page that surfaces clips with bytes on disk but *no*
`clip_cache` row — the orphan rows. For those, `get_clip` cannot find a
local row and falls through to `httpx`, which has a **60 s timeout per
request** (`catdv_client.py: timeout_secs=60.0`).

The `is_online_provider` gate already blocks the network when the
`ConnectionMonitor` reports offline. But the monitor only probes every
30 s and halts after a single failure (`connection_monitor.py:144-156`).
That leaves a 30 s window between "CatDV stops responding" and "next
probe times out and flips state" during which `_is_online()` still
returns `True`. Inside that window, each orphan thumb on `/cache` can
stall up to 60 s; with the browser's 6-per-origin parallelism the page
appears frozen for minutes.

## Alternatives

1. **Tighten the per-call timeout** on `archive.get_clip(...)` from
   inside the thumbnail service (e.g. 2 s `asyncio.wait_for`). Bounds
   the worst case but still wastes 50 timeouts × 2 s and doesn't address
   the root cause — orphans have no posterID we could ever discover.
2. **Speed up the connection monitor.** Drop the probe interval or do an
   immediate probe before each thumb fetch. Closes the blind-spot window
   but adds load to a 2-seat CatDV server precisely when it's struggling.
3. **Render orphan rows without a thumb URL at all** from the
   `/cache` row builder. Fixes only this one page; any future surface
   that lists clips outside `clip_cache` (a search-by-bytes view, an
   audit page) would have to re-implement the same suppression.
4. **Gate the network step on whether `clip_cache` has a row for this
   clip** (chosen). The thumbnail service already knows it needs the
   posterID from cached metadata; if the metadata isn't there, there's
   nothing it can do, online or not. Encoding that as a precondition in
   the service makes every consumer correct by default.

## Decision

Add an optional `metadata_cached_provider: Callable[[int],
bool | Awaitable[bool]]` parameter to `ThumbnailService`. When set and
returning `False` for a clip id, `get_or_fetch()` returns `None`
immediately — before touching `archive` or `catdv`.

Wired in `context.py` to consult `core.clip_cache_repo.get_row(...)`
against `archive.id`. The gate is only installed when `use_catdv=True`;
the FS adapter doesn't need it.

The gate sits *after* the on-disk cache-hit check and *after* the
`is_uploaded` short-circuit, so:
- Cached thumb JPEG → still served instantly (no DB lookup).
- Uploaded clips → still served from their pre-stored poster.
- Clip with `clip_cache` row, missing JPEG → still goes through the
  existing network fetch path.
- Clip without `clip_cache` row (orphans) → 404 fast, no network.

## Consequences

**Good.** `/cache` orphan rows render to 404 in milliseconds regardless
of `ConnectionMonitor` state. The 30 s probe blind spot stops mattering
for this surface. New surfaces that list clips outside `clip_cache` get
the same protection for free.

**Negligible cost.** One extra `clip_cache.get_row` SQLite lookup per
thumb miss. The lookup is indexed and bounded by the 50-row page; the
existing network path was orders of magnitude slower.

**Slight invariant change.** A clip whose `clip_cache` row is evicted
between page render and thumb request will now 404 instead of falling
through to a CatDV refetch. That's correct — a thumb without metadata is
meaningless — and the 5-minute browser 404 cache (`media.py:_THUMB_MISS_CACHE`)
keeps the symptom transient.

**Test coverage.** Two new tests in `tests/unit/test_thumbnail_service.py`:
`test_no_cached_metadata_skips_network` (gate suppresses both archive and
catdv calls) and `test_metadata_cached_proceeds_to_network` (gate must
not block the normal path).
