# 0036. Fold draft review into the clips list (supersedes the standalone /review page)

- **Date:** 2026-05-27
- **Status:** Accepted (supersedes the `/review` page parts of [0035](./0035-draft-review-accept-ui.md))
- **Lifespan:** Feature

## Context

[0035](./0035-draft-review-accept-ui.md) shipped draft review as a **separate
`/review` page** (its own rail entry, table, batch/media filters, bulk apply)
plus a HITL "review mode" on the clip page gated behind `?review=1`. On first
use the maintainer found the separate page less comprehensible than expected:
it duplicated the clip-browsing surface, and the clip list already had an
`anno=for_review` filter (clips with un-applied `review_items`) that did most of
the same job. The request: merge review into the existing clips list and make
the clip's draft panel editable whenever a draft exists, rather than behind a
mode.

## Alternatives

- Keep the standalone `/review` page (0035 as-built).
- Fold review into the clips list: review info as columns + the existing
  `for_review` filter + bulk actions, and an always-on draft editor.
- A hybrid (both surfaces).

## Decision

Fold review into the clips list; remove the standalone `/review` page.

- **Clips list (`/`)** gains three columns — **Type**, **Batch** (originating
  job id), **Drafts** (un-applied counts like `4m · 2f · 1n`) — populated by
  annotating each row from `ReviewItemsRepo.list_pending_clips`. The existing
  **`anno=for_review`** filter is the "awaiting review" view (no new filter
  needed). The bulk **Actions** menu gains **Review selected →** (stores the
  selected ids in `sessionStorage['catdv:reviewQueue']` and opens the first clip
  at `?review=1`) and a kind-filtered **Apply drafts (selected)**
  (`POST /api/review/apply-batch`).
- **Clip detail** Draft panel shows the per-item keep/edit controls **whenever
  the item is a draft item** (it has an `item_id`) — no longer gated on a
  review mode. Auto-scoping still holds (published/Studio items have no
  `item_id`). The review **action bar** appears whenever the clip has a draft:
  **Accept & apply** (apply, stay) normally; **‹ Prev · Skip · Accept & next →**
  when a queue exists in sessionStorage (i.e. arrived via "Review selected").
- **Editing is full**: marker name/category/description/in/out, field values
  (multi-value split on comma), and note text — all sent as `edited_value` and
  applied via the existing pipeline.
- **Removed**: the `/review` page + route + templates, the rail "Review" entry +
  pending badge, and the now-unused `GET /api/review/pending` and
  `/api/review/pending/count` endpoints. **Kept**: the `apply-batch` endpoint,
  the `_resolve_and_enqueue_clip` helper, the `list_pending_clips` /
  `count_pending_clips` repo methods (the former now powers the clips columns),
  and all clip-detail review-editor code.
- The clips list's hand-rolled `bulkSel()` was consolidated onto the shared
  `rowSelect()` factory (the same one `cacheSel()` uses), removing the third
  copy of the selection logic.

## Consequences

- One surface for browsing and reviewing: filter the clip list to "For review",
  see draft counts inline, select-and-apply or open to edit. Less to learn.
- "Awaiting review" is the existing `for_review` filter; no parallel data path.
- The media-type filter that lived on `/review` is dropped; Type is now a column
  (the clip list filters by cache/annotation state, not media kind). Re-add a
  media filter later only if needed.
- No rail pending-count badge anymore (the page it pointed at is gone). A
  count could be re-surfaced on the clips entry later via `count_pending_clips`.
- `list_pending_clips` is fetched (capped at 2000) on every clips-list render to
  build the Drafts column. The review backlog is small/self-draining, so this is
  cheap; revisit if it grows.
