# Top app bar, left rail, and cache view redesign — design

Date: 2026-05-20
Status: approved, ready for implementation

## Context

The handoff bundle from Claude Design (`api.anthropic.com/v1/design/h/ukjaMt3tPbDtI22Q2iYTGA`) ships a much richer UI than what currently lives in this repo: a dark "pro media tool" shell with a full top bar (brand + breadcrumbs + env pillset), a 56px left navigation rail, a status bar, and a redesigned `/cache` screen with a metric strip, tabs (All / Queue / Local / AI), and a clean inventory table.

The existing app:

- Uses Jinja templates + HTMX + Alpine. No build step.
- `templates/pages/layout.html` provides a thin 40px topbar (brand, crumb, single READ-ONLY pill) — no rail.
- `static/app.css` already shares the design's color tokens (`--accent #f5a623`, `--panel #14181d`, Inter + JetBrains Mono) — visual upgrade is mostly additive CSS.
- `/` (clips list), `/clips/{id}` (detail), and `/cache` exist. The cache page is a **standalone** `cache_page.html` that does not extend `layout.html`, uses `system-ui`, and a light background — it is visually inconsistent with the rest of the app.

The user explicitly scoped this redesign to: **top app bar**, **left rail** (only three icons: clips/search, preview clip, cache inspector), and a **cache view upgrade**. Other screens from the design (Templates, Jobs, Review, Archive, Settings) and the design's status bar / tweaks panel are out of scope.

## Goals

1. Recreate the design's top app bar visually in Jinja.
2. Add a left rail with three icon buttons that navigate between the three live destinations.
3. Migrate `/cache` onto the new shell and rebuild its content to match the design's `CacheScreen`.
4. Keep the existing backend behaviour untouched — this is a UI-layer change. HTMX patterns and API endpoints stay as they are; new tab/filter URL params are additive.

## Non-goals

- No React or build pipeline. Stay HTMX + Jinja + Alpine.
- No status bar. The host/env signals it carries are folded into the topbar pillset.
- No tweaks panel. The dark theme is the only theme.
- No Templates / Jobs / Review / Archive / Settings screens.
- No new pin/unpin action — endpoint does not exist; icon is omitted.

## Visual design source

- Bundle root: `/tmp/catdv_design/catdv-annotator/`
- Primary file: `project/CatDV Annotator.html` and its imports
- Components used by this design: `screens.jsx` (TopBar, SideRail), `icons.jsx`, `cache.jsx`, `styles.css`

The CSS tokens at the top of `styles.css` (`:root` block, dark theme) are nearly identical to the existing `static/app.css` tokens — port only the missing rules.

## Architecture

### Layout shell

`templates/pages/layout.html` becomes a **3-row × 2-col CSS grid** (drop the status row from the design):

```
grid-template-rows:    40px 1fr
grid-template-columns: 56px 1fr
grid-template-areas:
  "topbar topbar"
  "rail   main"
```

Two new Jinja blocks make the shell composable per page:

- `{% block rail_active %}` — string id of the active rail item: `"clips"`, `"preview"`, `"cache"`
- `{% block topbar_pills %}` — the right-side pillset; defaults to `DEV · <host:port>`, `CATALOG <id>`, `READ-ONLY`. Pages may override.

The `{% block crumb %}` block already exists and stays as-is.

### Top app bar

```
[ • CatDV Annotator ] [ crumb / strong-crumb ] [ grow ] [ DEV · host:port ] [ CATALOG <id> ] [ READ-ONLY ]
```

- 40px tall, `background: var(--panel)`, `border-bottom: 1px solid var(--line)`
- Brand: 7px accent dot with 2px halo, `font-weight: 600, font-size: 12.5px`, right border separator
- Crumb: `var(--text-2)` chain, slash `var(--text-4)`, last segment `var(--text)` + `font-weight: 500`, right border separator
- Env pills: 22px height, uppercase mono 10.5px, `border: 1px solid var(--line-2)`, `border-radius: 11px`. The `DEV` pill gets `.ok` (green text + green LED with shadow); the rest stay neutral.
- Settings such as `--webkit-app-region: drag` are intentionally dropped (web app, not Electron).

### Left rail

```
.rail (56px, panel-bg, right border)
  └ .rail-btn × 3  (40×40, IconClips / IconPlay / IconCache)
```

