# Clips List Redesign — Poster + Notes Per Row — Spec

**Date:** 2026-05-22
**Scope:** Redesign the clips search/list page (`/`) from a dense 7-column table to a list of media rows that show each clip's poster image and a notes excerpt inline. Default page size drops from 50 to 20. Adds a new `/api/poster/{clip_id}` route with a server-side disk cache for CatDV poster JPEGs. No changes to the filter form, pager, bulk-select, or backing data pipeline.

## Background

The current clips list (`backend/app/templates/pages/_clips_tbody.html`) is a compact table with columns for select / cache / name / year / decade / duration / markers. Each row is text-only — there is an empty `<span class="thumb"></span>` placeholder where a thumbnail would go, but no image is ever rendered. To decide whether a clip is worth opening, the user has to click into `/clips/<id>` and look at the poster + notes there.

CatDV's clip JSON already carries the data we need for an inline preview:

- `posterID: int` — JPEG poster frame, fetched via `GET /catdv/api/9/clips/{clip_id}/poster`.
- `notes: str` — short notes field.
- `bigNotes: str` — longer notes field. In practice on this catalogue the two are usually identical strings.
- `thumbnailIDs: int[]` — multiple JPEG thumbnails per clip. **Not in scope** for this redesign.

A check against `clip_list_cache` (the stored response from `GET /catdv/api/9/clips?query=...&skip=...&take=...`) confirms `posterID`, `notes`, and `bigNotes` are all present in the list endpoint's payload. `thumbnailIDs` is **not** included in the list response — fetching it requires a per-clip detail call. That observation drives the "single poster per row" choice over a scrub-on-hover thumbnail strip: posters are free (zero extra CatDV calls); thumbnails would cost N detail fetches over the slow VPN.

## Goals

1. Each row in the clips list shows the clip's poster image (or a placeholder when none exists) and a two-line notes excerpt.
2. Long notes can be expanded inline (no navigation) by clicking a "More" affordance.
3. Default page size is 20 rows.
4. Poster JPEGs are cached to local disk on first fetch and served by a new in-process route; the browser caches them indefinitely via a versioned URL.
5. Filter form (search, Cache, Annotations), pager, bulk-select, cache badge, and the Actions split-button continue to work unchanged.
6. Zero new CatDV API calls during list rendering. Posters fetch lazily when their `<img>` enters the viewport.

## Non-goals

- Thumbnails / scrub-on-hover. Posters only.
- Replacing the existing list cache layer or adding a new one for clip JSON.
- Sortable columns. The current table headers (Year, Decade, Duration, Markers) are display-only and stay that way.
- Editing notes from the list. That happens in clip detail.
- Server-side full-text search of notes. The search box still searches names only (`((clip.name)contains(...))`).
- Re-styling the clip detail page (`/clips/<id>`) to match. Detail page is out of scope.
- Pre-warming or background-fetching posters; first viewport hit is when we fetch.
- Pruning / size-bounding the on-disk poster cache. Poster JPEGs are small (KBs each); the whole catalogue fits in single-digit MBs. Manual cleanup if it ever matters.

## Architecture

```
┌──────────────── GET / (HTMX or full page) ─────────────────┐
│   filter form (unchanged)                                   │
│   ────────────────────────────────────────────────          │
│   ⎕ ▣  ┌──────┐  Krajina pod sněhem · 1934 · 4:21 · 6mk    │
│        │POSTER│  Zimní záběry, koně se saněmi…             │
│        │      │  …kostel v pozadí                          │
│        └──────┘  [More]                                     │
│   ────────────────────────────────────────────────          │
│   ⎕ ▣  ┌──────┐  Pražský hrad · 1928 · 2:14 · 3mk          │
│        │POSTER│  Pohled z Petřína, dav lidí…               │
│        └──────┘                                             │
│   ────────────────────────────────────────────────          │
│   ‹ Prev   1–20 of N   Next ›                               │
└─────────────────────────────────────────────────────────────┘
      │
      │  per-row <img loading="lazy"
      │           src="/api/poster/{clip_id}?v={poster_id}">
      ▼
┌────────────── new /api/poster/{clip_id} ────────────────────┐
│  disk cache: data/cache/posters/{clip_id}.jpg               │
│  on miss → ctx.catdv.download_poster(clip_id)               │
│         → atomic write (.tmp → rename)                      │
│         → FileResponse with                                  │
│           Cache-Control: public, max-age=31536000, immutable│
└─────────────────────────────────────────────────────────────┘
```

