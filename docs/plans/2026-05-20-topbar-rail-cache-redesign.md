# Implementation plan — topbar, left rail, and cache view redesign

Date: 2026-05-20
Spec: `docs/specs/2026-05-20-topbar-rail-cache-redesign-design.md`
Branch suggestion: `feat/ui-shell-and-cache-redesign`

The plan is grouped into four short PRs so each can land independently. PR 1 is a pure visual restyle and is safe to ship on its own; PR 4 (cache rewrite) depends on PR 1's new shell.

---

## PR 1 — Layout shell + topbar pillset (no behaviour change)

**Goal:** the new 3-row × 2-col grid renders on every page with the expanded topbar pillset. Rail markup exists but is hidden / empty so existing pages look the same except for the upgraded topbar.

### Tasks

1. **CSS — port the missing rules from the design's `styles.css` into `static/app.css`.**
   - Add `.app` grid (rows: `40px 1fr`, cols: `56px 1fr`, areas `"topbar topbar" "rail main"`).
   - Update `.topbar` to use `grid-area: topbar` and add the missing `.pillset` rule + `.env-pill.ok` modifier (green LED + green border).
   - Add `.main { grid-area: main; overflow: hidden; display: flex; flex-direction: column; }`.
   - Keep all existing rules.

2. **`templates/pages/layout.html` — restructure to the grid.**
   - Wrap the page in `<div class="app">`.
   - Topbar: brand → `{% block crumb %}` → `<span class="grow"></span>` → `{% block topbar_pills %}{% include "pages/_topbar_pills.html" %}{% endblock %}`.
   - Add `<aside class="rail">{% include "pages/_rail.html" %}</aside>` and `<main class="main">{% block body %}{% endblock %}</main>`.
   - Add `{% block rail_active %}{% endblock %}` (used by the rail partial).

3. **`templates/pages/_topbar_pills.html` (new).**
   - Render three env pills:
     - `<span class="env-pill ok"><span class="led"></span>DEV · {{ request.url.netloc }}</span>` (use the request host:port)
     - `<span class="env-pill">CATALOG {{ settings.catdv_catalog_id }}</span>`
     - `<span class="env-pill">READ-ONLY</span>`
   - Required Jinja context: `settings`. Wire it up by passing `settings` into every `TemplateResponse` context, or — simpler — read it from `request.app.state.ctx.settings` via a Jinja global filter / `context_processor`. Pick the lighter touch: add `templates.env.globals["settings"] = lambda r: r.app.state.ctx.settings` is awkward; instead pass `{"settings": ctx.settings}` in each page handler (3 places: `/`, `/clips/{id}`, `/cache`).

4. **`templates/pages/_rail.html` (new but empty body in this PR.)**
   - Render an empty `<div class="rail-spacer"></div>` so the column reserves its width. Icons land in PR 2.
   - This gives PR 1 a working 56px-rail column with the page content correctly offset.

5. **Tests**
   - Existing route tests must still pass. If any tests assert on the exact topbar text (e.g. "READ-ONLY"), they still pass because the pill is still there.
   - Manual: visit `/` and `/clips/{id}`, verify the topbar shows three pills and there's a 56px-wide empty column on the left.

### Review checkpoint

PR 1 ships when:
- `pytest -q` is green.
- Visual check: topbar has 3 pills (host, catalog, read-only); 56px gap on the left of the main content.

---

## PR 2 — Left rail icons + navigation

**Goal:** the three icon buttons appear in the rail with correct active state and the Preview icon respects `localStorage`.

### Tasks

1. **Icon partials.** Port three SVGs from `icons.jsx` (Clips, Play, Cache) into `templates/icons/_clips.svg`, `_play.svg`, `_cache.svg`. Each file is a single `<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">…</svg>`. Paths to copy verbatim:
   - Clips: filmstrip — `rect`, vertical lines, dots (from `icons.jsx` line 10-14)
   - Play (use for Preview): `<polygon points="6 4 20 12 6 20 6 4"/>` (line 57-59)
   - Cache: stacked cylinders (line 122-126)

