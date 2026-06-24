# 0035. Draft review & accept UI

- **Date:** 2026-05-27
- **Status:** Accepted
- **Lifespan:** Feature

## Context

Gemini annotation jobs already produced drafts: an `Annotation` plus one
`review_items` row per proposed marker/field/note, each starting at
`decision='pending'`, and a working but **UI-less** accept→apply pipeline
(`/api/review/items/{id}/decision`, `/api/review/clips/{id}/apply`,
`WriteQueue` → `pending_operations` → `SyncEngine`). The clip page showed
drafts read-only, so a human literally could not accept a draft, and there
was no consolidated view of what was pending. This work added the review
UI on top of the existing storage/apply machinery, for video and image
clips. Several non-obvious calls were made; they share context so they are
grouped here. Spec: `docs/specs/2026-05-27-draft-review-accept-design.md`.

## Alternatives

- **HITL surface:** a dedicated full-screen review app vs. reusing the
  existing clip detail page in a "review mode".
- **Bulk "yolo" granularity:** per-item bulk grid vs. per-clip selection
  with a coarse kind filter vs. apply-everything.
- **Per-item default:** neutral/opt-in, forced yes/no, or pre-accepted
  opt-out.
- **Media-type filter on `/review`:** denormalize media kind into the
  pending-clips SQL vs. classify in Python.
- **Selection model:** clone the cache page's `cacheSel()` into a second
  copy vs. extract a shared factory.

## Decision

- **HITL = the existing clip page in `?review=1` mode**, not a new screen.
  Per-item "keep"/edit controls are added into the shared
  `_anno_panels.html`, gated so they render only on draft items (which
  carry `item_id`); a `reviewQueue` Alpine component drives "Apply & next"
  across a sessionStorage queue. No second player/panels renderer
  (CLAUDE.md forbids parallel-evolving one).
- **Yolo = select clips on `/review` + a kind filter** (Markers/Fields/
  Notes toggles) → `POST /api/review/apply-batch {clip_ids, kinds}`.
  Granularity stops at kind; per-item bulk selection would just re-create
  the HITL flow. The batch path shares the single-clip apply resolver, and
  the kind filter is honoured end-to-end so a "markers only" apply never
  flushes previously-accepted field/note items.
- **Pre-accepted, opt-out**: every draft item starts checked; the reviewer
  unticks/edits the wrong ones. "Review by exception" — fewest clicks when
  Gemini is mostly right. Apply makes the checkbox state authoritative.
- **Media filter classifies in Python, not SQL.** Media kind lives in
  `clip_cache.provider_data` (via `_media_kind`), not in
  `review_items`/`annotations`. When a media filter is active the handler
  fetches the full candidate set (capped), classifies, filters, then
  paginates — so total/metrics/rows stay consistent and no wanted-kind
  clip is hidden on a later page. The no-filter path keeps the cheap SQL
  `LIMIT/OFFSET`. This trades a bounded full scan (only when filtering;
  the review backlog is small and self-draining) for avoiding a schema
  denormalization. Revisit if backlogs grow large or the filter gets hot.
- **Shared row-selection factory.** The cache page's inline `cacheSel()`
  was extracted to `static/row_select.js` (`rowSelect()`), and the cache
  page refactored to consume it; `/review`'s `reviewSel()` builds on the
  same factory. One selection implementation, two pages.

## Consequences

- Reviewers stay in a flow (clip → Apply & next → next clip) instead of
  bouncing to a list; the rail badge surfaces the pending backlog.
- The feature is almost entirely additive over existing storage — no
  schema change to `review_items` or the apply pipeline.
- Apply remains offline-safe and idempotent (enqueue skips already-applied
  items); `/review` is a DB-only read, usable offline.
- Inline editing is offered only for scalar fields; list/multi-value fields
  are accept/reject-only, to avoid collapsing a structured list into a
  joined string via `edited_value`.
- A third copy of the selection pattern still exists in `clips.html`
  (`bulkSel()`); consolidating it onto `rowSelect()` is tracked as a
  follow-up (out of this feature's scope; the clips list page has no
  render test to guard a refactor yet).
- The media-filter Python scan is capped (`CANDIDATE_CEILING`); a very
  large single-job backlog filtered by media could miss clips beyond the
  cap — acceptable given backlog size, noted for future revisit.
