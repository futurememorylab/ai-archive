# 0057. Draft review: buffered Save/Cancel edits + applied/deleted item lifecycle

**Date:** 2026-06-04
**Status:** Accepted
**Lifespan:** Feature

Spec: `docs/specs/2026-06-04-draft-review-edit-accept-ux-design.md`.
Builds on ADR 0054 (the Alpine-data-driven Draft panel).

## Context

Three UX/correctness problems in the Draft review panel: (1) edits
persisted live on every `@change` (and also flipped the item to
`accepted`), with a Delete button sitting next to a half-finished edit
and no way to back out; (2) marker edits persisted fire-and-forget from
`player.js`, outside `reviewMixin._inflight`, so "Accept & apply all"
could enqueue the upstream apply before the edit's decision write landed
— the apply read stale values or skipped still-`pending` items; (3)
applied items stayed in the Draft list indefinitely (only `rejected` was
filtered), and rejected items were unrecoverable from the UI.

## Alternatives

- **Keep live-persist, only track the marker fetches in `_inflight`.**
  Fixes the race but keeps the no-undo editing model and the Delete
  button during edits — the UX complaints stand.
- **Buffer edits in a separate bound object** (inputs bind to a copy,
  copy back on Save). Cleanest data flow but the timeline drag code and
  the bar's `:style` bindings operate on the live item; rebinding them
  to a buffer object would fork the drag implementation.
- **Buffer via snapshot-on-open** (chosen): inputs/drag keep mutating
  the live item (card + timeline preview stay live for free); opening
  Edit deep-copies the item, Cancel copies the snapshot back, Save does
  one tracked persist.
- For deletes: undo-toast-only (unrecoverable after ~6s) vs. a
  persistent Deleted section vs. both (chosen — toast for the instant
  "oops", `details` strip fed by a server-side `deleted` bucket for
  any-time recovery, since `rejected` rows already survive in the DB).

## Decision

- Edit is a transaction: `startEdit` snapshots, `saveEdit` does one
  tracked `_persist(accepted, full edited_value)`, `cancelEdit` restores
  the snapshot. Drag/nudge no longer persist; `player.js::_persistMarker`
  is deleted and `startMarkerDrag` routes through `startEdit` (falling
  back to the raw `editingItemId` flag on pages without the mixin).
  Switching items and "Accept & apply all" auto-save the open edit —
  silently dropping a buffered edit would recreate bug (2) in new form.
- Every decision write goes through `reviewMixin._persist`, which
  `applyDraft` awaits via `_inflight` — the race is structurally gone.
- `draft_review_arrays` partitions per item: rejected+applied → nowhere;
  rejected → `deleted` bucket (Restore = decision `pending`, an already
  supported endpoint value); applied → excluded but counted in
  `applied_count` (panel shows "N applied — syncing to CatDV");
  otherwise → live arrays. Upstream sync failures stay in the sync
  drawer; the panel does not re-surface them per-clip.

## Consequences

- Reload during an open edit loses the unsaved buffer (acceptable:
  drafts are low-stakes; pre-change behavior saved half-edits, which was
  worse).
- The Draft panel empties immediately after apply even though the
  upstream write is asynchronous; the Published panel reflects it only
  after the sync engine drains. The "syncing to CatDV" empty state names
  that window.
- Found during acceptance: a successful `apply_changes` left the 7-day
  clip cache untouched, so Published served the pre-apply clip long after
  a successful sync ("markers applied but missing from Published"). The
  adapter now deletes the clip's cache row after a successful PUT (the
  PUT response is not a full clip, so write-through wasn't an option).
- `applied_count` counts non-rejected applied items of the *latest*
  annotation only (same scope the panel always had).
- Restore preserves any prior edits (`set_decision` COALESCEs
  `edited_value`).
