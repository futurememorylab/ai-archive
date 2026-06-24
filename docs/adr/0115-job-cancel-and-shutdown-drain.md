# 0115. Annotation/studio jobs: cancel-actually-cancels + shutdown drain

**Date:** 2026-06-24
**Status:** Accepted

## Context

The app has two queue systems. The **cache queue** (`MediaPrefetcher`,
`prefetch_queue`) is a DB-backed claim worker started in the lifespan, with
orphan recovery (`requeue_orphans`) and a graceful `stop()` (signal → 5s
wait → `task.cancel()`). The **annotation/studio batch runner** is different:
`run_job` is spawned as a fire-and-forget `asyncio.create_task` *from the HTTP
route* (`POST /api/jobs`, `POST /api/studio/runs`) and tracked in
`CoreCtx._running_jobs[job_id]`.

A review of the batch runner found its queue *management* was incomplete — the
happy-path progress UI (SSE, phase counts, Batches hub) was rich, but cancel
and shutdown were left as DB-status bookkeeping:

1. **Cancel didn't cancel.** `POST /api/jobs/{id}/cancel` only flipped the job
   row to `cancelled`; `run_job` noticed it cooperatively *before the next
   item*. The in-flight item — including the long `asyncio.to_thread(gemini
   .annotate, …)` — ran to completion. The handle that could interrupt it,
   `_running_jobs[job_id]`, was tracked but **never `.cancel()`'d anywhere**.

2. **No shutdown drain.** `LiveCtx.aclose()` stopped every named service but
   never touched `_running_jobs`, then closed the DB. On SIGTERM an in-flight
   job was abandoned with the connection closing under it. These tasks are NOT
   request-scoped, so uvicorn's connection draining does not cover them. State
   was only made consistent on the *next* boot by `cancel_orphaned_running`,
   and the completed-but-unpersisted Gemini work (already paid for) was lost.

The full unification of the two paradigms (make the batch runner a DB-backed
claim worker like `MediaPrefetcher`) is tracked separately as issue #100 — a
larger change needing its own design. This ADR is the bounded correctness fix
(the "Tier 1" of that review).

## Alternatives

- **Cooperative cancel only, finer-grained.** Thread the job's cancel status
  into `_process_item` and check between resolve/upload/prompt. Rejected: the
  Gemini call is the long pole and runs in a worker thread that can't be
  cooperatively interrupted, so latency stays ~one clip. `task.cancel()`
  interrupts the `await` promptly; that's the right tool.
- **Rely on next-boot orphan recovery for shutdown.** Already the status quo;
  it leaves the abandoned-mid-run window and silently discards finished work.
- **Cancel the task without reconciling the DB first.** Rejected: a race
  leaves an item in a transient state (`prompting`) forever — `cancel_orphaned
  _running` won't catch it because the job is `cancelled`, not `running`.

## Decision

- Add `JobsRepo.cancel_job(conn, job_id)`: in one commit, flip the job AND all
  its still-in-flight items (pending + transient) to `cancelled`, leaving
  terminal items (done/review_ready/applied/rejected/error/cancelled)
  untouched. Same invariant as `cancel_orphaned_running`, scoped to one job.
  **Idempotent**, so it can be re-run to mop up a racing transient write.
- The cancel route reconciles via `cancel_job`, then `task.cancel()`s the
  tracked task.
- Both `_run_in_bg` wrappers (jobs + studio) catch `asyncio.CancelledError`,
  re-run `cancel_job` to reconcile any item that raced into a transient state
  after the route's first sweep, then re-raise so the task ends cancelled.
- Add `drain_running_jobs(running, *, timeout=5.0)` (cancel all + bounded
  `asyncio.wait`), called in `LiveCtx.aclose()` **before** `core.aclose()` so
  the tasks' `CancelledError` reconcile handlers run while the DB is still
  open. Mirrors `MediaPrefetcher.stop()`.

## Consequences

- Cancel is prompt (interrupts the in-flight `await`) and leaves consistent
  DB state immediately, not on next boot.
- Shutdown no longer abandons in-flight jobs; their item state is reconciled
  before the connection closes.
- The cooperative per-item check in `run_job` stays — it's the clean stop for a
  cancel that lands between items, complementary to `task.cancel()`.
- `_run_in_bg` is still duplicated across `routes/jobs.py` and
  `routes/studio.py` (now with identical cancel handling). Deduplication folds
  naturally into the issue #100 unification and was left out of scope here.
- This is a stop-gap; the durable fix is the DB-backed claim-worker paradigm
  (issue #100), after which `_running_jobs` and route-spawned tasks go away.
