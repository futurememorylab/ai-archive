# Unify queue paradigm: annotation-batch runner as a DB-backed claim worker

**Issue:** [#100](https://github.com/futurememorylab/ai-archive/issues/100)
**Date:** 2026-06-24
**Status:** Design approved
**Tier:** Tier 2 follow-up to ADR 0115 (Tier 1: cancel-actually-cancels + shutdown drain, PR #101)
**Reference pattern:** `backend/app/services/media_prefetcher.py`

## Problem

The app has two queue systems on two different paradigms. The annotation/studio
batch runner is the weaker one:

| | Cache queue (#78) | Annotation batches (today) |
|---|---|---|
| Store | `prefetch_queue` table | `jobs` + `job_items` |
| Worker | lifespan-owned **claim worker** (`MediaPrefetcher._loop`, `claim_next()` CAS) | **in-memory fire-and-forget** — `asyncio.create_task(run_job)` spawned **from the HTTP route**, tracked in `CoreCtx._running_jobs` |
| Started by | lifespan | the request handler (`POST /api/jobs`, `POST /api/studio/runs`) |
| Crash recovery | `requeue_orphans()` (running → re-runnable) | `cancel_orphaned_running()` (running → cancelled, terminal) |
| Shutdown drain | `stop()`: signal → 5s wait → cancel | `drain_running_jobs()` (added Tier 1) |

The route owns execution, so orphan recovery cancels instead of resuming, and
the route↔worker coupling means `routes/` imports `annotator.run_job` and spawns
tasks directly.

## Goal

**Do not merge the queues.** Downloading bytes (`prefetch_queue`) vs running
Gemini (`jobs`) are genuinely different concerns and keep separate tables.
**Unify the *paradigm*:** move job execution to a lifespan-owned DB-backed claim
worker (`JobRunner`) that mirrors `MediaPrefetcher` — start / poll / claim CAS /
orphan-recover / stop. Routes become pure DB writers.

## Decisions (locked during brainstorming)

1. **Concurrency: single serial worker.** Exact `MediaPrefetcher` mirror — one
   job claimed and run at a time. Naturally throttles Gemini/CatDV-seat load.
   **Consequence:** a bulk `run_group` of N jobs that ran concurrently today now
   runs sequentially in creation order.
2. **Wake: poll only (no `notify`).** Pure `MediaPrefetcher` parity, zero
   route↔runner coupling. Use a short tick (`~0.75s`, configurable) so job start
   stays snappy. We consciously chose poll over the issue's suggested
   `notify()` because it fully decouples the route from the worker.
3. **Caching double-tracking: keep as-is, document.** `job_items.status` is the
   execution source of truth; the parallel `prefetch_queue` row from
   `start_inline()` is deliberate `/cache`-page visibility (ADR 0114), not debt.
   The worker-lifecycle change does not touch inline caching inside `run_job`.
4. **Orphan recovery: resume (requeue running → pending).** Replace
   `cancel_orphaned_running` with `requeue_orphaned_running`. `run_job` is
   idempotent (skips completed items, cache-status-first), so resume re-runs only
   unfinished work. Cost: one in-flight Gemini call may re-issue on resume.
5. **`auto_start` dropped.** Under a pure claim worker any `pending` job is
   claimed; the `auto_start=False` create-but-don't-run path is unused by every
   caller today (`bulkAnnotate.js`, `clipAnnotate.js`, `batches.html` all send
   `true`). Remove the field; `POST /api/jobs` returns `{"id", "queued": true}`.

## Architecture

```
POST /api/jobs ─────┐
POST /studio/runs ──┤── create_job(...) → jobs row status='pending' ──> [ DB ]
retry-failed ───────┘   (no task spawn, no _running_jobs)                 │
                                                                  poll ~0.75s
   JobRunner._loop  (LiveCtx, lifespan-owned, SINGLE serial worker)  <────┘
     claim_next_job() CAS  →  run_job(job_id)  →  loop
```

Routes insert a `pending` job and return. The worker polls, claims oldest-first
via CAS, runs `run_job`, repeats. 1:1 paradigm match with `MediaPrefetcher`.

## Components

### `backend/app/services/job_runner.py` (new)

Modeled on `media_prefetcher.py`:

- `__init__(*, jobs_repo, run_job_fn, db_provider, tick_interval_s)`.
  `run_job_fn: Callable[[int], Awaitable[None]]` is a closure built in
  `build_context` that binds the live services and calls `run_job(...)`. Keeping
  the executor injected makes `JobRunner` thin and unit-testable with a fake.
- `start()` → `await jobs_repo.requeue_orphaned_running(db)`, then create the
  `_loop()` task.
- `_loop()` → call `tick_once()`; if it ran a job, loop immediately to drain;
  else `await _stop_evt.wait()` with `tick_interval_s` timeout. Broad
  `except Exception` per iteration (not `BaseException` — preserve
  `CancelledError`) so one bad job never kills the loop.
- `tick_once()` → `job_id = await jobs_repo.claim_next_job(db)`; `None` → return
  `False`. Otherwise run it as an **inner task** the worker holds
  (`self._current = (job_id, asyncio.create_task(run_job_fn(job_id)))`), `await`
  it, clear `self._current`; return `True`.
- `cancel(job_id)` → if `self._current` matches `job_id`, `task.cancel()` —
  prompt interrupt of a long Gemini call (preserves Tier 1 promptness).
- `stop()` → set `_stop_evt`, await the loop ≤5s, then cancel; the current inner
  job task is drained here (replaces `drain_running_jobs`).

### `JobsRepo` additions (`repositories/jobs.py`)

- `claim_next_job(conn) -> int | None` — CAS, identical shape to
  `prefetch_queue.claim_next`:
  ```sql
  SELECT id FROM jobs WHERE status='pending' ORDER BY created_at ASC LIMIT 1;
  UPDATE jobs SET status='running', started_at=? WHERE id=? AND status='pending';
  ```
  Check `rowcount == 1` (lost race → `None`), re-read, return `job_id`.
- `requeue_orphaned_running(conn) -> int` — **replaces** `cancel_orphaned_running`:
  ```sql
  UPDATE job_items SET status='pending'
    WHERE status IN ('resolving','uploading','prompting')
      AND job_id IN (SELECT id FROM jobs WHERE status='running');
  UPDATE jobs SET status='pending', started_at=NULL WHERE status='running';
  ```
  Resetting stuck transient items is required: `run_job` only processes
  `pending`/`error` items, so an item left `prompting` at crash would otherwise
  be skipped on resume and stay stuck forever. Returns count of requeued jobs.

## Status lifecycle

`pending` → (worker CAS) → `running` → `completed` | `failed` | `cancelled`.

`run_job` keeps its existing `update_status(running)` (now idempotent after the
claim) and its per-item skip of non-`pending`/`error` items, so a requeued job
resumes by re-running only unfinished work.

## Cancellation (offline-safe, prompt)

`POST /{job_id}/cancel` keeps the two-step dance, retargeting step 2:

1. `core.jobs_repo.cancel_job(db, job_id)` — DB flip; **works offline** (CoreCtx).
   `claim_next_job` only takes `pending`, so a cancelled job is never claimed.
2. If `live_ctx` exists: `live.job_runner.cancel(job_id)` — interrupts the inner
   task iff it is the one running. Offline → nothing is running to interrupt.

## Removed / repurposed

- `CoreCtx._running_jobs` (and the `LiveCtx._running_jobs` delegator) — **deleted**.
  The worker owns the single in-flight task.
- `drain_running_jobs()` — **deleted** (subsumed by `JobRunner.stop()`).
- `_run_in_bg()` + `start_job_in_background()` in `routes/jobs.py` — **deleted**.
- `routes/studio.py::create_run` task spawn + its local `_run_in_bg` — **deleted**.
- `only_clip_ids` param on `run_job` — **deleted**. Retry-failed becomes: reset
  targeted `error` items → `pending`, set job → `pending`, return. The worker
  re-claims; `run_job` naturally touches only the reset items.
- `cancel_orphaned_running` — replaced by `requeue_orphaned_running`; the jobs
  line is removed from `run_startup_cleanup` (resume now happens in
  `JobRunner.start()`).
- `auto_start` field on `JobCreate`.

After this, `routes/` imports nothing executable from the annotator — only
`JOBS_TOPIC` (SSE) remains imported in `routes/jobs.py`. `routes/batches.py` no
longer imports from `routes/jobs.py`.

## Lifecycle wiring

`JobRunner` is built in `build_context()` on `LiveCtx`, beside `MediaPrefetcher`
(it needs the full live stack: archive, proxy_resolver, ai_store, gemini, repos).
`start()` runs in `main.py` lifespan startup (after `MediaPrefetcher.start()`);
`stop()` runs in `LiveCtx.aclose()` before `core.aclose()`.

**Consequence (accepted):** because requeue happens in `JobRunner.start()`
(faithful to `MediaPrefetcher`), a crash-orphaned `running` job displays as
`running` until the next *live* boot requeues it. Self-healing and acceptable;
recorded in the ADR. (Alternative considered: keep an offline-safe requeue in
`run_startup_cleanup` for display honesty — rejected to stay 1:1 with the
reference pattern.)

## Out of scope

Caching double-tracking (`start_inline` → `prefetch_queue` row) is unchanged and
documented in the ADR as deliberate `/cache` visibility (ADR 0114). The two
queues stay separate tables (the issue's explicit constraint).

## ADR + docs

New ADR **0116** (`Lifespan: Invariant`): *"All background work is a
lifespan-owned DB-backed claim worker; routes never spawn execution tasks."*
Add the line to `docs/architecture-invariants.md`. This spec is the
screen-level companion.

## Testing

- **Unit (`tests/unit/`):**
  - `JobRunner` claim→run→loop, and `stop()` drain, with a fake `run_job_fn`.
  - `claim_next_job` CAS — oldest-first; lost race (`rowcount != 1`) → `None`.
  - `requeue_orphaned_running` — resets running jobs **and** transient items
    (`resolving`/`uploading`/`prompting`) to `pending`; leaves terminal items.
  - Guard: a test asserting `routes/` contains no `asyncio.create_task` of jobs.
- **Integration (`tests/integration/`):**
  - `POST /api/jobs` inserts `pending`; worker claims and completes it.
  - Prompt cancel of a `running` job (DB flip + inner-task interrupt).
  - Boot-resume: seed an orphaned `running` job → `JobRunner.start()` → it
    completes.
  - `retry-failed` reset path re-runs only the targeted items.
- **Import-linter (`.importlinter`):** add a contract forbidding `routes` from
  importing `annotator.run_job`.
- **Walkthrough (`tests/walkthrough/`):** existing batch + studio run scenarios
  must still pass in assert mode (no UI change expected, but the run lifecycle
  changes underneath them).

## Manual acceptance flows

1. **Annotation batch runs via the worker (not the route).**
   Setup: app online (live stack wired), a prompt version, the Clips list at
   `/clips`. Actions: select ≥2 clips → bulk Annotate. Expected: the topbar
   job indicator shows the batch advancing through caching → annotating → done
   within ~1s of clicking (worker tick); the batch reaches `completed` and review
   items appear. (Proves route → `pending` → worker claim → completion.)

2. **Studio run launches the same way.**
   Setup: `/studio` with a prompt and a clip. Actions: click Run. Expected: the
   studio run row transitions pending → running → complete, output renders. No
   regression vs today.

3. **Prompt cancel during a long run.**
   Setup: start a multi-clip batch; while a clip is mid-`prompting`. Actions:
   click Cancel on the batch. Expected: the job flips to `cancelled` promptly
   (does not wait out the current Gemini call); no item is left stuck
   `prompting`; the topbar clears.

4. **Boot-time resume after a hard stop.**
   Setup: start a multi-clip batch; `kill -9` the server (or simulate by seeding
   a `running` job + a `prompting` item in the DB) so a job is orphaned `running`.
   Actions: start the server (online). Expected: on startup the orphaned job is
   requeued to `pending`, the worker re-claims it, already-`annotated` items are
   skipped, and only unfinished items re-run to completion.

5. **Retry failed clips.**
   Setup: a completed batch with ≥1 `error` item, at `/batches`. Actions: click
   Retry failed. Expected: only the failed clips flip to `pending` and re-run via
   the worker; already-succeeded clips are untouched; the batch returns to
   running then completes.

6. **Offline create resumes on reconnect.**
   Setup: app offline (no live ctx). Actions: create a job (e.g. via API) — it is
   created `pending` but nothing runs. Bring the app online (live boot).
   Expected: `JobRunner.start()` requeue + claim picks up the `pending` job and
   runs it to completion. (Proves "resume on boot for free".)

7. **Cache queue still works (adjacent-surface guard).**
   Setup: `/cache`. Actions: Cache a clip via the Cache button. Expected: the
   `prefetch_queue` worker (`MediaPrefetcher`) downloads it as before; an
   annotator run on an uncached clip still shows its inline caching row on
   `/cache` (double-tracking unchanged).
