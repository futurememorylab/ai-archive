# 0054. Draft review redesign

**Date:** 2026-06-02
**Status:** Accepted
**Lifespan:** Feature

## Context

The original draft review panel (ADR 0035 / 0036) was server-rendered inside
`_anno_panels.html` and used a DOM-checkbox-driven `reviewQueue()` Alpine
component to track which items the user had toggled. Several friction points
accumulated:

1. **No reactive source of truth.** Proposal cards, timeline bars, and the
   review queue each computed state independently from the DOM; accepting a
   marker on the card did not instantly recolor the timeline bar (the page
   had to reload).
2. **Apply navigated away.** The "Accept & apply" path reloaded the full
   page to reflect the applied state, breaking the review walk mid-queue.
3. **No batch-level "Review →" entry point.** The batches hub had a static
   link to the first pending clip but no mechanism to seed the full ordered
   queue of clips with pending draft items across a batch's jobs.
4. **Delete was destructive.** Removing a proposal from the panel deleted
   the `review_item` row; rejected items needed to survive for audit / undo.

The spec (`docs/specs/2026-06-02-draft-review-redesign-design.md`) calls for
an Alpine-data-driven redesign that addresses all four points within the
existing backend surface.

## Alternatives

### 1. Keep server-rendered panel, add page-level SSE refresh on accept

Would propagate accepted state to the timeline via a full partial swap, but
still requires a network round-trip per card interaction and does not solve
the "apply navigates away" or "no batch queue" problems. Rejected: the
reactive card model gives instant UI feedback with no server call, which is
materially better for the review flow.

### 2. Extract a separate `/review/{clip_id}` full-page route

A dedicated review page could own its state cleanly, but creates a second
clip-detail surface diverging from the published panel — double maintenance.
The scope toggle already lets the existing clip detail switch between
"Published" and "Draft" views; extending it is strictly less work. Rejected.

### 3. Persist rejected items by adding a separate `deleted_at` column

An explicit `deleted_at` timestamp could mark deleted items without reusing
the `decision` column. Rejected: `decision` already has an `"rejected"` value
and is indexed; reusing it keeps the schema unchanged and the existing
`draft_review_arrays` serializer's exclusion logic (`decision != "rejected"`)
maps cleanly to the "hide deleted cards" requirement.

### 4. Refresh via HTMX partial swap after apply

The original code returned a server-rendered partial via the `HX-Request`
path. With the redesigned panel being pure Alpine-data, swapping server HTML
into a live Alpine reactive subtree would clobber Alpine's internal state.
Rejected: the new `GET /api/review/clips/{id}/draft-data` JSON endpoint lets
`refreshDraft()` splice new data into the existing Alpine arrays in-place
(`.splice(0, …, ...d.xxx)`), keeping Alpine reactive bindings intact.

## Decision

- **Draft panel moved from `_anno_panels.html` (server-rendered) to an
  Alpine-data-driven card panel (`_anno_draft.html`).** Draft items are
  serialized once at page load into `draftMarkers`/`draftFields`/`draftNotes`
  arrays (each entry carries `item_id` and `status`). The card panel, the
  3-color timeline bars, and all review actions operate on these arrays as
  the single reactive source of truth.

- **`draft_review_arrays(draft) → {markers, fields, notes}`** is a new pure
  function in `backend/app/ui/view_models.py`. It derives `status`
  ("accepted" if `decision == "accepted"`, else "proposed") and excludes
  rejected items entirely. No schema change — it maps the existing
  `decision` field. Unit-tested in isolation.

- **Delete = reject (non-destructive).** `del()` in the `reviewMixin` splices
  the item out of the Alpine array (instant UI) and posts `decision: rejected`
  to `/api/review/items/{id}/decision`. The row survives in the DB; the
  `draft_review_arrays` serializer excludes it from the next `draft-data`
  fetch so it does not reappear after apply/refresh.

- **Apply = apply + stay.** `applyDraft()` POSTs to
  `/api/review/clips/{id}/apply` (existing endpoint), then calls
  `refreshDraft()` which fetches `GET /api/review/clips/{id}/draft-data` (new
  endpoint) and splices the result back into the Alpine arrays in-place. The
  user stays on the clip with an up-to-date card panel; no page reload.

- **Batch Review → seeds the clip walk.** A new repo method
  `pending_clip_ids_for_jobs(job_ids)` and route
  `GET /batches/review-queue?job_ids=…` return the ordered list of clips with
  un-applied items across a batch's jobs (newest annotation first). The
  batches table "Review →" button calls `reviewBatch()` (inline Alpine in
  `batches.html`) which fetches this list, writes it to
  `sessionStorage["catdv:reviewQueue"]`, and navigates to the first clip with
  `?review=1&scope=draft`. The existing `reviewMixin` `navClip(±1)` walk
  logic and sessionStorage key are reused unchanged.

- **Reused without modification:** scope toggle (Published/Draft),
  `player.js` marker drag-edit and `_persistMarker`, the `POST .../decision`
  and `POST .../apply` endpoints, the `sessionStorage` review queue key and
  its auto-skip-empty-review script, and the annotate-refresh flow (now
  repointed from the server partial to the `draft-data` JSON endpoint).

- **3-color timeline:** `draft-range.is-accepted` CSS class (new) is bound
  via `:class="{ 'is-accepted': (_draftItem(id) || {}).status === 'accepted' }"`
  in `_player_overlay.html`. Rejected/deleted bars have no `_draftItem()` hit
  so they do not ghost on the timeline.

## Consequences

- Card accept/unaccept, delete, and edit interactions are instant (no server
  round-trip for UI state). Only persistence calls (`/decision`) go to the
  server; they are fire-and-forget with error toast on failure.
- The draft panel and the 3-color timeline share one Alpine array; there is
  no secondary state to synchronize.
- The HTMX `HX-Request: true` apply path still returns the `_anno_draft.html`
  partial (for callers that swap it in via HTMX), but the redesigned Alpine
  panel no longer relies on that path — `refreshDraft()` handles post-apply
  state update via JSON instead, avoiding Alpine state clobber.
- `pending_clip_ids_for_jobs` is bounded by a batch's member jobs (small IN
  clause) and is offline-safe (DB-only). The route is `CoreCtx`-only.
- No migration required. The `decision` column's "rejected" value was already
  valid; `draft_review_arrays` simply starts using it as the exclusion
  predicate.
- Five integration tests in `test_routes_review.py` that pinned the old
  server-rendered markup were updated to assert the new Alpine structural
  markers (`review-bar`, `ri-card`, `toggleAccept`, `navClip`, `applyDraft`).