- Default `color: var(--text-3)`, hover `background: var(--hover); color: var(--text)`
- Active state: `background: var(--surface); color: var(--text)` + an accent vertical bar (`::before`, 2px wide, 24px tall, `background: var(--accent)`) on the left edge
- Buttons are `<a href>` (not React state) so each is a real navigation:
  - Clips → `/`
  - Preview → `window.lastClipPath || "/"` (resolved at render time from `localStorage`)
  - Cache → `/cache`

**Preview-icon behaviour**

- On every clip-detail page render, a small inline script writes `localStorage.setItem("catdv:lastClipId", "<id>")`.
- A small inline script in `layout.html` rewrites the Preview anchor's `href` from `/` to `/clips/<lastClipId>` if one is present.
- If no clip has been visited yet, the link stays on `/`.

The three rail buttons each have a `title` attribute for the tooltip. The Settings icon and the bottom rail spacer are not included.

### Icons

Three SVG icons (Clips, Play, Cache) ported from `icons.jsx` into Jinja partials at `templates/icons/_clips.svg`, `_play.svg`, `_cache.svg`. Style: 18×18, currentColor stroke, `stroke-width: 1.7`, `stroke-linecap: round`. The `IconClips` "filmstrip" path and `IconCache` "stacked cylinders" path come straight from the design source.

### Cache view

`templates/cache_page.html` is rewritten to:

1. Extend `layout.html` (`{% block rail_active %}cache{% endblock %}`).
2. Set crumb to `System / Cache management`.
3. Page header: `<h1>Cache</h1>`, meta "inspector · queue · purge", refresh button.

**Metric strip** (`.metric-strip`, 4 tiles in a CSS flex row):

| Tile | Value | Sub | Bar | Foot |
|---|---|---|---|---|
| Local cache | total local bytes (human) | "of <cap>" | % of cap | "<n> metadata · <m> media" |
| AI store | AI bytes (human) | first store id | — | "<n> objects · <exact-bytes> B" |
| Prefetch queue | queued+downloading | "idle" / "active" | — | "<done> done · <err> err · <cxl> cxl" |
| Orphans | count | orphans bytes (human) | — | `Show & purge →` link → `/cache?orphans=1` |

The summary endpoint already provides most of this. **New requirement**: also expose `orphans_count` and `orphans_bytes` in the cache summary's view-model dict. If those aren't already in `CacheInspector.summary()`, add a cheap aggregation in the route handler (count `evictable` orphan layers across `list_orphans()` once).

**Tabs** (`.cache-tabs`):

- All / Queue / Local cache / AI cache
- Active tab gets `border-bottom: 2px solid var(--accent)` + brighter text colour.
- Each tab is a server URL: `/cache?tab=all|queue|local|ai`. Default = `all`.
- The tab body is wrapped in a target div (`#cache-table-region`) so a click triggers an `hx-get` swap rather than a full page reload — same UX as the existing clips search.

**Filter bar** (`.cache-filterbar`):

- Layer-dot legend on the left: `metadata`, `media-local`, `media-ai` — each a small coloured dot with label, using the design's `.lyr-meta/.lyr-local/.lyr-ai .on` colours.
- Right: `<n> entries` count (mono, muted).
- Existing filter inputs (`store`, `workspace`, `orphans only`, `evictable only`) move into a collapsible "Filters" detail block above the table; URL params unchanged.

**Bulk action bar** (`.bulkbar`):

