# 0091. Write-back: uniform retry ceiling, append idempotency, freshest-etag conflicts, and legible status

**Date:** 2026-06-17
**Status:** Accepted
**Lifespan:** Invariant

> **Synthesis note (2026-06-24):** Head of the write-back refinement chain
> 0091 → 0092 → 0093 → 0094 → 0095 → 0096 → 0097 → 0098. The converged,
> current-state rule is **Invariant 8** in
> [`docs/architecture-invariants.md`](../architecture-invariants.md). Read
> this chain only for the detailed history; the invariant is the live canon.

## Context

Follow-up to the notes-placement fix (ADR 0090). A review of the write-back
queue (`WriteQueue` → `pending_operations` → `SyncEngine` → `apply_changes`)
surfaced three correctness gaps plus a status-legibility gap:

1. **Retry ceiling was not uniform.** ADR 0042 bounded *unknown* exceptions by
   `max_attempts`, but the explicit `RetryableError` path and the
   `WriteResult(status="retryable")` path called `mark_retryable` with no
   ceiling — so a persistently-"retryable" condition (e.g. CatDV stuck busy)
   retried forever, never reaching a terminal `failed` state and never
   surfacing to the user.
2. **`AppendNote` was not idempotent under re-drain.** A re-drained op (crash
   recovery via `reset_in_flight_to_pending`, or a retry after a PUT the server
   applied but whose response was lost) re-read the now-appended note and
   appended the text a second time. `AddMarkers` is idempotent (it dedupes on
   `in.frm`); appends had no equivalent guard.
3. **Conflict check used the oldest etag.** When several apply batches for one
   clip are merged into a single ChangeSet, `_tick` used `rows[0].expected_etag`
   (the oldest batch). A stale older batch could spuriously flag the merged
   write as a conflict, or mask a real one.
4. **Status was shown as raw jargon, with a dead control.** The sync drawer
   rendered raw enum values (`in_flight`, `conflict`), labelled a raw ISO
   timestamp as "Age", and offered a "View conflict" button with **no JS
   handler** (dead). The per-clip draft poller folded `conflict` into the same
   "didn't reach CatDV — retry" message as `failed`, even though a conflict is
   not fixed by a blind retry.

## Alternatives

- **(1) Leave retryable unbounded for offline.** Rejected: the offline case is
  already handled earlier — `_tick` returns before calling `apply_changes` when
  the ConnectionMonitor is not `online`, so offline does not consume the budget.
  The unbounded path only helped genuinely-stuck ops spin forever.
- **(2) Idempotency key / dedup table for appends.** Rejected as over-built;
  CatDV's PUT has no idempotency key. A content check (does the live note
  already end with this segment?) covers the real re-drain windows cheaply.
- **(3) Conservative "conflict if etags disagree".** Rejected: more confusing
  UX than simply checking against the freshest snapshot the user actually saw.
- **(4) A bespoke topbar widget / Alpine component for the indicator.** Rejected
  in favour of mirroring the existing connection chip (a `popover()` container
  with an hx-get'd inner partial), reusing `.btn` / `.popover-panel` / the
  drawer table rather than introducing a parallel widget vocabulary.

## Decision

- **Uniform ceiling.** A single `SyncEngine._retry_or_fail(...)` helper bumps
  attempts and keeps the row retryable until the *youngest* op in the group
  reaches `max_attempts`, then flips to `failed` atomically
  (`mark_failed(bump_attempts=True)`). Every retryable path routes through it:
  explicit `RetryableError`, unknown `Exception`, and
  `WriteResult(status="retryable")`.
- **Append idempotency.** `build_put_payload` skips an `AppendNote` whose text
  already equals the live note or is its trailing separated segment.
- **Freshest etag.** The merged ChangeSet uses `rows[-1].expected_etag` (rows
  are ordered oldest-first), i.e. the last-enqueued batch's snapshot.
- **Legible status.** The sync drawer humanises status via `ui.status_pill`
  (Queued / Sending… / Conflict / Failed) with colour states, renders a
  readable enqueue time, drops the dead "View conflict" button (the detail is
  shown inline), and uses `ui.button`. The per-clip draft poller (`review.js`)
  tracks `failed` and `conflict` separately and words the message accordingly
  (conflict → "changed in CatDV after you reviewed — open the Sync drawer to
  resolve"; failed → "didn't reach CatDV — retry").

## Consequences

- A permanently-failing write now reaches a terminal `failed` state and stops
  consuming the queue; the user sees an honest message instead of an eternal
  "syncing…".
- Re-drains no longer duplicate note text.
- Conflict detection reflects the user's most recent view of the clip.
- **Global visibility.** A topbar sync chip (`_sync_chip.html` /
  `_sync_chip_inner.html`, mounted in `_topbar_pills.html`) now surfaces queued
  / problem counts app-wide and opens a dropdown that reuses the drawer for
  retry/discard, so a `failed`/`conflict` write-back is no longer visible only
  on its clip's draft page. It mirrors the connection chip (a `popover()`
  container with an hx-get'd inner partial), populates on load, refreshes every
  10s, and retry/discard return the chip partial on `HX-Request` so the panel
  updates with no reload. Counts come from `count_actionable`.
