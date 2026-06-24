# 0088. Filtered clip list skips un-hydratable clips instead of 502-ing the page

**Date:** 2026-06-16
**Status:** Accepted
**Lifespan:** Invariant

## Context

The clips list filters (`?anno=none|for_review|…`, `?cache=none|…`) take a
local-first path: `clip_list_filters.resolve()` builds a candidate clip-id set
from SQLite, and `_filtered_page()` hydrates each id — preferring the metadata
cache, falling back to a single live `archive.get_clip()` for genuine misses.

A candidate set can include ids with **no cached metadata and no reachable
upstream record**:

- synthetic / manual `review_items` rows (e.g. clip `1000000001`) that were
  never listed from CatDV, so they are absent from `clip_cache`; and
- any clip while **CatDV is offline / seat-limited** (the common case — see the
  cache-layer discipline in `CLAUDE.md`).

`_hydrate_clip()` deliberately re-raises non-`NOT_FOUND` `ProviderError`s (a
transient error must not be read as absence — ADR 0042). In the **list-render**
context that meant a *single* un-hydratable clip aborted the entire filtered
page with `502`. Worse, the list is loaded over HTMX, which by default **does
not swap on non-2xx responses** — so the 502 silently left the *previous*
filter's rows on screen. The visible symptom: "Annotated and Not annotated
return the same rows", and "switching filters stops changing results".

## Alternatives

1. **Keep re-raising (status quo).** Honest about the offline state, but makes
   every absence/large-candidate filter unusable whenever one edge clip can't be
   hydrated, and the failure is invisible through HTMX. Rejected.
2. **Pre-fetch live during render for misses, harder/retry.** Doubles down on a
   per-clip live fetch on a page-render path — itself a documented anti-pattern
   (`CLAUDE.md` → "Eagerly fetching on a page-render path"). Rejected.
3. **Skip the un-hydratable clip from this render, log it, keep the page.**
   The list is a cache-first, offline-safe view; a clip that can't be loaded
   right now is recoverable on refresh once the archive is reachable. Chosen.
4. **Surface a "N clips couldn't be loaded" banner.** Strictly nicer UX, but
   needs a skipped-count threaded through `_filtered_page` → `query_clip_page`
   → two callers. Deferred — not needed to fix the bug; the per-clip skip is
   logged server-side, and the HTMX error toast (below) covers genuine failures.

## Decision

- `_filtered_page()` catches `ProviderError` **per candidate clip**, skips it
  from the current render, and logs a warning (`logger.warning("filtered list:
  skipping clip %s — %s", …)`). The whole-page `502` path is reserved for real
  archive failures on the *unfiltered* (live `list_clips`) path.
- `_hydrate_clip()`'s contract is unchanged (still returns `None` only for
  `NOT_FOUND`, re-raises transient). The list view now decides what to do with a
  raised transient error; other callers (batch run) keep the strict behaviour.
- Frontend safety net (`static/nav-feedback.js`): a global
  `htmx:responseError` / `htmx:sendError` handler pushes an **error toast**, so
  a non-2xx or network failure can never again masquerade as "nothing changed".

This is a context-scoped refinement of ADR 0042, not a reversal: terminal
status decisions still demand explicit evidence; a transient list-render skip is
recoverable and must not take down a page.

## Consequences

- The absence/large-candidate filters work offline and never 502 on one edge
  clip. `anno=none` and `anno=has_any` now return visibly different sets.
- Clips that are genuinely un-hydratable (synthetic ids, offline) are quietly
  omitted from the filtered list until reachable; the omission is in the server
  log, not the UI. If that proves confusing, add alternative 4's banner.
- Any future HTMX action that fails now shows a toast instead of failing
  silently — a general UX improvement beyond this bug.
- Covered by `tests/unit/test_filtered_page_offline_resilience.py`.
