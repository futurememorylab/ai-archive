# Unify Queue Paradigm: JobRunner DB-Backed Claim Worker — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the route-spawned `asyncio.create_task(run_job)` annotation/studio runner with a single, lifespan-owned, DB-backed claim worker (`JobRunner`) that mirrors `MediaPrefetcher`.

**Architecture:** Routes become pure DB writers — they insert a `pending` `jobs` row and return. A lifespan-owned `JobRunner` polls `jobs`, claims the oldest `pending` job via a compare-and-swap (CAS), runs it through the existing `run_job`, and loops. Orphaned `running` jobs are **requeued** (resumed) on worker start, not cancelled. The cancel route flips DB state (offline-safe) and asks the worker to interrupt the in-flight job if it is the one running.

**Tech Stack:** Python 3.13, FastAPI, `aiosqlite`, pytest (`-n auto` only for full sweeps), import-linter.

**Spec:** `docs/superpowers/specs/2026-06-24-unify-queue-paradigm-job-runner-design.md`
**Reference pattern:** `backend/app/services/media_prefetcher.py`, `backend/app/repositories/prefetch_queue.py`

## Global Constraints

- **Python venv:** always `.venv/bin/python` (never system python).
- **In-loop tests:** run only the touched file/dir; **never** `-n auto` for a single file. Full sweep before final commit: `.venv/bin/python -m pytest -n auto -q`.
- **No `BaseException` catches** — catch `Exception` (preserve `asyncio.CancelledError`).
- **No sync fs I/O in `async def`** (none introduced here, but don't add any).
- **Single serial worker** — one job at a time, by construction. Do **not** add a concurrency/semaphore knob.
- **Two contexts:** `CoreCtx` (always present, offline-safe) and `LiveCtx` (live stack). `JobRunner` lives on `LiveCtx`. Do not add `Optional` service fields to `CoreCtx` or re-introduce late-binding.
- **Status vocabulary (unchanged):** `JobStatus = pending|running|completed|failed|cancelled`; transient `ItemStatus` = `resolving|uploading|prompting` (constant `TRANSIENT_STATUSES` in `repositories/jobs.py`).
- **Caching double-tracking stays as-is** (`start_inline` → `prefetch_queue` row is deliberate `/cache` visibility, ADR 0114). Out of scope.

---

### Task 1: `JobsRepo.claim_next_job` — CAS claim

**Files:**
- Modify: `backend/app/repositories/jobs.py` (add method after `create_job`)
- Test: `tests/unit/test_jobs_claim_worker.py` (create)

**Interfaces:**
- Produces: `JobsRepo.claim_next_job(conn) -> int | None` — atomically takes the oldest `pending` job, flips it to `running` (sets `started_at`), returns its id; `None` if no pending job or the race was lost.
- Consumes: existing `create_job`, `get_job`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_jobs_claim_worker.py
import pytest

from backend.app.repositories.jobs import JobsRepo
from tests._helpers.db import make_test_db  # existing helper; see any test in tests/unit that opens a db


@pytest.mark.asyncio
async def test_claim_next_job_takes_oldest_pending_and_marks_running():
    async with make_test_db() as conn:
        repo = JobsRepo()
        first = await repo.create_job(conn, prompt_version_id=1, clip_ids=[10])
        second = await repo.create_job(conn, prompt_version_id=1, clip_ids=[11])

        claimed = await repo.claim_next_job(conn)
        assert claimed == first  # oldest first

        job = await repo.get_job(conn, first)
        assert job.status == "running"
        # second is still pending
        assert (await repo.get_job(conn, second)).status == "pending"


@pytest.mark.asyncio
async def test_claim_next_job_returns_none_when_no_pending():
    async with make_test_db() as conn:
        repo = JobsRepo()
        assert await repo.claim_next_job(conn) is None


@pytest.mark.asyncio
async def test_claim_next_job_skips_non_pending():
    async with make_test_db() as conn:
        repo = JobsRepo()
        jid = await repo.create_job(conn, prompt_version_id=1, clip_ids=[10])
        await repo.update_status(conn, jid, "running")  # already running
        assert await repo.claim_next_job(conn) is None
```

> NOTE: confirm the db fixture/helper name used by neighbouring unit tests (e.g. `tests/unit/` conftest fixtures). If tests use a `conn` fixture rather than `make_test_db()`, adapt the harness — the assertions are what matter. Verify with: `grep -rln "create_job\|aiosqlite" tests/unit | head` and copy that file's setup.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_jobs_claim_worker.py -q`
Expected: FAIL — `AttributeError: 'JobsRepo' object has no attribute 'claim_next_job'`

- [ ] **Step 3: Write minimal implementation**

Add to `backend/app/repositories/jobs.py` (mirrors `prefetch_queue.claim_next`):

```python
    async def claim_next_job(self, conn: aiosqlite.Connection) -> int | None:
        """Atomically take the oldest `pending` job and mark it `running`.

        CAS, same shape as PrefetchQueueRepo.claim_next: SELECT the oldest
        pending id, then UPDATE guarded on status='pending'. If rowcount != 1
        another claim won the race (cannot happen with the single JobRunner,
        but kept for correctness), so return None. Returns the claimed job id
        or None when the queue is empty."""
        cur = await conn.execute(
            "SELECT id FROM jobs WHERE status = 'pending' ORDER BY created_at ASC, id ASC LIMIT 1"
        )
        row = await cur.fetchone()
        if row is None:
            return None
        jid = int(row[0])
        upd = await conn.execute(
            "UPDATE jobs SET status = 'running', started_at = COALESCE(started_at, ?) "
            " WHERE id = ? AND status = 'pending'",
            (_now_iso(), jid),
        )
        await conn.commit()
        if upd.rowcount != 1:
            return None
        return jid
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/test_jobs_claim_worker.py -q`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/app/repositories/jobs.py tests/unit/test_jobs_claim_worker.py
git commit -m "feat(#100): JobsRepo.claim_next_job CAS claim"
```

---

### Task 2: `JobsRepo.requeue_orphaned_running` — resume on boot

**Files:**
- Modify: `backend/app/repositories/jobs.py` (add method; do NOT delete `cancel_orphaned_running` yet — Task 5 removes it with its caller)
- Test: `tests/unit/test_jobs_claim_worker.py` (extend)

**Interfaces:**
- Produces: `JobsRepo.requeue_orphaned_running(conn) -> int` — flips every `running` job back to `pending` (clears `started_at`) and resets its stuck transient items (`resolving|uploading|prompting`) to `pending`. Returns the count of jobs requeued.
- Consumes: existing `TRANSIENT_STATUSES`, `create_job`, `update_status`, `update_item_status`, `list_items`.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/unit/test_jobs_claim_worker.py

@pytest.mark.asyncio
async def test_requeue_orphaned_running_resumes_jobs_and_resets_transient_items():
    async with make_test_db() as conn:
        repo = JobsRepo()
        jid = await repo.create_job(conn, prompt_version_id=1, clip_ids=[10, 11, 12])
        await repo.update_status(conn, jid, "running")
        items = await repo.list_items(conn, jid)
        # one done, one mid-prompting (orphaned transient), one still pending
        await repo.update_item_status(conn, items[0].id, "annotated")
        await repo.update_item_status(conn, items[1].id, "prompting")
        # items[2] stays pending

        n = await repo.requeue_orphaned_running(conn)
        assert n == 1

        assert (await repo.get_job(conn, jid)).status == "pending"
        after = {i.catdv_clip_id: i.status for i in await repo.list_items(conn, jid)}
        assert after[10] == "annotated"   # terminal item untouched
        assert after[11] == "pending"     # transient reset so run_job re-runs it
        assert after[12] == "pending"


@pytest.mark.asyncio
async def test_requeue_orphaned_running_ignores_terminal_jobs():
    async with make_test_db() as conn:
        repo = JobsRepo()
        jid = await repo.create_job(conn, prompt_version_id=1, clip_ids=[10])
        await repo.update_status(conn, jid, "completed")
        assert await repo.requeue_orphaned_running(conn) == 0
        assert (await repo.get_job(conn, jid)).status == "completed"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_jobs_claim_worker.py -q`
Expected: FAIL — `AttributeError: ... 'requeue_orphaned_running'`

- [ ] **Step 3: Write minimal implementation**

Add to `backend/app/repositories/jobs.py`:

```python
    async def requeue_orphaned_running(self, conn: aiosqlite.Connection) -> int:
        """Resume jobs left 'running' by a killed worker. Mirrors the
        prefetcher's requeue_orphans (running -> re-runnable), the opposite of
        the old cancel_orphaned_running. A job runs only inside the single
        JobRunner; at boot nothing is in-flight, so any 'running' job is an
        orphan from a crash/restart/dev-reload. Flip it back to 'pending' so
        the worker re-claims it, and reset its stuck transient items
        (resolving/uploading/prompting) to 'pending' too — run_job only
        processes pending/error items, so a transient item would otherwise be
        skipped on resume and hang forever. Terminal items (done/annotated/
        review_ready/applied/rejected/error/cancelled) are left as-is, so
        resume re-runs only unfinished work. Returns the count of jobs
        requeued."""
        placeholders = ",".join("?" * len(TRANSIENT_STATUSES))
        await conn.execute(
            f"UPDATE job_items SET status = 'pending' "
            f" WHERE status IN ({placeholders}) "
            f"   AND job_id IN (SELECT id FROM jobs WHERE status = 'running')",
            TRANSIENT_STATUSES,
        )
        cur = await conn.execute(
            "UPDATE jobs SET status = 'pending', started_at = NULL WHERE status = 'running'"
        )
        await conn.commit()
        return cur.rowcount or 0
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/test_jobs_claim_worker.py -q`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/app/repositories/jobs.py tests/unit/test_jobs_claim_worker.py
git commit -m "feat(#100): JobsRepo.requeue_orphaned_running (resume on boot)"
```

---

### Task 3: `JobRunner` service

**Files:**
- Create: `backend/app/services/job_runner.py`
- Test: `tests/unit/test_job_runner.py` (create)

**Interfaces:**
- Produces:
  - `JobRunner(*, jobs_repo: JobsRepo, run_job_fn: Callable[[int], Awaitable[None]], db_provider: Callable[[], aiosqlite.Connection], tick_interval_s: float = 0.75)`
  - `await start()` — requeue orphans, launch `_loop()`.
  - `await stop()` — signal, await ≤5s, cancel; drains the current in-flight job.
  - `await tick_once() -> int | None` — claim+run one job; returns job id or `None`.
  - `cancel(job_id: int) -> None` — interrupt the in-flight job iff it is the one running.
- Consumes: `JobsRepo.claim_next_job`, `JobsRepo.requeue_orphaned_running` (Tasks 1–2).

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_job_runner.py
import asyncio

import pytest

from backend.app.services.job_runner import JobRunner


class _FakeRepo:
    """Minimal stand-in: a list of pending job ids + recorded requeue calls."""

    def __init__(self, pending):
        self.pending = list(pending)
        self.requeued = 0
        self.running = None

    async def claim_next_job(self, conn):
        if not self.pending:
            return None
        self.running = self.pending.pop(0)
        return self.running

    async def requeue_orphaned_running(self, conn):
        self.requeued += 1
        return 0


@pytest.mark.asyncio
async def test_tick_once_claims_and_runs_one_job():
    repo = _FakeRepo([7])
    ran = []

    async def run_job_fn(job_id):
        ran.append(job_id)

    runner = JobRunner(jobs_repo=repo, run_job_fn=run_job_fn, db_provider=lambda: None)
    result = await runner.tick_once()
    assert result == 7
    assert ran == [7]
    # empty queue -> None
    assert await runner.tick_once() is None


@pytest.mark.asyncio
async def test_tick_once_swallows_job_error_and_keeps_going():
    repo = _FakeRepo([1, 2])

    async def run_job_fn(job_id):
        if job_id == 1:
            raise RuntimeError("boom")

    runner = JobRunner(jobs_repo=repo, run_job_fn=run_job_fn, db_provider=lambda: None)
    assert await runner.tick_once() == 1   # error is logged, not raised
    assert await runner.tick_once() == 2


@pytest.mark.asyncio
async def test_start_requeues_orphans_then_drains_queue():
    repo = _FakeRepo([1, 2])
    ran = []

    async def run_job_fn(job_id):
        ran.append(job_id)

    runner = JobRunner(jobs_repo=repo, run_job_fn=run_job_fn, db_provider=lambda: None,
                       tick_interval_s=0.01)
    await runner.start()
    # let the loop drain the queue
    for _ in range(50):
        if ran == [1, 2]:
            break
        await asyncio.sleep(0.01)
    await runner.stop()
    assert repo.requeued == 1
    assert ran == [1, 2]


@pytest.mark.asyncio
async def test_cancel_interrupts_only_the_current_job():
    repo = _FakeRepo([5])
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def run_job_fn(job_id):
        started.set()
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    runner = JobRunner(jobs_repo=repo, run_job_fn=run_job_fn, db_provider=lambda: None,
                       tick_interval_s=0.01)
    await runner.start()
    await asyncio.wait_for(started.wait(), timeout=1.0)
    runner.cancel(999)         # not the current job -> no-op
    assert not cancelled.is_set()
    runner.cancel(5)           # the current job -> interrupted
    await asyncio.wait_for(cancelled.wait(), timeout=1.0)
    await runner.stop()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_job_runner.py -q`
Expected: FAIL — `ModuleNotFoundError: backend.app.services.job_runner`

- [ ] **Step 3: Write minimal implementation**

Create `backend/app/services/job_runner.py` (mirrors `media_prefetcher.py` lifecycle):

```python
"""JobRunner: one-at-a-time, lifespan-owned annotation/studio job worker.

The queue paradigm twin of MediaPrefetcher (services/media_prefetcher.py).
Routes insert a `pending` jobs row and return; this worker claims the oldest
pending job via CAS (JobsRepo.claim_next_job), runs it through run_job, and
loops. One job runs at a time, by construction (a single coroutine + sequential
tick_once calls) — do NOT add a concurrency knob; that would be a new service.

Orphan recovery RESUMES (requeue_orphaned_running: running -> pending) rather
than cancelling, so a crash/restart re-runs unfinished work for free. run_job
is idempotent (it skips already-finished items), so resume only re-runs what
was left undone. See docs/adr/0116-*.md.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

import aiosqlite

from backend.app.repositories.jobs import JobsRepo
from backend.app.services.errors import humanise

log = logging.getLogger(__name__)


class JobRunner:
    def __init__(
        self,
        *,
        jobs_repo: JobsRepo,
        run_job_fn: Callable[[int], Awaitable[None]],
        db_provider: Callable[[], aiosqlite.Connection],
        tick_interval_s: float = 0.75,
    ) -> None:
        self._jobs = jobs_repo
        self._run_job_fn = run_job_fn
        self._db_provider = db_provider
        self._tick_interval_s = tick_interval_s
        self._stop_evt: asyncio.Event = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._current: tuple[int, asyncio.Task] | None = None

    # --- lifecycle ---------------------------------------------------

    async def start(self) -> None:
        if self._task is not None:
            return
        try:
            requeued = await self._jobs.requeue_orphaned_running(self._db_provider())
            if requeued:
                log.info("job_runner requeued %d orphaned running job(s) on start", requeued)
        except Exception:  # noqa: BLE001 -- recovery must not block startup
            log.exception("job_runner orphan recovery failed")
        self._stop_evt.clear()
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task is None:
            return
        self._stop_evt.set()
        # Interrupt the in-flight job so shutdown does not wait out a long
        # Gemini call; its CancelledError handler reconciles DB state.
        if self._current is not None:
            _, cur_task = self._current
            cur_task.cancel()
        try:
            await asyncio.wait_for(self._task, timeout=5.0)
        except TimeoutError:
            self._task.cancel()
        finally:
            self._task = None

    async def _loop(self) -> None:
        while not self._stop_evt.is_set():
            try:
                processed = await self.tick_once()
            except Exception:  # noqa: BLE001 -- loop must not die
                log.exception("job_runner tick failed")
                processed = None
            if processed is None:
                try:
                    await asyncio.wait_for(
                        self._stop_evt.wait(),
                        timeout=self._tick_interval_s,
                    )
                except TimeoutError:
                    pass
            # If we processed a job, loop immediately to drain.

    # --- single tick -------------------------------------------------

    async def tick_once(self) -> int | None:
        """Claim and run the next pending job, if any. Returns the job id that
        was run, or None when the queue was empty."""
        db = self._db_provider()
        job_id = await self._jobs.claim_next_job(db)
        if job_id is None:
            return None
        task = asyncio.create_task(self._run_job_fn(job_id))
        self._current = (job_id, task)
        try:
            await task
        except asyncio.CancelledError:
            # Cancelled via cancel()/stop(): run_job_fn's own handler already
            # reconciled the job's item state. Swallow here so the loop lives.
            log.info("job %s cancelled mid-run", job_id)
        except Exception as exc:  # noqa: BLE001 -- one bad job must not kill the loop
            log.warning("job %s failed: %s", job_id, humanise(exc), exc_info=True)
        finally:
            self._current = None
        return job_id

    def cancel(self, job_id: int) -> None:
        """Interrupt the in-flight job iff it is the one currently running.
        No-op otherwise (a pending/terminal job needs only the DB flip the
        cancel route already did)."""
        if self._current is not None and self._current[0] == job_id:
            self._current[1].cancel()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/test_job_runner.py -q`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/job_runner.py tests/unit/test_job_runner.py
git commit -m "feat(#100): JobRunner claim worker (mirrors MediaPrefetcher)"
```

---

### Task 4: Simplify `run_job` — drop `only_clip_ids`

**Files:**
- Modify: `backend/app/services/annotator.py` (signature line 213; skip block lines 235-236)
- Test: existing annotator tests must stay green (`tests/integration` + any `tests/unit` touching run_job)

**Interfaces:**
- Produces: `run_job(...)` without the `only_clip_ids` parameter. Retry now works by resetting items → `pending` (Task 6), which `run_job` already honours via its `item.status not in ("pending", "error")` skip.

- [ ] **Step 1: Find current callers/tests of `only_clip_ids`**

Run: `grep -rn "only_clip_ids" backend tests`
Expected: matches in `annotator.py`, `routes/jobs.py`, `routes/batches.py` (those routes are rewritten in Task 6; if a test passes `only_clip_ids`, note it for update here).

- [ ] **Step 2: Remove the parameter and the skip block**

In `backend/app/services/annotator.py`, delete the parameter (line ~213):

```python
    prefetch_queue_repo: PrefetchQueueRepo | None = None,
    only_clip_ids: set[int] | None = None,   # <-- DELETE THIS LINE
) -> None:
```

And delete the per-item skip (lines ~235-236):

```python
        if only_clip_ids is not None and item.catdv_clip_id not in only_clip_ids:
            continue
