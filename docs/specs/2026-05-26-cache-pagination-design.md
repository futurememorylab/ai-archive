# Cache page pagination (shared with Clips)

**Date:** 2026-05-26
**Status:** Accepted

## Problem

The Cache page (`/cache`) renders **every** inventory row in one long list. The
Clips list (`/`) is paginated (50 per page, Prev/Next). The Cache page should
paginate the same way so it has a constant, predictable page length, and the
pagination code should be shared between the two pages rather than duplicated.

## Decisions (locked during brainstorming)

- **Page size:** 50 per page (matches the Clips `limit=50` default).
- **Pager mechanism:** HTMX — the Cache pager does `hx-get` into
  `#cache-table-region` (the same region the cache tabs already swap) with
  `hx-push-url="true"`. The Clips pager keeps its existing full-page-nav links.
- **Scope:** Only the inventory tabs (All / Local / AI) paginate. The Queue tab
  stays a live recent-activity list (auto-refreshes every 2 s, capped at recent
  50) — no pager.
- **Reuse approach (A):** Extract a shared pager partial + a shared offset-math
  helper used by *both* routes, rather than reusing only the CSS.

## Current state

- `clips.py::clips_list` takes `offset`/`limit` (default 50), computes
  `prev_offset = max(0, offset - limit) if offset > 0 else None` and
  `next_offset = offset + limit if offset + limit < total else None`, and renders
  `<nav class="pager">` inline in `pages/_clips_tbody.html`
  (`.pager`/`.pg-btn`/`.pg-meta` classes).
- `cache.py::cache_page` builds the full filtered list `rows_for_template` in
  memory (from `status_for_clips` over all cached keys, then tab/filter passes),
  passes it all to the template, and `cache_page.html` shows
  `{{ rows | length }} entries`. The inventory table is rendered by
  `pages/_cache_inventory_table.html` via the shared `pages/_video_list.html`
  scaffold, swapped into `#cache-table-region` on tab clicks.

## Design

### 1. Shared offset helper — `backend/app/ui/pagination.py` (new)

```python
def page_offsets(offset: int, limit: int, total: int) -> tuple[int | None, int | None]:
    """Return (prev_offset, next_offset) for a paged list, or None at an edge."""
    prev_offset = max(0, offset - limit) if offset > 0 else None
    next_offset = offset + limit if offset + limit < total else None
    return prev_offset, next_offset
```

`clips.py` is refactored to call this instead of its inline computation
(behaviour identical). `cache.py` uses it too.

### 2. Shared pager partial — `pages/_pager.html` (new)

Renders the existing pager structure: `‹ Prev | start–end of total | Next ›`,
using the current `.pager` / `.pg-btn` / `.pg-meta` / `.pg-btn.disabled` classes
(no CSS changes). Inputs (passed via `{% with %}`):

- `prev_url` (str | None), `next_url` (str | None) — already-built hrefs, or None
  for a disabled edge button.
- `range_label` (str) — e.g. `"1–50 of 240"` or `"0 of 0"`.
- `hx_target` (str | None) — when set (Cache), each link emits
  `hx-get="<url>" hx-target="<hx_target>" hx-swap="innerHTML" hx-push-url="true"`;
  when None (Clips), links are plain `<a href="<url>">` (full-page nav).

`_clips_tbody.html` is updated to build `prev_url`/`next_url`/`range_label` from
its existing `_pq` query string and include `_pager.html` (no `hx_target`),
replacing its inline `<nav>`. The rendered output for Clips is unchanged.

### 3. Cache route — `cache.py::cache_page`

- Add params `offset: int = 0`, `limit: int = 50`.
- For inventory tabs: after `rows_for_template` is built, set
  `total = len(rows_for_template)`, then slice
  `page_rows = rows_for_template[offset : offset + limit]`. Compute
  `prev_offset, next_offset = page_offsets(offset, limit, total)`.
- Add to `ctx_dict`: `offset`, `limit`, `total`, `prev_offset`, `next_offset`,
  and pass `rows = page_rows` (the slice). For the Queue tab, `total = 0` and no
  pager is shown.
- Tab links, the Filters form, and the orphans link carry no `offset`, so
  switching tab / changing filters / toggling orphans resets to page 1 naturally.

### 4. Cache template — `_cache_inventory_table.html` / `cache_page.html`

- Build the pager query string preserving `tab` + `store` / `workspace` /
  `orphans` / `evictable`, plus `limit` and the target `offset` for each link;
  base path `/cache`. Build `prev_url`/`next_url`/`range_label`, then include
  `pages/_pager.html` with `hx_target="#cache-table-region"`. The pager lives
  inside `#cache-table-region` so it re-renders on every page turn.
- `cache_page.html`: the filter-summary count switches from `{{ rows | length }}`
  to `{{ total }}` (the full filtered count; the pager shows the visible range).

## Alternatives

- **B — reuse CSS + helper only:** Cache-specific pager `<nav>` reusing the
  `.pager` classes; share just `page_offsets()`. No edits to the Clips template,
  but not literally "same code." Rejected: the user wants shared markup.
- **C — cache-local, no extraction:** duplicate the pager. Rejected: duplication.
- **Server/API-level pagination of the cache inventory** (like Clips paginates
  via the CatDV `list_clips` call): unnecessary — the cache inventory is already
  fully assembled in memory, so in-process slicing is correct and simpler.

## Consequences

- One pager component and one offset helper serve both pages — consistent
  behaviour, no duplication. The Clips pager is touched (low risk; its rendered
  output is unchanged and covered by tests).
- Cache pages are a constant length (≤ 50 rows), so the page no longer grows
  unbounded; combined with the recent scroll fix the table area stays steady.
- Slicing happens after the full filtered list is built, so all existing tab /
  filter / orphan / store / workspace semantics and the tab badge counts are
  unchanged (counts remain totals, not per-page).

## Testing

- **Unit** (`page_offsets`): offset 0 (prev None), a middle page (both set), the
  last page (next None), `total ≤ limit` (both None), `total == 0` (both None).
- **Route** (`tests/integration/test_routes_cache.py`): seed 3 cached clips,
  request `/cache?tab=all&limit=2` → 2 rows + a next link (offset 2); request
  `offset=2&limit=2` → 1 row + a prev link. Assert the Queue tab shows no pager.
- **Render parity:** both `_clips_tbody.html` and `_cache_inventory_table.html`
  include `pages/_pager.html`; a Clips route test still passes (output unchanged).
- **Manual browser** (respect CatDV single-seat discipline in `CLAUDE.md`): page
  through the cache; confirm switching tabs and changing filters reset to page 1,
  the table stays a constant height, and Prev/Next swap only the table region.