**Reuse, no pipeline changes.** The list endpoint and its cache (`backend/app/repositories/clip_list_cache.py`) already carry `posterID`, `notes`, `bigNotes`. The redesign exposes those fields through `clip_summary()`, adds CSS / template structure for the new row shape, and adds one new HTTP route + one `CatdvClient` method for poster JPEGs.

### New / changed modules

| File | Change |
|---|---|
| `backend/app/ui/view_models.py` | `clip_summary()` adds `poster_id`, `notes_excerpt`, `notes_has_more` keys (next section). |
| `backend/app/routes/pages.py` | Default `limit` for the clips list changes from `50` → `20`. |
| `backend/app/routes/posters.py` | **New.** `GET /api/poster/{clip_id}` route — disk-cached pass-through to CatDV. |
| `backend/app/services/catdv_client.py` | New method `download_poster(clip_id) -> bytes` (or streams to a path). Mirrors `download_proxy` shape but for the small JPEG blob. |
| `backend/app/app.py` (or wherever routers are mounted) | Mount `posters.router`. |
| `backend/app/templates/pages/_clips_tbody.html` | Replace `<table>`-based rows with `<ul class="clip-list">` of `<li class="clip-row">`. Keeps `#clips-region` wrapper + pager intact so HTMX swaps still target the right node. |
| `backend/app/templates/pages/clips.html` | Wrap the region in an `x-data="{ expanded: {} }"` Alpine scope for per-row expand state. |
| `backend/app/static/styles.css` (or current stylesheet) | New rules for `.clip-list`, `.clip-row`, `.clip-row__rail`, `.clip-row__poster`, `.clip-row__body`, `.clip-row__notes`, `.clip-row__notes.is-clamped`, `.clip-row__more`, `.poster-fallback`. |

## View-model

`backend/app/ui/view_models.py:57` — `clip_summary()` gains three keys:

```python
def clip_summary(clip: CanonicalClip, cache_status=None) -> dict[str, Any]:
    pd = clip.provider_data
    notes_raw = pd.get("notes") or pd.get("bigNotes") or ""
    notes_excerpt = _fix(notes_raw) or None
    notes_has_more = bool(
        notes_excerpt
        and (len(notes_excerpt) > 140 or notes_excerpt.count("\n") >= 2)
    )
    return {
        "id": int(clip.key[1]),
        "name": _fix(clip.name),
        "duration_secs": clip.duration_secs,
        "year": _first_value(clip.fields.get(_YEAR_FIELD)),
        "decade": _first_value(clip.fields.get(_DECADE_FIELD)),
        "marker_count": len(clip.markers),
        "cache": cache_status_view(cache_status) if cache_status else None,
        "poster_id": pd.get("posterID"),         # int | None
        "notes_excerpt": notes_excerpt,          # full, un-truncated string
        "notes_has_more": notes_has_more,        # drives [More] caret
    }
```

`notes_excerpt` is the **full** text — CSS handles the visual two-line clamp via `-webkit-line-clamp:2`. Sending the full string means the "More" expand happens client-side with no extra request. The `notes_has_more` flag is a conservative server-side heuristic (>140 chars or ≥2 newlines) so we don't render a useless caret for short notes; getting it wrong is cheap (a needless caret, or a missing one that the user can click the row to bypass).

## Poster route

`backend/app/routes/posters.py` (new):

```
GET /api/poster/{clip_id}
```

- Path param `clip_id` is what CatDV's `/catdv/api/9/clips/{id}/poster` endpoint accepts.
- Query string `?v={poster_id}` is **client-only** — the server ignores it. Different `v` values look like different resources to the browser cache, so we can return `Cache-Control: public, max-age=31536000, immutable` and never re-fetch unless the poster actually changes (a new `posterID` shows up in the list response → the `<img src>` URL changes → browser refetches).
- Disk cache: `data/cache/posters/{clip_id}.jpg`.
- On cache hit: `FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=31536000, immutable"})`.
- On miss:
  1. Acquire an in-process `asyncio.Lock` keyed by `clip_id` (small `dict[int, Lock]` with a module-level mutex around insertion). Second waiter wakes after the first writer finishes and falls through to the disk-hit branch.
  2. Call `await ctx.catdv.download_poster(clip_id)` → bytes.
  3. Write to `{clip_id}.jpg.tmp` then `os.replace` to `{clip_id}.jpg` (atomic).
  4. Return `FileResponse` as above.