```

(Leave the `item.status not in ("pending", "error"): continue` skip directly above it — that is what makes a reset-to-pending retry touch only the targeted items.)

- [ ] **Step 3: Run the annotator tests**

Run: `.venv/bin/python -m pytest tests/integration -q -k "job or annotat or studio"`
Expected: PASS (any test that previously passed `only_clip_ids=` must be updated to drop it — they should still pass logically since reset-to-pending is the new mechanism).

- [ ] **Step 4: Commit**

```bash
git add backend/app/services/annotator.py tests/
git commit -m "refactor(#100): drop only_clip_ids from run_job (retry resets items to pending)"
```

---

### Task 5: Wire `JobRunner` into context + lifespan; remove `_running_jobs`/`drain_running_jobs`/`cancel_orphaned_running`

**Files:**
- Modify: `backend/app/context.py` (build `JobRunner`, add `LiveCtx.job_runner` field, construct it, replace drain in `aclose`, delete `drain_running_jobs` + `_running_jobs` field + `_running_jobs` delegator)
- Modify: `backend/app/main.py` (start `JobRunner` in lifespan)
- Modify: `backend/app/startup.py` (remove the `cancel_orphaned_running` call + docstring lines)
- Modify: `backend/app/repositories/jobs.py` (delete `cancel_orphaned_running` — now unused)
- Test: `tests/integration/test_job_runner_lifecycle.py` (create — boot resume + worker run)

**Interfaces:**
- Produces: `LiveCtx.job_runner: JobRunner | None`. Built whenever the live stack needed by `run_job` is present (same gate the old auto-start used: `proxy_resolver is not None`). The `run_job_fn` closure binds the live services exactly as the old `_run_in_bg` did.

- [ ] **Step 1: Write the failing integration test**

```python
# tests/integration/test_job_runner_lifecycle.py
import pytest

