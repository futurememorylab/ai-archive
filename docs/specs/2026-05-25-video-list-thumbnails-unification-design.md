# Video lists: thumbnails + unified list component

**Date:** 2026-05-25
**Status:** Approved (design)

## Problem

The app has two "list of videos" surfaces that look like unrelated
components even though they show the same underlying clips:

- **Clips list** (`/`, `pages/clips.html` + `pages/_clips_tbody.html`) —
  table class `.tbl`, columns Cache / Clip / Year / Decade / Duration /
  Markers.
- **Cache list** (`/cache`, `cache_page.html` +
  `pages/_cache_inventory_table.html`) — table class `.cache-tbl`,
  columns Clip / Workspace / Layers / Local / AI / actions.

Two concrete defects:

1. **No thumbnails.** Rows carry only text. The clips table even has an
   empty `<span class="thumb">` placeholder that was never wired up.
2. **Misaligned columns on the clips list.** The clips table renders
   `<tr class="row">`, and `app.css:1006` defines a global flex helper
   `.row { display: flex }`. That turns every table row into a flex
   container, so the `<td>`s no longer line up under their `<th>`
   headers. The cache table dodges this only because it uses
   `.cache-row`.

The two tables are styled and built independently, so they drift and
duplicate effort.

## Goals

- Add poster thumbnails to both video lists.
- Make both lists **one shared component** — identical chrome (table,
  selection, cache badge, thumbnail+name cell, row height, typography),
  with each list supplying only its own data columns.
- Fix the row-alignment bug.

## Non-goals (explicitly out of scope)

- Thumbnail eviction UI or a managed "thumbnail" cache layer.
- Prefetch/warming of thumbnails (on-demand lazy fetch only).
- A global "loading N/M" progress indicator (per-cell shimmer only).
- Surfacing the exact-bytes (`comma B`) subline — it is being removed.

## Decisions (from brainstorming)

- **Thumbnail source:** CatDV poster (via clip `posterID`), cached
  locally so it survives offline. Chosen over ffmpeg-from-proxy (most
  clips have no cached proxy → mostly placeholders) and over a hybrid
  (unnecessary complexity).
- **Sharing strategy:** shared scaffold partial + per-page injected
  trailing-cell partials (approach "A"). Rejected a fully data-driven
  column spec ("B") because the cache rows contain markup that doesn't
  fit a flat value spec (layer-dot badge, hover action buttons with
  Alpine bindings, per-row orphan class, `data-bytes` checkbox attr).
- **Loading UX:** per-cell skeleton shimmer → poster fades in; quiet
  placeholder on no-poster/offline/error. No global spinner.
- **Simplification:** drop the second line of the byte cells (the exact
  `comma B` value) on the cache list.

## Architecture

### Thumbnail fetch + cache

Mirror the proxy-cache pattern (`RestProxyResolver` stores
`cache_dir/{clip_id}.mov`). Thumbnails are plain files —
`thumbs/{clip_id}.jpg` — in a new thumbnail cache dir, sibling to the
proxy cache. No DB table (tiny, regenerable).

**Endpoint:** `GET /api/media/{clip_id}/thumb`

```
if thumbs/{clip_id}.jpg exists:           -> FileResponse (image/jpeg, cacheable)
elif catdv client available (online):
    poster_id = clip.posterID
    fetch poster bytes from CatDV (short timeout)
    save to thumbs/{clip_id}.jpg
    -> FileResponse
else / no poster / fetch error / timeout: -> 404
```

A `404` is the placeholder trigger: the cell's `<img onerror>` swaps to
the gradient/icon placeholder. The HTML table never blocks on thumbnails
— `<img loading="lazy">` lets the browser fetch per visible row.

**CatDV client:** add a `download_thumbnail(...)` method hitting the
**resolved** endpoint:

```
GET /catdv/api/9/thumbnail/{id}        # singular — image renderer
    ?width=&height=&fmt=jpg&bgcolor=   # all optional
```

- Returns image bytes (`image/jpeg` by default; `fmt=png` for PNG).
- `{id}` = the clip's `posterID` (fallback: first entry of
  `thumbnailIDs`).
