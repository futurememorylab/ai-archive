# 0013. Clip list filters: Cache + Annotations dropdowns, local-first resolution

- **Date:** 2026-05-22
- **Status:** Accepted
- **Lifespan:** Feature

## Context

The clip list page needed a more prominent search box and
two filters — `Cache: any|none|local|ai` and
`Annotations: any|for_review|applied|none|has_any` — plus a single
"Actions" dropdown replacing the three per-action bulk buttons.

Neither cache state nor annotation drafts live at CatDV: both are
local SQLite concerns (`proxy_cache`, `ai_store_files`, `annotations`,
`review_items`). CatDV's `list_clips` cannot accept them as a query
predicate.

**Alternatives considered.**

1. *Client-side filter over the current page* — Simplest. Apply filters
   in the browser to whatever 50 rows the route already fetched. **Rejected:**
   pagination becomes a lie ("5 of 50" when the page is mostly filtered
   out), and "for review" with one draft on the catalog's tenth page
   would show nothing on page 1.
2. *Fetch-all-then-filter* — When a filter is active, walk every
   CatDV page (catalog has hundreds of clips) into memory, enrich
   with cache/annotation status, filter, then paginate locally.
   Always correct. **Rejected:** the VPN is slow (~300–400 KB/s) and a
   filter toggle would block the page on a minute-long sync.
3. *Local-first when filters active* (chosen) — Derive a candidate
   `set[int]` of CatDV clip IDs from SQLite, hydrate each from the
   metadata cache (`clip_cache`) or a single `archive.get_clip` call,
   apply the text query, sort by name, paginate locally.

## Decision

Option 3, with explicit acceptance of its blind spot:
"absence" filters (`cache=none`, `anno=none`) are bounded to the
**universe of clips we've already observed locally** — anything in
`clip_list_cache` pages plus any row in `clip_cache`, `proxy_cache`,
`ai_store_files`, `annotations`, or `review_items`. A clip that exists
upstream but has never been listed will not appear under those
filters until it shows up in a list page.

The filter resolver lives at
`backend/app/services/clip_list_filters.py` and returns
`set[int] | None` (None = no filter, caller takes the existing
CatDV-paginated path).

## Consequences

- *Speed.* Filter toggle is a handful of indexed SQLite queries —
  effectively instant. No CatDV round-trip unless a candidate clip
  isn't in the metadata cache, and even then it's at most `limit`
  per-clip fetches after pagination.
- *Honest pagination.* Total reflects the filtered set, so the pager
  numbers match what the user sees.
- *Minimal blast radius.* The no-filter path is byte-for-byte
  unchanged; only when `cache` or `anno` is non-default does the
  route branch to `_filtered_page`. Existing tests that exercise the
  default path keep passing without modification.
- *Documented limitation.* The "absence" blind spot is unavoidable
  without enumerating CatDV's full catalog on every toggle. It's a
  reasonable price given the workflow — users care about "what do I
  have local / what have I drafted", and those are positive-set
  queries that the SQL knows about precisely.

**Other UI decisions in the same change.**

- *Explicit search submit.* The old `hx-trigger="input changed
  delay:300ms"` autosearch was replaced with a single `<form>` that
  submits on Enter, on the new "Search" button, or on `<select>`
  change. **Why:** every typeahead keystroke against the slow VPN
  burned a CatDV round-trip; the user wanted a deliberate search.
- *Cache filter as single-select (not multi-toggle).* Despite the
  `independent toggles` framing in brainstorming, the user picked the
  simpler single-select dropdown variant. Avoids ambiguity around
  "show clips that have local OR ai cache" vs "have both" — there's
  exactly one selected value.
- *Actions split-button.* Three bulk buttons (`Cache view`,
  `Cache selected`, `Evict selected`) collapsed into one `Actions`
  dropdown with `Cache locally` and `Remove from local cache`. The
  `Cache view ›` link was dropped per spec; users get to the cache
  page from the left rail. Disabled state is driven by the same
  Alpine `count` selection counter the prior toolbar used.