# Use the app's existing integration harness (TestClient / live app fixture).
# Pattern-match an existing tests/integration test that builds a context with a
# fake live stack (search: grep -rln "build_context\|run_job\|JobRunner" tests/integration).


@pytest.mark.asyncio
async def test_boot_resume_requeues_and_runs_orphan(make_live_app):
    """A job left 'running' before boot is requeued and run to completion."""
    app = make_live_app(fake_run_job=True)  # adapt to the real harness factory
    # seed an orphaned running job directly in the DB
    core = app.state.live_ctx.core
    jid = await core.jobs_repo.create_job(core.db, prompt_version_id=1, clip_ids=[10])
    await core.jobs_repo.update_status(core.db, jid, "running")

    await app.state.live_ctx.job_runner.start()
    # tick the worker until it finishes
    ...
    assert (await core.jobs_repo.get_job(core.db, jid)).status in ("completed", "failed")
```

> NOTE: this task's test must use the repo's real integration harness. If no factory like `make_live_app` exists, write the test against `JobRunner.tick_once()` driven directly with a fake `run_job_fn`, plus a separate assertion that `build_context` populated `live.job_runner`. The non-negotiable assertions: (a) `build_context` sets `live.job_runner` when `proxy_resolver` is present; (b) a seeded orphaned `running` job is requeued by `job_runner.start()`.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/integration/test_job_runner_lifecycle.py -q`
Expected: FAIL — `AttributeError: 'LiveCtx' object has no attribute 'job_runner'`