2. **`templates/pages/_rail.html` — fill in the three buttons.**
   - Read `rail_active` block content into a local Jinja variable; default `""`.
   - For each button: `<a class="rail-btn{% if rail_active == 'clips' %} active{% endif %}" href="/" title="Clips">{% include "icons/_clips.svg" %}</a>` (and similar for the other two).
   - Preview: `<a class="rail-btn{% if rail_active == 'preview' %} active{% endif %}" id="rail-preview" href="/" title="Preview clip">…</a>` — the JS in step 4 rewrites the href.

3. **CSS — `.rail`, `.rail-btn`, `.rail-btn.active`, `.rail-btn::before` (accent bar).**
   - Copy rules from the design's `styles.css` lines 180-213, excluding `.rail-badge` and `.rail-spacer` (not used in this scope).

4. **`localStorage`-driven Preview link.**
   - Inline `<script>` at the bottom of `_rail.html`:
     ```js
     (function(){
       var id = localStorage.getItem("catdv:lastClipId");
       if (id) document.getElementById("rail-preview").href = "/clips/" + id;
     })();
     ```
   - In `clip_detail.html`, add an inline `<script>` near the top of the `{% block body %}`:
     ```js
     localStorage.setItem("catdv:lastClipId", "{{ clip.id }}");
     ```

5. **Set `rail_active` on existing pages.**
   - `clips.html`: `{% block rail_active %}clips{% endblock %}` near the top.
   - `clip_detail.html`: `{% block rail_active %}preview{% endblock %}`.
   - Cache page handled in PR 4.

6. **Tests**
   - Add `tests/routes/test_rail.py` (or extend an existing UI test): GET `/` and assert response body contains `class="rail-btn active"` near the Clips icon. GET `/clips/{id}` (mock the archive provider) and assert active class on the Preview button.

### Review checkpoint