- On 404 from CatDV (clip has no poster): return `HTTP 404`. The template never renders the `<img>` in that case (it checks `c.poster_id` before emitting `<img>`), so a 404 here is a real edge case worth logging but not failing on.
- No new VPN session is opened. `download_poster` reuses the existing `CatdvClient` and its single seat. `download_proxy`'s relogin/retry-on-401 pattern is the model.

## `CatdvClient.download_poster`

Add to `backend/app/services/catdv_client.py` alongside `download_proxy` (line 135):

```python
async def download_poster(self, clip_id: int) -> bytes:
    """Fetch the JPEG poster for a clip. Small blob; returned all at once."""
    if not self._logged_in:
        await self.login()
    url = f"{self._base}/catdv/api/9/clips/{clip_id}/poster"
    resp = await self.http.get(url)
    if resp.status_code == 401:
        await self.login()
        resp = await self.http.get(url)
    resp.raise_for_status()
    return resp.content
```

Unlike `download_proxy`, we don't stream / resume — posters are small (single-digit KB up to maybe ~50 KB), so reading the whole body into memory is fine.

## Template

`backend/app/templates/pages/_clips_tbody.html` — replace the `<table>` with:

```html
<div id="clips-region" class="clips-region">
  <ul class="clip-list">
    {% for c in clips %}
      <li class="clip-row"
          :class="{ 'is-expanded': expanded[{{ c.id }}] }">
        <div class="clip-row__rail" onclick="event.stopPropagation()">
          <input type="checkbox"
                 class="row-check"
                 name="clip_keys"
                 value="catdv/{{ c.id }}"
                 aria-label="Select clip {{ c.id }}">
          {% with cache = c.cache %}
            {% include "pages/_cache_badge.html" %}
          {% endwith %}
        </div>

        <a class="clip-row__poster" href="/clips/{{ c.id }}" aria-hidden="true" tabindex="-1">
          {% if c.poster_id %}
            <img loading="lazy" decoding="async"
                 src="/api/poster/{{ c.id }}?v={{ c.poster_id }}"
                 alt="">
          {% else %}
            <span class="poster-fallback"></span>
          {% endif %}
        </a>

        <div class="clip-row__body">
          <h3 class="clip-row__title">
            <a href="/clips/{{ c.id }}">{{ c.name }}</a>
            <span class="meta mono">
              {{ c.year or "—" }} ·
              {{ "%d:%02d"|format((c.duration_secs|int)//60, (c.duration_secs|int)%60) }} ·
              {{ c.marker_count }} mk
            </span>
          </h3>
          {% if c.notes_excerpt %}
            <p class="clip-row__notes"
               :class="{ 'is-clamped': !expanded[{{ c.id }}] }">{{ c.notes_excerpt }}</p>
            {% if c.notes_has_more %}
              <button type="button" class="clip-row__more"
                      @click="expanded[{{ c.id }}] = !expanded[{{ c.id }}]"
                      x-text="expanded[{{ c.id }}] ? 'Less' : 'More'">More</button>
            {% endif %}
          {% endif %}
        </div>
      </li>
    {% else %}
      <li class="clip-row clip-row--empty">No clips match.</li>
    {% endfor %}
  </ul>

  {# pager block unchanged from current file #}
</div>
```

`clips.html` — wrap `_clips_tbody.html` include in `x-data="{ expanded: {} }"` so per-row expand state is page-scoped. State resets on HTMX swap, mirroring how `row-check` state already resets — acceptable for v1.

## CSS