- [ ] **Step 3a: Build the closure + runner in `context.py`**

In `backend/app/context.py`, after the `media_prefetcher = ...` block (around line 873), add:

```python
    from backend.app.services.annotator import run_job
    from backend.app.services.job_runner import JobRunner

    job_runner: JobRunner | None = None
    if arch.proxy_resolver is not None:

        async def _run_one_job(job_id: int) -> None:
            try:
                await run_job(
                    db=core.db,
                    job_id=job_id,
                    archive=arch.archive,
                    proxy_resolver=arch.proxy_resolver,
                    ai_store=arch.ai_store,
                    gemini=arch.gemini,
                    event_bus=core.event_bus,
                    annotations_repo=core.annotations_repo,
                    review_items_repo=core.review_items_repo,
                    jobs_repo=core.jobs_repo,
                    prompts_repo=core.prompts_repo,
                    studio_runs_repo=core.studio_runs_repo,
                    uploaded_clips_repo=core.uploaded_clips_repo,
                    run_telemetry_repo=core.run_telemetry_repo,
                    telemetry_ctx=core.telemetry_ctx,
                    prefetch_queue_repo=core.prefetch_queue_repo,
                )
            except asyncio.CancelledError:
                # Cancelled by JobRunner.cancel()/stop(): reconcile item state
                # (nothing left 'prompting') before propagating. Idempotent.
                with contextlib.suppress(Exception):
                    await core.jobs_repo.cancel_job(core.db, job_id)
                raise

        job_runner = JobRunner(
            jobs_repo=core.jobs_repo,
            run_job_fn=_run_one_job,
            db_provider=lambda: core.db,
            tick_interval_s=float(settings.job_tick_interval_s),
        )
```