- `pytest -q` green.
- Manual: visit `/`, see Clips icon active (accent bar on left). Click Cache icon → goes to `/cache` (still old layout in this PR — that's fine). Visit a clip, then click the Preview icon from `/cache` — it should land on that clip.

---

## PR 3 — Backend: `?tab=` filter + orphan totals + HTMX partial routing

**Goal:** the cache route can serve four tab views, return partials for HTMX swaps, and surface orphan count + bytes.

### Tasks

1. **`routes/cache.py` — extend `cache_page()`.**
   - Add `tab: str | None = None` query param. Validate against `{"all", "queue", "local", "ai"}`; coerce others to `"all"`.
   - After loading statuses, filter:
     - `tab == "local"`: `[s for s in statuses if s.layers[1].present]`
     - `tab == "ai"`: `[s for s in statuses if s.layers[2].present]`
   - For `tab == "queue"`, skip status loading entirely and load `active + recent` queue rows (already done at end of handler).
   - Compute `orphan_count` and `orphan_bytes`:
     ```py
     orphan_statuses = await insp.list_orphans()
     orphan_count = len(orphan_statuses)
     orphan_bytes = sum(
         sum(l.bytes for l in s.layers if l.evictable)
         for s in orphan_statuses
     )
     ```
   - Build the response context with: `summary`, `orphan_count`, `orphan_bytes`, `tab`, `rows`, `queue_active`, `queue_recent`, `queue_counts`, `filters`.

2. **HTMX-aware response.**
   - If `request.headers.get("HX-Request") == "true"`:
     - `tab == "queue"` → render `pages/_cache_queue_table.html` (new in PR 4 — stub it in this PR with `<tbody></tbody>` content if needed to keep PR self-contained, then flesh out in PR 4).
     - else → render `pages/_cache_inventory_table.html` (same — stub it now).
   - Else render the full page (also stubbed; full content arrives in PR 4).

3. **Tests**
   - `test_cache_tab_local`: insert two clip caches via the existing factories (one with `media_local`, one without). GET `/cache?tab=local` → response contains the first clip's id, not the second's.
   - `test_cache_tab_ai`: same idea against `media_ai`.
   - `test_cache_tab_queue_returns_queue_partial_for_htmx`: GET `/cache?tab=queue` with `HX-Request: true` → response is a small partial (no `<html>` tag) and contains queue-table markup or empty-queue message.
   - `test_cache_summary_includes_orphan_totals`: handler context dict has `orphan_count` and `orphan_bytes` keys.

### Review checkpoint

- `pytest -q` green with the new tests.
- The stubbed partials are minimal but valid HTML; the real markup lands in PR 4.

---

## PR 4 — New cache view markup + CSS

**Goal:** the visual rebuild of `/cache` — metric strip, tabs, filter bar, bulk bar, inventory table, queue table.

### Tasks

1. **`templates/cache_page.html` — rewrite to extend layout.**
   - `{% extends "pages/layout.html" %}`
   - `{% block rail_active %}cache{% endblock %}`
   - `{% block crumb %}<span class="crumb"><span>System</span> <span class="sep">/</span> <span class="strong">Cache management</span></span>{% endblock %}`
   - Body wraps everything in `<div class="page cache-page" x-data="cacheSel()">`.

2. **Page header.**
   - `<div class="page-hdr"><h1>Cache</h1><span class="meta">inspector · queue · purge</span><div class="grow"></div><button class="btn ghost" onclick="location.reload()"><…refresh-icon…> Refresh</button></div>`

3. **Metric strip.** Four `.metric` tiles (use Jinja, no React):
   - **Local cache**: value `{{ summary.total_local_bytes | bytes_human }}`, sub `of {{ summary.media_cache_cap_bytes | bytes_human }}`, bar `{{ 100 * summary.total_local_bytes / summary.media_cache_cap_bytes }}`, foot `<b>{{ summary.metadata_clip_count }}</b> metadata · <b>{{ summary.media_local_clip_count }}</b> media`.
   - **AI store**: value `{{ summary.total_ai_bytes | bytes_human }}`, sub first AI store id (or "—"), foot `<b>{{ ai_total_count }}</b> object · <span class="muted-2">{{ summary.total_ai_bytes | comma }} B</span>`.
   - **Prefetch queue**: value `{{ queue_counts.queued + queue_counts.downloading }}`, sub `"active"` if value > 0 else `"idle"`, foot `<b>{{ queue_counts.done }}</b> done · <b>{{ queue_counts.error }}</b> err · <b>{{ queue_counts.cancelled }}</b> cxl`.
   - **Orphans**: value `{{ orphan_count }}`, sub `{{ orphan_bytes | bytes_human }}`, tone `danger`, foot `<a class="link-danger" href="/cache?orphans=1">Show & purge →</a>`.
   - Register two small Jinja filters in `routes/cache.py` (or a shared template setup file): `bytes_human` (KB/MB/GB) and `comma` (thousand separators).

4. **Tabs.** `<div class="cache-tabs">` with four `<a class="ctab{% if tab == 'all' %} active{% endif %}" hx-get="/cache?tab=all" hx-target="#cache-table-region" hx-swap="innerHTML" hx-push-url="true">All <span class="ctab-n mono">{{ counts.all }}</span></a>` — same for queue/local/ai.

5. **Filter bar.** `<div class="cache-filterbar">` with the layer-dot legend (`.lyr-row > .lyr-{meta,local,ai}.on`) and the `<n> entries` mono count. Existing per-field filters go into a collapsible `<details><summary>Filters</summary>…</details>` block above the table.

6. **Bulk action bar.** `<div class="bulkbar" x-show="count > 0">` with count + bytes + Clear + Re-fetch + Purge selected. Reuse the bulkSel pattern from `clips.html` (`bulkPrefetch()` / `bulkEvict()`). Move the shared logic into a tiny `cacheSel()` Alpine factory.

7. **`templates/pages/_cache_inventory_table.html` (new).** Renders the inventory table from spec § Cache view → Inventory table.
   - Use `.lyr-row` markup for the three dots.
   - Per-row actions: Re-fetch button → `bulkPrefetch([key])`, Purge button → `bulkEvict([key])`.
   - Bottom-of-table empty state: `No entries match the current filter.` when `rows` is empty.

8. **`templates/pages/_cache_queue_table.html` (new).** Renders the queue table from spec § Cache view → Queue table.
   - Outer wrapper: `<div id="prefetch-panel" hx-get="/cache?tab=queue" hx-trigger="every 2s" hx-swap="outerHTML">` — keeps the existing 2s auto-refresh contract.
   - Status tags via `.tag.{good,warn,bad,info}`.

9. **CSS — port the rest of the design's cache rules.**
   - From `styles.css`: `.metric-strip`, `.metric` and its children, `.link-danger`, `.cache-tabs`, `.ctab`, `.cache-filterbar`, `.bulkbar`, `.cache-listwrap`, `.cache-tbl`, `.lyr-row`, `.lyr-meta/local/ai`, `.orphan-mark`, `.row-actions`, `.ra-btn`.
   - Goal: no rule is invented — every one comes from the design file. If a referenced class is missing from the design's CSS, pick the closest existing rule and note it inline in `app.css` with a comment.

10. **Wire up the old `_prefetch_panel.html` callers.**
    - `/ui/cache/queue` should now return `_cache_queue_table.html` (it auto-refreshes itself).
    - Delete `_prefetch_panel.html` once nothing imports it (search the repo for the partial name first).

11. **Tests**
    - `test_cache_page_full_render`: GET `/cache` returns 200, body contains `metric-strip`, `cache-tabs`, and four metric tiles.
    - `test_cache_page_orphans_tile`: orphan count and bytes appear in the metric strip from PR 3's wiring.
    - `test_cache_page_tab_swap`: GET `/cache?tab=local` with `HX-Request: true` returns inventory partial only (no `<html>` element).
    - Update / remove any tests that asserted on the old cache_page's `<h1>Cache management</h1>` text.

### Review checkpoint

- `pytest -q` green.
- Manual: open `/cache`. Four tiles render with realistic numbers. Click each tab — content swaps without a full page load. Select two rows — bulkbar appears. Click Re-fetch — see entries enter the queue tab. Click Purge — confirm dialog, then rows disappear from Local tab. Switch to Queue tab — see the auto-refresh tick.

---

## Handover summary

- **PR 1** (shell): 1 day. Pure restyle. Safe to ship alone.
- **PR 2** (rail icons): 0.5 day. Depends on PR 1. Safe to ship alone.
- **PR 3** (backend `?tab=` + orphan totals): 0.5 day. No frontend change visible yet.
- **PR 4** (cache view rewrite): 1.5 days. Depends on PR 1 + PR 3.

Total estimate: ~3.5 days of focused work. Each PR is small enough to review in one sitting.

### Continuation prompt for the next session

> Pick up the redesign plan at `docs/plans/2026-05-20-topbar-rail-cache-redesign.md`. Start with PR 1: extend `templates/pages/layout.html` to the 3-row × 2-col grid and add the topbar pillset partial. Spec lives at `docs/specs/2026-05-20-topbar-rail-cache-redesign-design.md`. The Claude Design bundle is unpacked at `/tmp/catdv_design/catdv-annotator/` — design CSS is `project/styles.css` and the relevant components are `screens.jsx` (TopBar + SideRail) and `cache.jsx` (CacheScreen). Use existing HTMX/Alpine patterns from `clips.html` for the bulk selection logic.