```css
.clip-list { display: flex; flex-direction: column; gap: 0; }

.clip-row {
  display: grid;
  grid-template-columns: auto 160px 1fr;
  gap: 12px;
  padding: 12px 16px;
  border-top: 1px solid var(--border-1);
  align-items: start;
}
.clip-row:first-child { border-top: none; }

.clip-row__rail { display: flex; flex-direction: column; gap: 6px; align-items: center; }

.clip-row__poster {
  display: block;
  width: 160px;
  aspect-ratio: 16 / 9;
  background: var(--surface-2);
  border-radius: 4px;
  overflow: hidden;
}
.clip-row__poster img { width: 100%; height: 100%; object-fit: cover; display: block; }
.poster-fallback {
  display: block; width: 100%; height: 100%;
  background: var(--surface-2) url("/static/film-strip.svg") center/40% no-repeat;
  opacity: 0.4;
}

.clip-row__body { min-width: 0; }  /* lets line-clamp work inside grid */
.clip-row__title { margin: 0 0 4px; font-size: 14px; }
.clip-row__title a { color: var(--text-1); text-decoration: none; }
.clip-row__title a:hover { text-decoration: underline; }
.clip-row__title .meta { margin-left: 8px; color: var(--text-2); font-weight: normal; }

.clip-row__notes {
  margin: 0;
  color: var(--text-2);
  font-size: 13px;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.clip-row__notes.is-clamped {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.clip-row__more {
  margin-top: 2px;
  padding: 0;
  background: none; border: none;
  color: var(--accent-1); font-size: 12px; cursor: pointer;
}
.clip-row__more:hover { text-decoration: underline; }
```

(Color tokens above are illustrative — implementation should use whatever variables the current stylesheet already defines.)

## Page size

`backend/app/routes/pages.py` — the route that renders `clips.html` currently defaults `limit=50`. Change to `limit=20`. The pager already handles any page size correctly; no other code touches the default.

## Edge cases

- **Clip has no `posterID`:** template falls back to `.poster-fallback`; no `<img>` is emitted, no request fires.
- **Poster fetch fails / VPN down:** `<img>` 404s; the browser renders its broken-image glyph. The list page still renders cleanly. We do not block the page on poster availability.
- **Clip has no notes:** the `<p>` and `[More]` button are skipped; row collapses to title-only with extra whitespace below the poster.
- **Mojibake notes:** already handled by `_fix()` in `view_models.py` — same path as the existing `_marker_view` / `clip_detail` flows.
- **Concurrent first-fetch of same poster:** `asyncio.Lock` per `clip_id` coalesces. Second request waits and reads from disk.
- **CatDV session expired mid-poster-fetch:** `download_poster` re-logs in on 401 and retries once, same pattern as `download_proxy`. No extra seat consumed.
- **HTMX swap after filter change:** new `#clips-region` HTML replaces old; Alpine re-initialises `expanded: {}`. All rows render collapsed. This matches the current behaviour of the bulk-select checkboxes.

## Testing

- Unit: `tests/ui/test_view_models.py` (or wherever `clip_summary` is currently exercised) — assert new keys (`poster_id`, `notes_excerpt`, `notes_has_more`) are populated correctly from a fixture clip with notes, with `bigNotes`-only, and with neither.
- Unit: poster route — given a fixture CatDV client that returns bytes, assert the cache file is written atomically, and that a second call serves from disk without re-calling the client.
- Manual: load `/` against the live CatDV, confirm posters appear lazily on scroll, "More" expands a row in-place, page size defaults to 20, filter changes and pagination still work.

## Risks

- **List-response shape varies by clip type.** The cached sample is a movie clip (`type: "clip"`). Sequence / sub-clip variants might omit `posterID` even when a poster exists — easy to fix by falling back to the first `thumbnailID` if present, but worth one-time verification before declaring done.
- **Browser cache busting via `?v=` is library-dependent.** A few CDNs strip unknown query strings before deciding cache keys; we're not behind one here (LAN-only behind WireGuard), so this is theoretical. If we ever front this with a CDN, switch to `/api/poster/{clip_id}/v/{poster_id}` (path-based) instead.
- **Per-row Alpine `x-data` proliferation.** The expand state lives on a parent `expanded: {}` map, so we avoid creating ~20 small Alpine components per page. Keep that structure during implementation.

## Open questions

None. Layout, notes treatment, and poster strategy were all chosen during brainstorming.