Ensure `import asyncio` and `import contextlib` are present at the top of `context.py` (add if missing).

Add the setting in `backend/app/settings.py` near `prefetch_tick_interval_s` (line 136):

```python
    job_tick_interval_s: float = 0.75
```

- [ ] **Step 3b: Add the `LiveCtx.job_runner` field and pass it in**

In `LiveCtx` field block (near line 283, beside `media_prefetcher`):

```python
    media_prefetcher: MediaPrefetcher | None = None
    job_runner: "JobRunner | None" = None
```

Add `from backend.app.services.job_runner import JobRunner` to the imports used for type hints (or use a string annotation as above). In the `return LiveCtx(...)` block (line 935), add:

```python
        media_prefetcher=media_prefetcher,
        job_runner=job_runner,
```

- [ ] **Step 3c: Start it in the lifespan**

In `backend/app/main.py` lifespan, after the media_prefetcher start (lines 95-96):

```python
        if live.media_prefetcher is not None:
            await live.media_prefetcher.start()
        if live.job_runner is not None:
            await live.job_runner.start()
```

- [ ] **Step 3d: Stop it in `aclose`, delete the drain**

In `LiveCtx.aclose` (around line 435), add the stop near the media_prefetcher stop and **replace** the `drain_running_jobs` call:

```python
        if self.media_prefetcher is not None:
            await self.media_prefetcher.stop()
        if self.job_runner is not None:
            await self.job_runner.stop()
```

Delete these lines at the end of `aclose` (449-452):

```python
        # Drain fire-and-forget annotation/studio job tasks before the DB ...
        await drain_running_jobs(self.core._running_jobs)
```

Delete the `drain_running_jobs` function (context.py lines 93-109), the `_running_jobs` field on `CoreCtx` (line 145), and the `_running_jobs` property on `LiveCtx` (lines 424-426).

- [ ] **Step 3e: Remove orphan-cancel from startup**

In `backend/app/startup.py`, delete the jobs recovery from `run_startup_cleanup` (lines 26-33) and its docstring bullet, leaving only the live-sessions cleanup:

```python
async def run_startup_cleanup(conn: aiosqlite.Connection) -> int:
    """Boot-time cleanup of state a previous process left mid-flight. Returns
    the count of stale live_sessions dropped (back-compat with callers).

    Orphaned annotation jobs are resumed, not cleaned up here: that now happens
    in JobRunner.start() (requeue_orphaned_running), faithful to the prefetcher.
    """
    sessions = LiveSessionsRepo()
    return await sessions.cleanup_stale_pending(conn, older_than_hours=1)
```

Remove the now-unused `from backend.app.repositories.jobs import JobsRepo` import in `startup.py`. Delete `JobsRepo.cancel_orphaned_running` from `backend/app/repositories/jobs.py` (lines 251-275).

