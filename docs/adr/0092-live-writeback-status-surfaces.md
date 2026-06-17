# 0092. Live write-back status surfaces: topbar sync chip, clip-grouped drawer, transient-only connection poll

**Date:** 2026-06-17
**Status:** Accepted

## Context

ADR 0091 made the write-back drawer + per-clip draft poller *correct and
legible* but noted the drawer wasn't mounted anywhere live. Operator feedback
while testing then drove four concrete changes:

1. The pending-writes drawer wasn't reachable in the running app at all.
2. The drawer (7 per-op columns, one row per ChangeOp) was "super confusing" —
   a single clip with a note + markers showed as several rows.
3. No bulk "Retry all" for stuck writes.
4. After a heavy batch the connection chip showed a stale "Disconnected" until
   the user navigated to another page (then it "reconnected").

## Alternatives

- **Mount the drawer as its own panel / page.** Rejected — it belongs next to
  the other topbar indicators; reused the connection-chip pattern instead.
- **Trim columns but keep per-op rows.** Considered for the drawer; rejected in
  favour of grouping by clip, which matches how operators think and removes the
  most rows. (Offered both to the user; they picked grouping.)
- **Poll the connection chip continuously.** Rejected — a constant innerHTML
  swap was the original flicker source (`test_stable_chip_does_not_poll`).
- **Push connection state to the chip via the EventBus "connection" SSE topic.**
  Deferred — more moving parts than the bug needs; a scoped poll suffices.

## Decision

- **Topbar sync chip** (`_sync_chip.html` / `_sync_chip_inner.html` in
  `_topbar_pills.html`), mirroring the connection chip: a stable `#sync-chip`
  owns `popover()`; the pill is **always visible** (⚠ problems / ↑ queued, or a
  muted "✓ Synced" when idle) and opens a `.popover-panel` containing the
  drawer. Counts from `PendingOperationsRepo.count_actionable`.
- **Drawer grouped by clip**: one row per clip — combined change kinds (Note,
  Markers, …), the worst status across its ops (severity
  conflict > failed > in_flight > pending), and per-clip actions. New repo
  methods `reset_clip_for_retry` / `delete_clip_pending` and routes
  `POST /api/sync/clip/{provider_id}/{provider_clip_id}/{retry,discard}`. The
  grouping is done in the template via `groupby`, so all render sites keep
  passing `rows`.
- **Retry all**: header button → `POST /api/sync/retry-all` →
  `reset_all_for_retry` (resets every failed/conflict op to pending), shown only
  when there are failed/conflict rows.
- **Connection chip polls only in transient/recovering states** — `2s` while
  the VPN is coming up, `5s` while CatDV is offline/disconnected *but the VPN is
  up* (the case the monitor auto-recovers from in manual mode). It does NOT poll
  when online, VPN-off, or forced-offline, preserving the no-flicker intent.
- All write actions (`retry`, `discard`, `retry-all`, per-clip) return the
  refreshed `_sync_chip_inner.html` on `HX-Request` (`_chip_or_json`) so panels
  update in place — no `location.reload`.

## Consequences

- A `failed`/`conflict` write-back is now visible app-wide and actionable, and
  the chip is glanceable at all times.
- The drawer reads as a clip list, not an op list.
- A transient post-batch disconnect self-corrects within ~5s without a
  navigation; a stable chip still does no background polling.
- Per-op retry/discard routes remain for the API/tests but are no longer used by
  the UI (the drawer is clip-grouped).
- **Embedded-context isolation (follow-up fix):** because the chip renders on
  every page, the drawer/chip read **namespaced** context (`sync_rows` /
  `sync_counts`), never a generic `rows`/`counts` — a page with its own `rows`
  (e.g. `/cache`) otherwise fed the group-by-clip `groupby` and 500'd the page.
  Undefined → empty/idle; the `/ui/sync-chip` poll (and an apply-time nudge)
  supply the real data.
