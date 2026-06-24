# 0094. Accept & apply advances to the next clip in the review queue

**Date:** 2026-06-17
**Status:** Accepted
**Lifespan:** Feature

> **Synthesis note (2026-06-24):** Refinement in the write-back status chain (0091–0098); the converged rule is **Invariant 8** in [`docs/architecture-invariants.md`](../architecture-invariants.md).

## Context

Operator feedback on the batch review flow:

1. **"Accept & apply all" parked you on the clip.** Reviewing a multi-clip
   batch, the natural rhythm is review → apply → *next clip*. Instead, after
   applying, the clip stayed put and the draft panel swapped to the
   "N sent to CatDV — M confirmed on the server so far…" message (the live
   write-back status surface from ADRs 0092/0093). To advance you then had to
   reach for the `›` nav button. The syncing message — useful as a *terminal*
   state — became a per-clip speed bump in the middle of a batch.
2. **Batches table: the last column's row-divider line was misaligned.** The
   actions cell (`.bt-actions`) set `display: flex` on the `<td>` itself. A
   flex `<td>` only grows to its button content rather than stretching to the
   row height the way a real `table-cell` does, so its `border-bottom` (the
   gray divider) rendered higher than the other columns on every row whose
   Batch cell (#num + prompt + version/model) made the row taller.

## Alternatives

- **Keep parking + rely on the `›` button (status quo).** Rejected: it makes
  the common multi-clip case a manual two-step and buries forward progress
  behind the same control used for free browsing.
- **Auto-advance always, including the last clip → bounce back to the list.**
  Rejected: the last clip is exactly where the operator *wants* to see the
  applied/synced confirmation; bouncing away from it would discard the one
  place the message earns its keep.
- **Drop the per-clip syncing message entirely now that we advance.**
  Rejected: it remains the right terminal state for single-clip review, the
  last clip of a batch, and reloads (reconciled by `_checkSyncOnce`). Global,
  always-visible write-back status already lives in the topbar sync chip
  (ADR 0092), so nothing is lost by advancing.

## Decision

- **`acceptApplyAll()` advances on success.** After the apply POST resolves,
  `review.js` calls `navClip(1)`. `applyDraft()` now returns `true`/`false`
  so we only advance on a clean apply (a failed apply keeps you on the clip
  with the error toast). `navClip(1)` is already a bounds-checked no-op on the
  last clip in the queue, so that clip stays put and shows the applied/synced
  message — the deliberate terminal state. The write-back continues
  server-side regardless of navigation; the topbar sync chip is the glanceable
  global status, and each freshly-loaded clip reconciles its own state via
  `_reviewInit` → `_checkSyncOnce`.
- **`.bt-actions` is a real table-cell again.** The `<td>` keeps
  `text-align: right; white-space: nowrap` (so it stays `display: table-cell`,
  full row height, aligned divider); the flex + `gap` moves to an inner
  `.bt-actions-inner` wrapper around the buttons.

## Consequences

- Reviewing a batch is now review → Accept & apply → next, with no manual nav
  step until the queue ends.
- The "syncing… / synced ✓" message is seen only where it's the terminal
  state (last clip, single clip, reloads) — consistent with the topbar chip
  owning live global status.
- This is a JS/CSS behaviour change with no executing test (Python-only stack,
  ADR 0001 — no JS runner); the template-string guards in
  `tests/integration/test_routes_review.py` still pin `acceptApplyAll`/
  `navClip` presence, and the batches render + design-language guard tests stay
  green. Verified manually in draft review.