> CONSEQUENCE (accepted, per spec §6): when the app boots **offline**, orphaned `running` jobs are not requeued until the next *live* boot runs `JobRunner.start()`. They display as `running` meanwhile; self-healing. Recorded in ADR 0116.

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/integration/test_job_runner_lifecycle.py tests/unit/test_job_runner.py -q`
Then the broader sweep for anything referencing the deleted symbols:
Run: `grep -rn "drain_running_jobs\|_running_jobs\|cancel_orphaned_running" backend tests` → expect only deletions remain (update any leftover test).
Run: `.venv/bin/python -m pytest tests/integration -q -k "job or studio or lifecycle or shutdown"`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/context.py backend/app/main.py backend/app/startup.py backend/app/repositories/jobs.py backend/app/settings.py tests/integration/test_job_runner_lifecycle.py
git commit -m "feat(#100): wire JobRunner into lifespan; remove route-spawn drain + cancel-orphan"
```

---

### Task 6: Convert routes to pure DB writers

**Files:**
- Modify: `backend/app/routes/jobs.py` (drop `auto_start`, drop spawn, delete `_run_in_bg`/`start_job_in_background`, retarget cancel)
- Modify: `backend/app/routes/studio.py` (drop spawn + local `_run_in_bg`, drop `run_job` import)
- Modify: `backend/app/routes/batches.py` (retry-failed resets job→pending; drop `start_job_in_background` import)
- Test: `tests/integration` job/studio/batches route tests (update response-shape expectations)

**Interfaces:**
- Produces: `POST /api/jobs` returns `{"id": int, "queued": true}`. `POST /{job_id}/cancel` flips DB via core then calls `live.job_runner.cancel(job_id)` when live. `POST /batches/retry-failed` resets targeted `error` items → `pending` **and** the job → `pending`.

- [ ] **Step 1: Rewrite `routes/jobs.py`**

Replace the top import (line 14):

```python
from backend.app.services.annotator import JOBS_TOPIC
```

Replace `JobCreate` (drop `auto_start`) and `create_job` (lines 20-46):

```python
class JobCreate(BaseModel):
    prompt_version_id: int
    clip_ids: list[int]
    # Shared token tying together the per-kind jobs of one bulk action so the
    # Batch filter can present them as a single run.
    run_group: str | None = None


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_job(request: Request, body: JobCreate):
    require_permission(request, "run")
    ctx = get_core_ctx(request)
    job_id = await ctx.jobs_repo.create_job(
        ctx.db,
        prompt_version_id=body.prompt_version_id,
        clip_ids=body.clip_ids,
        run_group=body.run_group,
    )
    # The lifespan-owned JobRunner claims pending jobs when the live stack is
    # up; offline, the job stays pending and resumes on the next live boot.
    return {"id": job_id, "queued": True}
```

Delete `_run_in_bg` (lines 49-78) and `start_job_in_background` (lines 81-88) entirely. Remove the now-unused `import asyncio`, `import contextlib`, and `BackgroundTasks` import if nothing else uses them (check with `grep -n "asyncio\|contextlib\|BackgroundTasks" backend/app/routes/jobs.py`).

Retarget the cancel route (lines 178-189):

```python
@router.post("/{job_id}/cancel")
async def cancel_job(request: Request, job_id: int):
    ctx = get_core_ctx(request)
    # DB flip first (works offline): job + in-flight items -> cancelled, so the
    # claimer never picks it up. Then, if a live worker is running this exact
    # job, interrupt it so cancel is prompt instead of waiting out the Gemini
    # call. Its CancelledError handler re-runs cancel_job (idempotent).
    await ctx.jobs_repo.cancel_job(ctx.db, job_id)
    live = request.app.state.live_ctx
    if live is not None and live.job_runner is not None:
        live.job_runner.cancel(job_id)
    return {"id": job_id, "status": "cancelled"}
```

- [ ] **Step 2: Rewrite `routes/studio.py` create_run**

Remove `from backend.app.services.annotator import run_job` (line 27). Remove the spawn in `create_run` (lines 314-319) so the tail reads:

```python
    await ctx.studio_runs_repo.attach_job(ctx.db, run_id, job_id=job_id)
    # The JobRunner claims this pending job when live; offline it resumes later.
    return {"run_id": run_id, "job_id": job_id}
```

Delete the local `_run_in_bg` (lines 324-351). Remove now-unused `import asyncio` / `import contextlib` from studio.py if nothing else uses them (`grep -n "asyncio\|contextlib" backend/app/routes/studio.py`).

- [ ] **Step 3: Rewrite `routes/batches.py` retry-failed**

Remove `from backend.app.routes.jobs import start_job_in_background` (line 11). Replace the loop body (lines 164-167) so retry resets items **and** the job to pending — the worker re-claims it:

```python
        for it in failed:
            await core.jobs_repo.update_item_status(core.db, it.id, "pending")
        # Flip the job back to pending so the lifespan JobRunner re-claims it.
        # run_job only processes pending/error items, so only the reset clips
        # actually re-run.
        await core.jobs_repo.update_status(core.db, jid, "pending")
        started.append(jid)
```

The `live = get_live_ctx(request)` 503 guard stays (retry is meaningless with no live worker to pick the job up). The `only` filter still selects which failed items to reset.

- [ ] **Step 4: Update route tests**

