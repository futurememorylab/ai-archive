# 0098. Retrying a conflicted write-back re-bases on the live clip

**Date:** 2026-06-17
**Status:** Accepted

## Context

A write-back lands in `conflict` status when the clip's live etag no longer
matches the `expected_etag` captured at enqueue time — the clip changed in CatDV
after the user reviewed it (`adapter.apply_changes`, ADR 0091). The Sync drawer
offers a **Retry** button for conflict (and failed) rows, backed by
`reset_for_retry` / `reset_clip_for_retry` / `reset_all_for_retry`, which flip the
row back to `pending` for the SyncEngine to re-drain.

But the reset kept the original `expected_etag`. On re-drain the adapter compared
that same stale etag against the (still newer) live etag and returned `conflict`
again — **a blind retry could never succeed.** The drawer's Retry button was a
no-op-shaped trap: it looked actionable, reset the row, and the row came straight
back as a conflict. This is the opposite of "dead-reliable write-back": the one
recovery action offered to the user did nothing.

## Alternatives

- **Three-way merge / show the user the upstream diff before retrying.** Correct
  in the limit but far heavier; needs a diff UI and per-op-type merge rules. The
  ops are already conflict-tolerant on replay (markers de-dupe on `in.frm`, notes
  chain/idempotently append per ADR 0091/0097, fields overwrite), so re-basing is
  safe enough without a merge UI.
- **Auto-clear the etag whenever the SyncEngine hits a conflict.** Rejected: that
  removes the conflict guard entirely and would silently overwrite concurrent
  upstream edits with no human in the loop. The re-base must be an *explicit*
  user choice (clicking Retry).

## Decision

The three retry resets drop `expected_etag` **only for `conflict` rows**, via a
shared SET fragment `_CONFLICT_RETRY_ETAG`:

```sql
expected_etag = CASE WHEN status = 'conflict' THEN NULL ELSE expected_etag END
```

(`status` is the pre-update value — SQLite evaluates SET right-hand sides against
the original row, even though the same statement sets `status = 'pending'`.)

With `expected_etag` NULL, the adapter's mismatch check is skipped
(`change_set.expected_etag is not None` guard) and the change re-bases on whatever
is currently upstream. Clicking **Retry** on a conflict therefore means "apply my
change on top of the current clip." `failed` rows keep their etag, so a genuine
concurrent upstream change still surfaces as a conflict on their retry rather than
being silently clobbered.

## Consequences

- Retrying a conflicted clip from the Sync drawer now actually resolves it.
- The conflict guard is preserved for the normal (automatic) drain and for
  `failed`-row retries; only an explicit conflict retry waives it.
- Per-op replay safety carries the re-base: markers de-dupe on `in.frm`, notes
  append idempotently/accumulate (ADR 0091/0097), fields overwrite.
- Covered by `test_reset_*_clears_etag_*` in
  `tests/integration/test_pending_operations_repo.py`; the stale "blind retry
  just re-conflicts" note in `review.js` is updated.
