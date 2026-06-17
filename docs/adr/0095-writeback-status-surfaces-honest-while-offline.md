# 0095. Write-back status surfaces stay honest (and quiet) while CatDV is offline

**Date:** 2026-06-17
**Status:** Accepted

## Context

CatDV's 2-seat license + VPN mean it disconnects often (see CLAUDE.md). While
it's offline the SyncEngine parks the write-back queue (`_tick` returns early
when `current_state() != online`), so accepted changes sit at `pending` with
`attempts=0` until it reconnects. Live operator QA surfaced a cluster of issues,
all variations on "the status surfaces don't tell the truth about the
offline/parked state, and they're noisy":

1. **A batch showed "Applied" while its writes were still queued.** `batch_view`
   reported *review* state (all clips reviewed → "Applied"), ignoring whether the
   write-back had actually landed — the same `applied_at ≠ on-server` gap ADR 0093
   fixed for the draft message, never applied to the batches table.
2. **The pending-writes drawer showed a bare, action-less "Queued"** with no hint
   that CatDV was offline or that the writes would send on reconnect.
3. **The topbar chips polled forever.** The sync chip polled every 10s even when
   idle; the connection chip polled every 5s while offline — faster than the
   monitor's own 30s probe, so it re-fetched the same cached state ~6× per change.
   Both ran indefinitely because CatDV never recovered, spamming the log.
4. **The sync chip flickered "✓ Synced" → "↑ N"** on every page load (the topbar
   paints before the async count fetch returns).
5. **The clips-list per-clip status pills didn't update while a batch ran** —
   the page is server-rendered and never refreshed.
6. **"Retry failed" left a stale "Failed"** until another interaction: the retry
   dispatches an async job that (over the VPN) takes seconds to flip statuses,
   and the page's 500ms post-retry refresh landed in that gap.

## Decision

- **Batch "Syncing N" status.** `list_batches` gains a `syncing` CTE counting a
  batch's clips that have an active row in `pending_operations`
  (status `pending`/`in_flight`) — the SAME source as the topbar sync chip
  (`count_actionable`), so the batch pill and the chip can never contradict each
  other (a "Syncing 1" batch with a "✓ Synced" chip). `batch_view` shows
  "Syncing N" (accent) after review is done but before the write-backs land, only
  then "Applied". Precedence: running → awaiting-review → syncing → applied.
  (An earlier draft sourced this from `review_items.synced_at`; that's unreliable
  for applies made before the column existed — it over-reported "Syncing" for
  historical rows whose ops had long drained — so it must NOT drive this.)
- **Offline drawer banner.** `/ui/sync-chip` passes an `offline` flag (cached
  monitor state, no CatDV round-trip); the drawer shows "CatDV is offline — these
  write-backs are waiting and will send automatically when it reconnects" instead
  of a bare "Queued".
- **Self-limiting / right-cadence polls.** The sync chip emits its 10s poller
  ONLY while `queued or problems` (idle = silent). The connection chip's
  offline/recovering poll is **30s** (matching `ConnectionMonitor.interval_s`),
  not 5s — polling faster than the cached state can change is wasted. 2s is kept
  only for the fast, locally-driven `vpn_connecting` state.
- **No chip flicker; chip is a pill.** The chip's counts are rendered INLINE on
  full-page loads via a Jinja context processor (`_topbar_sync_context`) doing a
  short synchronous `pending_operations` read — so it paints the real "↑ N" /
  "✓ Synced" immediately instead of flashing a placeholder while the load-fetch
  returns. (Synchronous read because a context processor can't await the async
  repo; WAL serves concurrent readers; gated to non-HX renders; bulletproof —
  any error → `{}` and the load-fetch fallback still runs.) The request spinner
  is hidden (`.sync-chip-trigger .htmx-indicator`, mirroring `.conn-pill`), and
  the trigger is a fully-rounded pill with a fixed min-width to match the
  neighbouring `.conn-pill`/`.env-pill` and avoid a width jump on count changes.
  The inner still keeps a `⋯` placeholder as a fallback for direct renders.
- **Clips list updates pills via OOB swaps while a batch runs.** When viewing a
  batch with any in-flight item, `_clips_tbody.html` emits a self-limiting
  `#bstatus-poll` (`every 4s`, `hx-swap="none"`) hitting `/ui/batch-statuses`,
  which returns one out-of-band `<span>` per clip patching just that clip's pill
  — NOT a `#clips-region` re-render, which would reset the table scroll and re-run
  the list's heavier queries each tick. Once the batch settles the endpoint
  OOB-replaces the poller with a triggerless span, so polling stops. DB-only.
- **Retry resets items synchronously.** `/batches/retry-failed` flips the
  targeted `error` items to `pending` (clearing `error_message`) BEFORE
  dispatching the async run, so `in_flight > 0` immediately and the next refresh
  reads as running. `run_job` re-processes `pending`/`error` alike, so what runs
  is unchanged.
- **"Open" opens the batch's clips.** The batches-table "Open" link targets
  `files_href` (the batch's full clip list, same as the row click), not
  `review_href` — which pointed at the `for_review` filter and was empty once a
  batch was fully reviewed (and at `/` for a just-started batch).

## Alternatives

- *Keep "Applied" + a small syncing badge* — rejected; the operator asked for the
  status itself to stop lying.
- *Drawer "Queued" with a Retry button when offline* — rejected; a blind retry
  can't send while offline. The honest move is to explain the auto-send, not
  offer a no-op action.
- *Poll the clips list via a per-row endpoint* — rejected; N pollers for a big
  batch. One self-limiting region poll off the cached list is cheaper and reuses
  the existing fragment path.

## Consequences

- Every status surface now reflects *server-confirmed* state, and the offline
  case is explained rather than silently parked. Nothing is lost — parked writes
  drain on reconnect (verified: 49 applied, 8 parked since a 15:25 disconnect).
- Idle/offline request volume drops sharply: the sync chip is silent when synced,
  the connection chip polls 6× less while offline.
- CSS/JS/template changes have no executing tests (Python-only stack, ADR 0001);
  the batch_view, list_batches, drawer-render, clips-list-poll, retry-pre-reset
  and chip-poll behaviours are all covered by unit/integration tests. Refines
  ADRs 0092/0093/0094.