- Renders only when `selected.length > 0` (Alpine `x-show`).
- `<n> selected · <total-bytes>`, Clear, Re-fetch, Purge selected.
- Re-fetch calls `POST /api/cache/prefetch` with selected `clip_keys`.
- Purge calls `POST /api/cache/bulk-evict` with `layers: ["media-local"]` (matches today's clips-page behaviour).

**Inventory table** (`.cache-tbl`, used by All/Local/AI tabs):

| col | content |
|---|---|
| checkbox | select row |
| Clip | name + small id (mono, muted) — `orphan` rows get `<span class="orphan-mark">orphan</span>` and a subtle red tint |
| Workspace | mono |
| Layers | three coloured dots via `.lyr-row > .lyr-{meta,local,ai}.on/off` |
| Local | bytes (human + exact below in small muted) |
| AI | bytes (human + exact below) |
| actions | Re-fetch, Purge (per-row; visible on row hover or when selected) |

**Queue table** (`.cache-tbl` variant, used by Queue tab):

| col | content |
|---|---|
| checkbox |  |
| Status | tag (`queued` warn / `downloading` info / `done` good / `error` bad) |
| Clip | provider/id link |
| Layer | mono (always `media-local` today) |
| At | started_at / requested_at, mono |
| Size | bytes_downloaded MB, mono, right-aligned |
| actions | Cancel (downloading) / Retry (error) / Remove |

The queue table auto-refreshes every 2s via `hx-trigger="every 2s"` (same pattern as today's `_prefetch_panel.html`).

## Backend changes

Small and additive — no schema changes.

1. **`routes/cache.py`**
   - Accept `tab: str | None = None` in `cache_page()`. Map to filtered statuses:
     - `tab="local"` → keep rows with `layers[1].present` (media-local present)
     - `tab="ai"` → keep rows with `layers[2].present`
     - `tab="queue"` → render the queue partial instead of the inventory partial
     - `tab="all"` or absent → unchanged behaviour
   - For HTMX requests (`HX-Request: true`), return only the table partial (`_cache_inventory_table.html` or `_cache_queue_table.html`) so the tab swap doesn't re-render the metric strip.
   - In the JSON response of the page, surface `orphans_count` and `orphans_bytes` for the metric strip. Compute from `list_orphans()` if `summary()` doesn't carry them.

2. **No new API endpoints.** Re-fetch and Purge actions reuse the existing `/api/cache/prefetch` and `/api/cache/bulk-evict` endpoints.

## Files added or changed

| Path | Action |
|---|---|
| `backend/app/templates/pages/layout.html` | grid shell, rail markup, pillset block |
| `backend/app/templates/pages/_rail.html` | new — rail partial included by layout |
| `backend/app/templates/pages/_topbar_pills.html` | new — default pillset partial |
| `backend/app/templates/pages/clips.html` | `{% block rail_active %}clips{% endblock %}` |
| `backend/app/templates/pages/clip_detail.html` | set `rail_active = "preview"`, write lastClipId to localStorage |
| `backend/app/templates/cache_page.html` | rewritten; extends layout; new metric strip + tabs |
| `backend/app/templates/pages/_cache_inventory_table.html` | new — HTMX swap target |
| `backend/app/templates/pages/_cache_queue_table.html` | new — HTMX swap target |
| `backend/app/templates/icons/_clips.svg` | new |
| `backend/app/templates/icons/_play.svg` | new |
| `backend/app/templates/icons/_cache.svg` | new |
| `backend/app/static/app.css` | add rail, pillset (extra pills), metric-strip, cache-tabs, cache-tbl, bulkbar, lyr-row, orphan-mark rules |
| `backend/app/routes/cache.py` | accept `tab` query param; HTMX partial routing; expose orphan totals |

The existing `_prefetch_panel.html` becomes unused once `_cache_queue_table.html` replaces it; **delete it** (and the `/ui/cache/queue` route's reference) only after verifying the new queue table is wired into the auto-refresh path. If anything else imports it, leave it for a follow-up cleanup.

## Testing

- `pytest -q` must remain green. Existing route tests for `/`, `/clips/{id}`, `/cache` continue to exercise the page-level rendering — if they check for specific strings, update them where the rewrite legitimately removes them (e.g. `"Cache management"` heading text moves).
- Add one small test: `GET /cache?tab=local` returns 200 and the response body contains a row from an entry with `media_local: true` but does not contain one that's `media_local: false`. Same idea for `tab=ai`.
- Smoke-test the three rail destinations manually: clips → preview (after viewing a clip once) → cache, with each icon's active state styled.
- No visual regression test framework — the design's pixel-perfect requirement is met by reading the CSS, not by screenshots.

## Risks

- **Orphan total bytes** may require a small inspector helper if `summary()` doesn't already return it. Mitigation: compute inline in the route handler from `list_orphans()` for this pass; promote to `summary()` later if needed.
- **HTMX partial routing** for tab swaps is the most fragile new piece. Use `request.headers.get("HX-Request") == "true"` (already used in `clips_list`) so the same handler serves both full-page and partial.
- **localStorage-driven Preview href** depends on the page running JS before the user clicks. The rewrite happens synchronously in a `<script>` inside the rail markup, before paint, so it should always be set on second navigation; first visit (no remembered clip) falls back to `/`, which is acceptable.

## Acceptance

- Top bar matches the design's brand + crumb + pillset layout in colour, type, and spacing.
- Left rail has three icons, correct active state with accent left-bar, and each routes to the expected page.
- `/cache` shares the dark shell, shows the four-tile metric strip, tabs swap content via HTMX, the inventory table shows layer dots and byte counts, and the bulk bar appears on selection and successfully calls existing prefetch/evict endpoints.
- All existing tests pass; new tab-filter test passes.