Run: `grep -rn "auto_start\|\"started\"\|'started'\|start_job_in_background\|only_clip_ids" tests`
Update any integration test asserting `{"started": ...}` to expect `{"queued": True}`; drop `auto_start` from request bodies; for retry tests, assert the job/items return to `pending` (the worker, started in the test app's lifespan, then runs them).

Run: `.venv/bin/python -m pytest tests/integration -q -k "job or studio or batch"`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/routes/jobs.py backend/app/routes/studio.py backend/app/routes/batches.py tests/
git commit -m "feat(#100): routes become pure DB writers; cancel via job_runner"
```

---

### Task 7: Guard test — routes never spawn job execution

**Files:**
- Test: `tests/unit/test_jobs_no_route_spawn.py` (create)

**Interfaces:**
- Produces: a source-scan guard (idiom matches `test_no_x_data_stack.py` / `test_no_sync_fs_in_async.py`).

> WHY a scan and not an import-linter contract: `JOBS_TOPIC` still lives in `services.annotator`, and import-linter forbids at **module** granularity — a `routes ✗ services.annotator` contract would also block the legitimate `JOBS_TOPIC` import. A token scan bans `run_job` specifically while leaving `JOBS_TOPIC` allowed.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_jobs_no_route_spawn.py
"""Guard: routes must never execute jobs themselves. Job execution belongs to
the lifespan-owned JobRunner (services/job_runner.py). Routes only insert
pending rows + (for cancel) call job_runner.cancel. See ADR 0116."""

from pathlib import Path

ROUTES = Path(__file__).resolve().parents[2] / "backend" / "app" / "routes"

BANNED = ("run_job", "_running_jobs", "start_job_in_background", "drain_running_jobs")


def test_routes_do_not_execute_jobs():
    offenders = []
    for py in ROUTES.rglob("*.py"):
        text = py.read_text()
        for token in BANNED:
            if token in text:
                offenders.append(f"{py.name}: {token}")
    assert not offenders, (
        "Routes must not execute jobs — use the lifespan JobRunner (ADR 0116). "
        f"Found: {offenders}"
    )
```

- [ ] **Step 2: Run test to verify it passes (after Task 6) — or fails if Task 6 missed a spot**

Run: `.venv/bin/python -m pytest tests/unit/test_jobs_no_route_spawn.py -q`
Expected: PASS. If it FAILS, the offender list points at the leftover reference — remove it in `routes/`.

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_jobs_no_route_spawn.py
git commit -m "test(#100): guard — routes never execute jobs (JobRunner owns it)"
```

---

### Task 8: Frontend — drop `auto_start`, retire the offline "not started" branch

**Files:**
- Modify: `backend/app/static/bulkAnnotate.js` (lines 88, 96-98)
- Modify: `backend/app/static/clipAnnotate.js` (line 307)
- Modify: `backend/app/templates/pages/batches.html` (lines 289, 292)

**Interfaces:**
- Consumes: `POST /api/jobs` now returns `{"id", "queued": true}` (no `started`).

- [ ] **Step 1: `bulkAnnotate.js`**

Remove `auto_start: true,` from the request body (line 88). Replace the offline-failure branch (lines 96-98) — under queue-and-resume the job is always enqueued, so there is no "not started":

```javascript
            // job is enqueued; the JobRunner runs it when the live stack is up
            // (and resumes it automatically if offline now). No per-job "not
            // started" state any more.
```

(Delete the `if (data.started === false) { failures.push(...) }` block.)

- [ ] **Step 2: `clipAnnotate.js`**

Remove `auto_start: true,` from the request body (line 307). (`clipAnnotate` reads `data.job_id` / `data.item_status` / `data.started_at` from the *resume* endpoint, not from create — leave those untouched.)

- [ ] **Step 3: `batches.html`**

Remove `auto_start: true,` from the JSON body (line 289). Replace the `d.started === false` branch (line 292):

```javascript
            else { /* enqueued; JobRunner picks it up (resumes if offline) */ }
```

- [ ] **Step 4: Manual smoke (offline-safe, no CatDV seat)**

Run the walkthrough assert suite (covers batch + studio run flows):
Run: `.venv/bin/python -m tests.walkthrough.run --assert`
Expected: PASS. If a scenario asserts the old `started` toast/text, update that scenario + its `data-test` hooks in the same task (per CLAUDE.md e2e rule).

- [ ] **Step 5: Commit**

```bash
git add backend/app/static/bulkAnnotate.js backend/app/static/clipAnnotate.js backend/app/templates/pages/batches.html tests/walkthrough/
git commit -m "feat(#100): frontend — drop auto_start, retire offline 'not started' branch"
```

---

### Task 9: ADR 0116 + architecture invariants

**Files:**
- Create: `docs/adr/0116-jobrunner-claim-worker.md`
- Modify: `docs/architecture-invariants.md` (add the invariant line)
- Modify: `docs/decisions.md` (add 0116 to the index table)

- [ ] **Step 1: Write the ADR**

Create `docs/adr/0116-jobrunner-claim-worker.md` (MADR-lite, per CLAUDE.md):

```markdown
# 0116. Annotation/studio jobs run on a lifespan-owned DB-backed claim worker

**Date:** 2026-06-24
**Status:** Accepted
**Lifespan:** Invariant

## Context

Two queues on two paradigms: the cache queue is a lifespan-owned claim worker
(MediaPrefetcher), the annotation/studio runner was a fire-and-forget
`asyncio.create_task(run_job)` spawned from the HTTP route and tracked in
`CoreCtx._running_jobs`. The route owned execution; orphan recovery cancelled
rather than resumed; routes imported `run_job`. Tier 1 (ADR 0115) added
cancel-promptness + a shutdown drain but kept the route-spawn shape.

## Alternatives

- **Merge the two queues into one table.** Rejected — downloading bytes vs
  running Gemini are different concerns; the issue mandates separate tables.
- **Bounded-concurrent JobRunner (N workers).** Rejected — single serial worker
  matches MediaPrefetcher exactly and throttles Gemini/CatDV-seat load; bulk
  run_groups serialize (accepted).
- **notify()/event wake.** Rejected — pure polling keeps zero route↔runner
  coupling; a short tick keeps job start snappy.

## Decision

Job execution moves to `JobRunner` (services/job_runner.py): a single,
lifespan-owned worker on `LiveCtx` that polls `jobs`, claims the oldest
`pending` job via CAS (`JobsRepo.claim_next_job`), runs `run_job`, and loops.
Routes only insert a `pending` row (and, for cancel, call `job_runner.cancel`).
Orphan recovery **resumes**: `requeue_orphaned_running` flips `running` →
`pending` and resets stuck transient items, run on `JobRunner.start()`.
`CoreCtx._running_jobs`, `drain_running_jobs`, `cancel_orphaned_running`,
`only_clip_ids`, and `auto_start` are removed. The caching double-tracking
(`start_inline` → prefetch_queue row, ADR 0114) is unchanged — deliberate
`/cache` visibility.

## Consequences

- Boot-time resume of unfinished jobs falls out for free; run_job idempotency
  means only undone items re-run (one in-flight Gemini call may re-issue).
- Bulk run_groups run sequentially, not concurrently.
- An offline boot leaves crash-orphaned `running` jobs displayed as `running`
  until the next *live* boot requeues them (self-healing).
- Routes import nothing executable from the annotator. Enforced by
  `tests/unit/test_jobs_no_route_spawn.py`.
```

- [ ] **Step 2: Add the invariant line**

In `docs/architecture-invariants.md`, add (match the file's existing list style):

```markdown
- **All background work is a lifespan-owned DB-backed claim worker; routes never
  spawn execution tasks.** Both the cache queue (`MediaPrefetcher`) and the
  annotation/studio runner (`JobRunner`) share the start/poll/claim-CAS/
  orphan-resume/stop shape. Routes insert pending rows only. (ADR 0114, 0116)
```

- [ ] **Step 3: Index the ADR**

Add a row to the index table in `docs/decisions.md` for `0116` (match existing columns).

- [ ] **Step 4: Commit**

```bash
git add docs/adr/0116-jobrunner-claim-worker.md docs/architecture-invariants.md docs/decisions.md
git commit -m "docs(#100): ADR 0116 — JobRunner claim worker + invariant"
```

---

### Task 10: Full verification sweep

**Files:** none (verification only)

- [ ] **Step 1: import-linter**

Run: `.venv/bin/lint-imports`
Expected: PASS (contracts unchanged; no new violations).

- [ ] **Step 2: Full test suite (parallel)**

Run: `.venv/bin/python -m pytest -n auto -q`
Expected: PASS. Pay attention to any test referencing removed symbols (`auto_start`, `started`, `_running_jobs`, `drain_running_jobs`, `cancel_orphaned_running`, `only_clip_ids`) — fix the test to the new contract, not the code.

- [ ] **Step 3: Walkthrough assert (UI touched in Task 8)**

Run: `.venv/bin/python -m tests.walkthrough.run --assert`
Expected: PASS.

- [ ] **Step 4: Manual acceptance flows (from the spec)**

Walk the spec's **Manual acceptance flows** §1–§7 on a running dev server (use the `server-start`/`server-stop` skills for seat discipline). Tick each or report the failing step.

- [ ] **Step 5: Final commit (if any test fixups remain)**

```bash
git add -A
git commit -m "test(#100): align suite with JobRunner claim-worker contract"
```

---

## Self-Review

**Spec coverage:**
- Single serial worker → Task 3 (`JobRunner`, one `tick_once` at a time). ✓
- Poll-only, short tick → Task 3 + `job_tick_interval_s=0.75` (Task 5). ✓
- Double-tracking kept → out of scope, asserted in ADR (Task 9); no task touches `start_inline`. ✓
- Orphan resume → Task 2 (`requeue_orphaned_running`) + Task 5 (called in `start()`, cancel-orphan removed). ✓
- `auto_start` dropped, `{queued:true}` → Task 6 + Task 8. ✓
- `claim_next_job` CAS → Task 1. ✓
- Cancel offline-safe + prompt → Task 6 (DB flip via core + `job_runner.cancel`). ✓
- `_running_jobs`/`drain_running_jobs`/`only_clip_ids` removed → Tasks 4, 5. ✓
- Coupling kill + guard → Task 6 + Task 7. ✓
- ADR + invariants → Task 9. ✓
- Tests (unit/integration/guard/walkthrough) → Tasks 1-3, 5-8, 10. ✓

**Placeholder scan:** The two `NOTE:` blocks (Task 1 db fixture, Task 5 integration harness) point the implementer at `grep` commands to find the repo's real test harness rather than inventing one — this is deliberate (the exact fixture name is harness-specific), not a content gap. All code steps include full code.

**Type consistency:** `claim_next_job -> int | None`, `requeue_orphaned_running -> int`, `JobRunner(jobs_repo, run_job_fn, db_provider, tick_interval_s)`, `tick_once -> int | None`, `cancel(job_id)`, `LiveCtx.job_runner` — used identically across Tasks 1-7. `run_job` loses `only_clip_ids` (Task 4) and is never called with it afterward (Tasks 5-6). ✓
