# 0053. New-batch picker

**Date:** 2026-06-02
**Status:** Accepted
**Lifespan:** Feature

## Context

ADR 0052 decided that "+ New batch" on the Batches hub would redirect the user
to the clips list (`href='/'`), relying on the existing clips-list multi-select
flow to start an annotate job that feeds `/batches`. Two problems emerged in
practice:

1. **Teleport:** the redirect navigates the user away from the hub entirely.
   After starting a batch they must manually navigate back to `/batches` to
   see it appear.
2. **Cross-page selection is impossible:** the clips-list checkbox selection
   is DOM-driven (checked state lives in `<input>` elements that are
   server-replaced on each paged fetch). Picking clips from page 2 always
   cleared the picks from page 1, so any batch larger than one page was
   not reachable without a text search that fits in a single page.

The spec (`docs/specs/2026-06-02-batches-new-batch-picker-design.md`)
calls for an in-page two-pane picker modal on `/batches` that fixes both:
the operator stays on the hub, and selection persists across paginated
fetches.

## Alternatives

### 1. Keep the redirect (ADR 0052) + fix cross-page selection in the clips list

The clips list could adopt a client-side selection map too, but that would
require refactoring its bulk-annotate flow and coupling the batches feature
to a different page's state machine. Rejected: the batches hub should be
self-contained; the clips-list flow is its own surface.

### 2. Server-side session / URL-encoded selection state

Encode selected clip ids in the URL (or store them in server session state)
so they survive page transitions. Rejected: URL length limits make this
fragile for large selections; server session state adds persistence complexity
outside the existing cache layers.

### 3. In-page picker with a second video-list renderer

Duplicate the clips-list `_video_list.html` rendering inside the new picker.
Rejected: violates the CLAUDE.md reuse rule; the shared `_video_list.html`
scaffold already accepts injectable `head_cells` and `row_cells` partials,
so the picker can reuse it via `_batch_picker.html` with minimal additional
columns.

### 4. "Selected only" as a separate server-rendered route

Add a `/batches/picker?sel=1,2,3` route that re-fetches only selected clips.
Rejected: requires CatDV round-trips per UI interaction; the basket is
already fully described by the client `sel` map, so client-side rendering is
sufficient and avoids the network.

### 5. kind/decade filters as server-side catalog predicates

CatDV's list_clips does not expose kind/decade filter parameters directly;
emulating them would require client-side post-filtering or a full-catalog
fetch followed by in-memory filtering. Deferred: too expensive for the
initial implementation; the search + cache + anno filters already cover the
primary selection patterns.

## Decision

- **Reverses ADR 0052's "New batch reuses the clips-list flow (redirect)"
  decision.** The redirect teleported the user away and relied on clips-list
  DOM-checkbox selection that resets on server-side paging, so cross-page
  selection was impossible. Replaced by an in-page two-pane modal.

- **Selection is a client-side map keyed by clip id**, with metadata (name,
  kind, thumb URL) captured from the row DOM on each checkbox tick. Picks
  survive server-paginated fetches and filter changes; the basket panel
  renders from the map without re-fetching.

- **`query_clip_page` extracted** from the clips-list route
  (`backend/app/routes/pages/clips.py`) and shared with the new
  `GET /batches/picker` route (one query path, one place to maintain).
  The clips-list N+1 pin (`test_clips_page_perf.py`) still holds at
  8 statements.

- **kind/decade filters deferred** (no server-side catalog predicate added
  yet); the search, cache-state, and annotation-state filters are forwarded
  to `query_clip_page` as-is. "Selected only" reuses the client basket
  chip renderer — no second row template.

- **`GET /batches/picker` needs live services** (typed 503 when offline),
  matching the other live-catalog routes. The batch hub's read path
  (`GET /batches`, `GET /batches/table`) remains `CoreCtx`-only (offline-safe).

## Consequences

- The operator never leaves the Batches hub to start a new batch; the hub
  shows the new batch immediately via the existing `jobs-changed` SSE refresh.
- Cross-page selection works for any catalog size: the `sel` map grows as
  the user pages and filters; server fetches are stateless.
- The shared `query_clip_page` helper is the single query path for both the
  clips list and the picker; N+1 regressions are caught by the existing
  `test_clips_page_perf.py` pin.
- The clips-list "Annotate selected" bulk flow is unchanged (behavior-
  preserving refactor, guarded by `test_routes_pages.py` + the perf pin).
- kind/decade filter predicates remain a future work item; the deferred
  scope is recorded here so a future implementer understands the gap is
  intentional.