- Auth reuses the existing `JSESSIONID` session, same as
  `download_proxy`.
- **Gotcha:** the *plural* `/catdv/api/9/thumbnails/{id}` is the JSON
  metadata endpoint (returns an envelope, not an image) — do not use it.

Verified during brainstorming: the path exists under `/catdv/api/9/`
(an unauthenticated probe returns the `AUTH` envelope, not `404`),
matching the official SquareBox docs and this repo's original design
note (`docs/specs/2026-05-18-catdv-annotator-design.md`). Not yet
verified: the authenticated image response and whether `posterID` or
`thumbnailIDs[0]` is the better source — confirmed by a free smoke test
in the first implementation task (the running dev server already holds a
session), not by taking a second CatDV seat.

### Shared list component

New `pages/_video_list.html` renders the parts that must be identical:

- `<table class="vlist">`, sticky header.
- Leading columns: select-all / per-row checkbox, the **cache-layer
  badge** (three dots: metadata / media-local / media-ai), and the
  **thumbnail + name cell** (`64×36` lazy `<img>` + name, optional
  name subline).
- Uniform row height, hover, selection wiring.

Parameters passed by each page:

- `rows` — row view models sharing common keys (see View models).
- `head_cells` — template path for the trailing `<th>`s.
- `row_cells` — template path for the trailing `<td>`s; rendered once
  per row with the row in context.
- `cache_label` — header label for the badge column ("Cache").
- optional `row_href` key on rows for clickable navigation (clips).

Per-page wrappers that legitimately differ stay in the page templates:
the clips list keeps its `#clips-region` wrapper + pager; the cache list
keeps its `#cache-table-region` wrapper + "N entries" + bulk bar. Only
the `<table>` itself comes from the shared partial.

The cache list keeps its bespoke trailing cells as ordinary Jinja in its
`row_cells` partial: Workspace, Local (single-line `bytes_human`), AI,
and the hover Re-fetch / Purge buttons; the orphan row state and
`data-bytes` checkbox value are carried on the row view model and applied
by the scaffold.

### View models (`ui/view_models.py`)

- `clip_summary` gains `thumb_url = f"/api/media/{id}/thumb"`.
- The cache-page row builder produces the same common keys consumed by
  the scaffold — `select_value`, `thumb_url`, `name`, `name_sub`
  (clip-id for cache rows), cache-layer badge data, `row_class`
  (`orphan`), `row_bytes` (for `data-bytes`) — plus its list-specific
  fields (workspace, local_bytes, ai_bytes).
- Drop the exact-bytes subline data.

### CSS (`static/app.css`)

- Introduce one `.vlist` ruleset; retire `.tbl` and `.cache-tbl`
  duplicated rules.
- Table rows use a non-colliding class (`vrow`); the global
  `.row { display:flex }` helper stays for non-table uses but no longer
  touches table rows — **this is the alignment fix.**
- Add `.thumb` (`64×36`, rounded), the skeleton shimmer animation, and
  the placeholder style.
- Remove `.exact` subline styling.

## Testing

- **Contract** (`/api/media/{id}/thumb`): cache hit serves the file;
  online miss fetches from CatDV and writes the file then serves it;
  offline / no-poster / fetch-error returns 404; unknown clip handled.
- **View model:** `clip_summary` includes `thumb_url`; cache rows expose
  the shared keys.
- **Template smoke:** both `/` and `/cache` render through
  `_video_list.html` without error (online and offline modes).

## ADR

Record one ADR (next number in `docs/adr/`) covering the linked
decisions: shared list component (approach A over B), CatDV-poster
thumbnail caching, and dropping the exact-bytes subline. Update
`docs/decisions.md`.

## Resolved: CatDV thumbnail endpoint

`GET /catdv/api/9/thumbnail/{id}` (singular) — see CatDV client section
above. Remaining confirmation (authenticated image response; `posterID`
vs `thumbnailIDs[0]`) is a free smoke test in the first implementation
task, not a blocker.
