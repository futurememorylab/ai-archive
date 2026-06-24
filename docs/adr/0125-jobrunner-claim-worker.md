# 0125. Annotation/studio jobs run on a lifespan-owned DB-backed claim worker

**Date:** 2026-06-24
**Status:** Accepted
**Lifespan:** Invariant

## Context

Two queues on two paradigms: the cache queue is a lifespan-owned claim worker
(`MediaPrefetcher`), the annotation/studio runner was a fire-and-forget
`asyncio.create_task(run_job)` spawned from the HTTP route and tracked in
`CoreCtx._running_jobs`. The route owned execution; orphan recovery cancelled
rather than resumed; routes imported `run_job`. Tier 1 (ADR 0115) added
cancel-promptness + a shutdown drain but kept the route-spawn shape. This is
the Tier 2 unification tracked in issue #100.

## Alternatives

- **Merge the two queues into one table.** Rejected — downloading bytes vs
  running Gemini are different concerns; the issue mandates separate tables.
- **Bounded-concurrent JobRunner (N workers).** Rejected — a single serial
  worker matches `MediaPrefetcher` exactly and throttles Gemini / CatDV-seat
  load; bulk `run_group`s serialize (accepted).
- **notify()/event wake.** Rejected — pure polling keeps zero route↔runner
  coupling; a short tick (`job_tick_interval_s = 0.75`) keeps job start snappy.

## Decision

Job execution moves to `JobRunner` (`services/job_runner.py`): a single,
lifespan-owned worker on `LiveCtx` that polls `jobs`, claims the oldest
`pending` job via CAS (`JobsRepo.claim_next_job`), runs `run_job`, and loops.
Routes only insert a `pending` row (and, for cancel, call `job_runner.cancel`).
Orphan recovery **resumes**: `requeue_orphaned_running` flips `running` →
`pending` and resets stuck transient items, run on `JobRunner.start()`.
`CoreCtx._running_jobs`, `drain_running_jobs`, `cancel_orphaned_running`,
`only_clip_ids`, and `auto_start` are removed. The caching double-tracking
(`start_inline` → `prefetch_queue` row, ADR 0114) is unchanged — deliberate
`/cache` visibility.

The calibration sweep (ADR 0116) previously spawned jobs with `force_resolution`
/ `record_only` arguments that lived only in the request. Because the single
worker runs every job the same way, those two parameters are now persisted on
the `jobs` row (migration `0029`) and read back per-job by the worker, so a
calibration job runs identically to the old route-spawn path while its route
stays a pure DB writer.

## Consequences

- Boot-time resume of unfinished jobs falls out for free; `run_job`
  idempotency means only undone items re-run (one in-flight Gemini call may
  re-issue).
- Bulk `run_group`s run sequentially, not concurrently.
- An offline boot leaves crash-orphaned `running` jobs displayed as `running`
  until the next *live* boot requeues them (self-healing).
- Routes import nothing executable from the annotator. Enforced by
  `tests/unit/test_jobs_no_route_spawn.py`.
- The job model carries `force_resolution` / `record_only`; any new per-run
  parameter follows the same persist-on-the-row pattern rather than a new
  spawn path.
