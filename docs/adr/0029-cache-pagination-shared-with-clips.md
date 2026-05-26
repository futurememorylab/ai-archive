# 0029. Cache page pagination shared with Clips

**Date:** 2026-05-26
**Status:** Accepted

## Context

The Cache page rendered every inventory row in one list; the Clips list is
paginated (50/page, Prev/Next). The Cache page should paginate the same way, and
the pagination code should be shared rather than duplicated.

Spec: `docs/specs/2026-05-26-cache-pagination-design.md`.

## Alternatives

- **Reuse only the CSS** and write a cache-specific pager `<nav>`. Rejected: the
  user wanted shared markup, not just shared styling.
- **Cache-local, no extraction.** Rejected: duplicates the pager.
- **Server/API-level paging of the cache inventory** (as Clips paginates via the
  CatDV `list_clips` call). Rejected: the cache inventory is already fully
  assembled in memory, so in-process slicing is correct and simpler.
- **Full-page-reload pager** (literally identical to Clips). Rejected in favour
  of HTMX region swaps, matching the cache tabs' existing behaviour.

## Decision

- Extract a shared `page_offsets(offset, limit, total)` helper
  (`backend/app/ui/pagination.py`) and a shared `pages/_pager.html` partial used
  by **both** routes. The partial is parameterized by an optional `hx_target`:
  set → `hx-get` region swap (Cache, into `#cache-table-region`, with
  `hx-push-url`); unset → plain `href` full-page nav (Clips, unchanged output).
- The Cache route slices its in-memory `rows_for_template` (`offset:offset+limit`,
  default 50) **after** the full filtered list is built, so `total`, the tab
  badge counts, and all tab/filter/orphan semantics stay correct.
- Only the inventory tabs (All / Local / AI) paginate. The Queue tab stays a live
  recent-activity list (2 s refresh, capped at 50) with no pager.

## Consequences

- One pager component + one offset helper serve both pages; the Clips render
  output is unchanged (verified).
- Cache pages are a constant length (≤ 50 rows), so the page no longer grows
  unbounded — complementing the scroll fix in [0028](./0028-ui-responsiveness-local-assets-feedback-scroll.md).
- **Tradeoff:** turning the page swaps `#cache-table-region`, which resets the
  client-side Alpine bulk-selection (`cacheSel`) state. Accepted as expected
  behaviour rather than a silent regression.
- Switching tab / changing filters / toggling orphans carry no `offset`, so they
  reset to page 1 naturally.
