# Batches Hub Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `/batches` hub — a dedicated, offline-safe overview of annotation runs (grouped by `run_group`) with metrics, completed/reviewed progress, failed-clip inspection + retry, and a review hand-off.

**Architecture:** Read path is pure DB: three new `JobsRepo` methods aggregate `jobs`/`job_items`/`review_items` into one-row-per-`run_group` batches (single grouped queries, N+1-guarded). A `routes/batches.py` renders a server-rendered Jinja page (reusing `layout.html`, `_ui.html`, `.metric-strip`, `.pill`, `:root` tokens) and a partial that an inline Alpine controller re-fetches on the existing `jobs` SSE topic. Retry re-uses `annotator.run_job` (which already re-runs only `error` items) via a small `only_clip_ids` filter; "+ New batch" routes to the clips list (no second picker).

**Tech Stack:** FastAPI, aiosqlite, Jinja2 (shared `templates` env), Alpine.js + HTMX (via `htmxAlpine.reinit`), pytest + `tests/_helpers/query_count.assert_query_count`.

Spec: `docs/specs/2026-06-02-batches-hub-design.md`.

---

## File Structure

| File | Create/Modify | Responsibility |
|---|---|---|
| `backend/app/repositories/jobs.py` | Modify | `list_batches`, `count_total_batches`, `failed_items_for_jobs` (read-only aggregation) |
| `tests/integration/test_jobs_repo_batches.py` | Create | Repo aggregation unit tests |
| `tests/integration/test_batches_page_perf.py` | Create | N+1 query-count guard for the read path |
| `backend/app/ui/view_models.py` | Modify | `batch_view(row)` — status label/state, bars, review href |
| `tests/unit/test_batch_view.py` | Create | `batch_view` label/clamp/percent logic |
| `backend/app/services/annotator.py` | Modify | `run_job(..., only_clip_ids=None)` filter |
| `backend/app/routes/jobs.py` | Modify | `_run_in_bg(only_clip_ids=…)`, extract `start_job_in_background`, reuse in `create_job` |
| `tests/integration/test_run_job_only_clip_ids.py` | Create | `only_clip_ids` re-runs just the named clip |
| `backend/app/routes/batches.py` | Create | `GET /batches`, `GET /batches/table`, `POST /batches/retry-failed` |
| `backend/app/main.py` | Modify | Register the batches router |
| `tests/integration/test_routes_batches.py` | Create | Page render, partial, retry 503/start |
| `backend/app/templates/icons/_batches.svg` | Create | Rail layers glyph |
| `backend/app/templates/pages/_rail.html` | Modify | Add the Batches rail button |
| `backend/app/templates/pages/batches.html` | Create | Page shell + metric strip + inline `batchesPage()` JS |
| `backend/app/templates/pages/_batches_table.html` | Create | History table + inline failed-clip detail rows |
| `backend/app/static/app.css` | Modify | Page-scoped `.batch-tbl` / `.miniprog` / fail-row styles (tokens only) |
| `docs/adr/NNNN-batches-hub.md` + `docs/decisions.md` | Create/Modify | ADR for the design calls |

---

## Task 1: `JobsRepo.list_batches` + `count_total_batches`

**Files:**
- Modify: `backend/app/repositories/jobs.py`
- Test: `tests/integration/test_jobs_repo_batches.py`

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_jobs_repo_batches.py`:

```python
import pytest

from backend.app.repositories.jobs import JobsRepo
from backend.app.repositories.prompts import PromptsRepo


async def _seed_version(db, *, name="Scénické značky CZ", model="gemini-2.5-pro") -> tuple[int, int]:
    prompts = PromptsRepo()
    pid, vid = await prompts.create_with_initial_version(
        db, name=name, description=None, body="p",
        target_map={"x": {"kind": "markers"}}, output_schema={}, model=model,
    )
    return pid, vid


async def _annotation_with_review(db, *, job_id, clip_id, applied):
    """Insert an annotation for (job, clip) and one review_item; applied=True
    sets applied_at so the clip counts as reviewed, else it's awaiting."""
    cur = await db.execute(
        "INSERT INTO annotations "
        "(catdv_clip_id, catdv_clip_name, prompt_version_id, job_id, model, "
        " prompt_used, raw_response, structured_output, clip_snapshot, created_at) "
        "VALUES (?, ?, 1, ?, 'm', 'p', '{}', '{}', '{}', '2026-06-02T00:00:00')",
        (clip_id, f"Clip_{clip_id}", job_id),
    )
    ann_id = cur.lastrowid
    await db.execute(
        "INSERT INTO review_items "
        "(annotation_id, studio_run_id, catdv_clip_id, kind, target_identifier, "
        " proposed_value, edited_value, decision, applied_at) "
        "VALUES (?, NULL, ?, 'marker', NULL, '{}', NULL, 'pending', ?)",
        (ann_id, clip_id, "2026-06-02T01:00:00" if applied else None),
    )
    await db.commit()


@pytest.mark.asyncio
async def test_list_batches_groups_run_group_into_one_row(db):
    _, vid = await _seed_version(db)
    jobs = JobsRepo()
    # two per-kind jobs sharing a run_group = one batch
    j1 = await jobs.create_job(db, prompt_version_id=vid, clip_ids=[101, 102], run_group="rg-1")
    j2 = await jobs.create_job(db, prompt_version_id=vid, clip_ids=[201], run_group="rg-1")
    # mark progress: 101 done, 102 error, 201 done
    its1 = await jobs.list_items(db, j1)
    await jobs.update_item_status(db, its1[0].id, "review_ready")
    await jobs.update_item_status(db, its1[1].id, "error", error="boom")
    its2 = await jobs.list_items(db, j2)
    await jobs.update_item_status(db, its2[0].id, "review_ready")

    rows = await jobs.list_batches(db, limit=50)
    assert len(rows) == 1
    r = rows[0]
    assert r["batch_key"] == "rg-1"
    assert sorted(r["job_ids"]) == sorted([j1, j2])
    assert r["primary_job_id"] == min(j1, j2)
    assert r["ran"] == 3
    assert r["failed"] == 1
    assert r["completed"] == 2  # two review_ready
    assert r["prompt_name"] == "Scénické značky CZ"
    assert r["version_num"] == 1
    assert r["model"] == "gemini-2.5-pro"
    assert r["prompt_count"] == 1


@pytest.mark.asyncio
async def test_list_batches_singleton_job_without_run_group(db):
    _, vid = await _seed_version(db)
    jobs = JobsRepo()
    jid = await jobs.create_job(db, prompt_version_id=vid, clip_ids=[1])
    rows = await jobs.list_batches(db, limit=50)
    assert len(rows) == 1
    assert rows[0]["batch_key"] == f"job:{jid}"
    assert rows[0]["job_ids"] == [jid]


@pytest.mark.asyncio
async def test_list_batches_awaiting_clips_counts_unapplied_reviews(db):
    _, vid = await _seed_version(db)
    jobs = JobsRepo()
    jid = await jobs.create_job(db, prompt_version_id=vid, clip_ids=[101, 102])
    its = await jobs.list_items(db, jid)
    await jobs.update_item_status(db, its[0].id, "review_ready")
    await jobs.update_item_status(db, its[1].id, "review_ready")
    await _annotation_with_review(db, job_id=jid, clip_id=101, applied=False)  # awaiting
    await _annotation_with_review(db, job_id=jid, clip_id=102, applied=True)   # reviewed

    rows = await jobs.list_batches(db, limit=50)
    assert rows[0]["awaiting_clips"] == 1


@pytest.mark.asyncio
async def test_list_batches_excludes_studio_jobs(db):
    _, vid = await _seed_version(db)
    jobs = JobsRepo()
    await jobs.create_job(db, prompt_version_id=vid, clip_ids=[1], kind="studio")
    assert await jobs.list_batches(db, limit=50) == []


@pytest.mark.asyncio
async def test_count_total_batches(db):
    _, vid = await _seed_version(db)
    jobs = JobsRepo()
    await jobs.create_job(db, prompt_version_id=vid, clip_ids=[1], run_group="rg-1")
    await jobs.create_job(db, prompt_version_id=vid, clip_ids=[2], run_group="rg-1")
    await jobs.create_job(db, prompt_version_id=vid, clip_ids=[3])  # singleton
    assert await jobs.count_total_batches(db) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/integration/test_jobs_repo_batches.py -q`
Expected: FAIL — `AttributeError: 'JobsRepo' object has no attribute 'list_batches'`.

- [ ] **Step 3: Add the methods to `JobsRepo`**

In `backend/app/repositories/jobs.py`, add these methods to the `JobsRepo` class (after `reset_transient`):

```python
    # --- Batches hub aggregation (read-only, offline-safe) -------------
    # A "batch" = the jobs sharing a non-null run_group, keyed by
    # COALESCE(run_group, 'job:'||id). Studio jobs are excluded. Each method
    # below issues a single grouped query — never a per-batch loop — so the
    # /batches read path stays O(1) in batch count (ADR 0046).

    _BATCHES_SQL = """
        WITH batch AS (
          SELECT
            COALESCE(j.run_group, 'job:' || j.id)               AS batch_key,
            MIN(j.id)                                           AS primary_job_id,
            MIN(j.created_at)                                   AS started_at,
            COUNT(DISTINCT j.prompt_version_id)                 AS prompt_count,
            GROUP_CONCAT(j.id)                                  AS job_ids_csv,
            SUM(CASE WHEN j.status = 'running' THEN 1 ELSE 0 END) AS running_jobs
          FROM jobs j
          WHERE COALESCE(j.kind, '') != 'studio'
          GROUP BY batch_key
        ),
        items AS (
          SELECT
            COALESCE(j.run_group, 'job:' || j.id) AS batch_key,
            COUNT(*) AS ran,
            SUM(CASE WHEN ji.status = 'error' THEN 1 ELSE 0 END) AS failed,
            SUM(CASE WHEN ji.status NOT IN
                ('pending','resolving','uploading','prompting','error')
                THEN 1 ELSE 0 END) AS completed,
            SUM(CASE WHEN ji.status IN
                ('pending','resolving','uploading','prompting')
                THEN 1 ELSE 0 END) AS in_flight
          FROM job_items ji
          JOIN jobs j ON j.id = ji.job_id
          WHERE COALESCE(j.kind, '') != 'studio'
          GROUP BY batch_key
        ),
        reviewed AS (
          SELECT
            COALESCE(j.run_group, 'job:' || j.id) AS batch_key,
            COUNT(DISTINCT ri.catdv_clip_id) AS awaiting_clips
          FROM jobs j
          JOIN annotations a ON a.job_id = j.id
          JOIN review_items ri ON ri.annotation_id = a.id AND ri.applied_at IS NULL
          WHERE COALESCE(j.kind, '') != 'studio'
          GROUP BY batch_key
        )
        SELECT
          b.batch_key                   AS batch_key,
          b.primary_job_id              AS primary_job_id,
          b.started_at                  AS started_at,
          b.job_ids_csv                 AS job_ids_csv,
          b.prompt_count                AS prompt_count,
          b.running_jobs                AS running_jobs,
          p.name                        AS prompt_name,
          pv.version_num                AS version_num,
          pv.model                      AS model,
          COALESCE(i.ran, 0)            AS ran,
          COALESCE(i.failed, 0)         AS failed,
          COALESCE(i.completed, 0)      AS completed,
          COALESCE(i.in_flight, 0)      AS in_flight,
          COALESCE(r.awaiting_clips, 0) AS awaiting_clips
        FROM batch b
        JOIN jobs pj ON pj.id = b.primary_job_id
        LEFT JOIN prompt_versions pv ON pv.id = pj.prompt_version_id
        LEFT JOIN prompts p ON p.id = pv.prompt_id
        LEFT JOIN items i ON i.batch_key = b.batch_key
        LEFT JOIN reviewed r ON r.batch_key = b.batch_key
        ORDER BY b.started_at DESC, b.primary_job_id DESC
        LIMIT ?
    """

    async def list_batches(
        self, conn: aiosqlite.Connection, *, limit: int = 50
    ) -> list[dict]:
        """One row per batch (run_group, or 'job:<id>' singleton), newest
        first. `job_ids` is the sorted list of member job ids."""
        cur = await conn.execute(self._BATCHES_SQL, (limit,))
        cols = [c[0] for c in cur.description]
        rows = [dict(zip(cols, r, strict=True)) for r in await cur.fetchall()]
        for r in rows:
            csv = r.pop("job_ids_csv") or ""
            r["job_ids"] = sorted(int(x) for x in csv.split(",") if x)
        return rows

    async def count_total_batches(self, conn: aiosqlite.Connection) -> int:
        """Grand total of distinct batches (run_groups + singleton jobs),
        excluding studio jobs. Powers the 'Batches' metric."""
        cur = await conn.execute(
            """
            SELECT COUNT(*) FROM (
              SELECT COALESCE(run_group, 'job:' || id) AS bk
              FROM jobs WHERE COALESCE(kind, '') != 'studio'
              GROUP BY bk
            )
            """
        )
        row = await cur.fetchone()
        return int(row[0]) if row else 0
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/integration/test_jobs_repo_batches.py -q`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/repositories/jobs.py tests/integration/test_jobs_repo_batches.py
git commit -m "feat(batches): JobsRepo.list_batches + count_total_batches aggregation"
```

---

## Task 2: `JobsRepo.failed_items_for_jobs`

**Files:**
- Modify: `backend/app/repositories/jobs.py`
- Test: `tests/integration/test_jobs_repo_batches.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/integration/test_jobs_repo_batches.py`:

```python
@pytest.mark.asyncio
async def test_failed_items_for_jobs_resolves_clip_name(db):
    _, vid = await _seed_version(db)
    jobs = JobsRepo()
    jid = await jobs.create_job(db, prompt_version_id=vid, clip_ids=[882290, 999])
    its = await jobs.list_items(db, jid)
    await jobs.update_item_status(db, its[0].id, "error", error="ProxyNotFound: not on disk")
    await jobs.update_item_status(db, its[1].id, "review_ready")  # not failed
    # name only known for 882290 via clip_cache
    await db.execute(
        "INSERT INTO clip_cache "
        "(provider_id, provider_clip_id, name, catalog_id, duration_secs, fps, "
        " canonical_json, provider_etag, fetched_at) "
        "VALUES ('catdv', '882290', 'Návštěva delegace', '7', 1.0, 25.0, '{}', NULL, "
        " '2026-06-02T00:00:00')"
    )
    await db.commit()

    fails = await jobs.failed_items_for_jobs(db, [jid])
    assert len(fails) == 1
    f = fails[0]
    assert f["job_id"] == jid
    assert f["catdv_clip_id"] == 882290
    assert f["error_message"] == "ProxyNotFound: not on disk"
    assert f["clip_name"] == "Návštěva delegace"


@pytest.mark.asyncio
async def test_failed_items_for_jobs_empty_input(db):
    assert await JobsRepo().failed_items_for_jobs(db, []) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/integration/test_jobs_repo_batches.py -k failed_items -q`
Expected: FAIL — `AttributeError: ... 'failed_items_for_jobs'`.

- [ ] **Step 3: Add the method**

In `backend/app/repositories/jobs.py`, add to `JobsRepo`:

```python
    async def failed_items_for_jobs(
        self, conn: aiosqlite.Connection, job_ids: list[int]
    ) -> list[dict]:
        """Failed (status='error') items across the given jobs, with the clip
        name resolved from clip_cache when available. `job_ids` is bounded by
        the page's batch limit, so a single IN clause is safe (one statement,
        not a per-row loop)."""
        if not job_ids:
            return []
        placeholders = ",".join("?" * len(job_ids))
        sql = f"""
            SELECT ji.job_id        AS job_id,
                   ji.catdv_clip_id AS catdv_clip_id,
                   ji.error_message AS error_message,
                   cc.name          AS clip_name
            FROM job_items ji
            LEFT JOIN clip_cache cc
              ON cc.provider_id = 'catdv'
             AND cc.provider_clip_id = CAST(ji.catdv_clip_id AS TEXT)
            WHERE ji.status = 'error' AND ji.job_id IN ({placeholders})
            ORDER BY ji.job_id, ji.catdv_clip_id
        """
        cur = await conn.execute(sql, tuple(job_ids))
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, r, strict=True)) for r in await cur.fetchall()]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/integration/test_jobs_repo_batches.py -q`
Expected: PASS (7 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/repositories/jobs.py tests/integration/test_jobs_repo_batches.py
git commit -m "feat(batches): JobsRepo.failed_items_for_jobs with clip-name resolution"
```

---

## Task 3: N+1 query-count guard for the read path

**Files:**
- Test: `tests/integration/test_batches_page_perf.py`

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_batches_page_perf.py`:

```python
"""Pin the /batches read path against N+1 regressions (ADR 0046).

list_batches(limit=50) + count_total_batches + failed_items_for_jobs must
issue a CONSTANT number of SQL statements regardless of how many batches
exist in the DB. Page is capped at 50 batches, so the failed-items IN list
stays inside one statement.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from backend.app.repositories.jobs import JobsRepo
from tests._helpers.query_count import assert_query_count


async def _seed_batches(db, n: int) -> None:
    now = datetime.now(UTC).isoformat()
    cur = await db.execute(
        "INSERT INTO prompts (name, description, archived, created_at, updated_at) "
        "VALUES ('p', NULL, 0, ?, ?)",
        (now, now),
    )
    pid = cur.lastrowid
    cur = await db.execute(
        "INSERT INTO prompt_versions "
        "(prompt_id, version_num, state, body, target_map, output_schema, model, "
        " created_at, updated_at) "
        "VALUES (?, 1, 'production', 'b', '{}', '{}', 'm', ?, ?)",
        (pid, now, now),
    )
    vid = cur.lastrowid
    for i in range(1, n + 1):
        cur = await db.execute(
            "INSERT INTO jobs (prompt_version_id, status, created_at, total_clips, run_group) "
            "VALUES (?, 'completed', ?, 3, ?)",
            (vid, now, f"rg-{i}"),
        )
        jid = cur.lastrowid
        for c in range(3):
            st = "error" if c == 0 else "review_ready"
            await db.execute(
                "INSERT INTO job_items (job_id, catdv_clip_id, status, error_message) "
                "VALUES (?, ?, ?, ?)",
                (jid, i * 10 + c, st, "boom" if st == "error" else None),
            )
    await db.commit()


@pytest.mark.asyncio
@pytest.mark.parametrize("n", [60, 200, 1000])
async def test_batches_read_path_is_constant_query_count(db, n):
    await _seed_batches(db, n)
    repo = JobsRepo()
    async with assert_query_count(db, 5) as counter:
        rows = await repo.list_batches(db, limit=50)
        await repo.count_total_batches(db)
        job_ids = [jid for r in rows for jid in r["job_ids"]]
        await repo.failed_items_for_jobs(db, job_ids)
    # list_batches (1) + count_total_batches (1) + failed_items_for_jobs (1)
    assert counter.count == 3, f"[n={n}] expected 3 statements, got {counter.count}"
```

- [ ] **Step 2: Run test to verify it passes**

Run: `.venv/bin/pytest tests/integration/test_batches_page_perf.py -q`
Expected: PASS (3 params). (The methods from Tasks 1–2 already satisfy it; this test pins the behavior.)

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_batches_page_perf.py
git commit -m "test(batches): pin read path against N+1 (10/200/1000 batches)"
```

---

## Task 4: `batch_view` view-model

**Files:**
- Modify: `backend/app/ui/view_models.py`
- Test: `tests/unit/test_batch_view.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_batch_view.py`:

```python
from backend.app.ui.view_models import batch_view


def _row(**over):
    base = {
        "batch_key": "rg-1", "primary_job_id": 42, "started_at": "2026-06-02T09:04:00",
        "job_ids": [42, 43], "prompt_count": 1, "running_jobs": 0,
        "prompt_name": "Scénické značky CZ", "version_num": 5, "model": "gemini-2.5-pro",
        "ran": 12, "failed": 1, "completed": 11, "in_flight": 0, "awaiting_clips": 5,
    }
    base.update(over)
    return base


def test_basic_counts_and_reviewed_clamp():
    v = batch_view(_row())
    assert v["id"] == 42
    assert v["job_ids"] == [42, 43]
    assert v["ran"] == 12
    assert v["completed"] == 11
    assert v["failed"] == 1
    assert v["reviewed"] == 6          # completed(11) - awaiting(5)
    assert v["pct_done"] == 100        # (completed+failed)/ran = 12/12
    assert v["pct_reviewed"] == 55     # round(6/11*100)


def test_status_running():
    v = batch_view(_row(running_jobs=1, completed=4, failed=1))
    assert v["running"] is True
    assert v["status_state"] == "accent"
    assert v["status_label"] == "Running 5/12"


def test_status_awaiting_review_when_none_reviewed():
    v = batch_view(_row(running_jobs=0, completed=10, awaiting_clips=10))
    assert v["status_state"] == ""
    assert v["status_label"] == "Awaiting review"


def test_status_n_to_review():
    v = batch_view(_row(running_jobs=0, completed=10, awaiting_clips=3))
    assert v["status_label"] == "3 to review"


def test_status_applied_when_all_reviewed():
    v = batch_view(_row(running_jobs=0, completed=10, awaiting_clips=0))
    assert v["status_state"] == "ok"
    assert v["status_label"] == "Applied"


def test_multi_prompt_label():
    v = batch_view(_row(prompt_count=2))
    assert v["prompt"] == "Scénické značky CZ + 1 more"


def test_missing_prompt_label():
    v = batch_view(_row(prompt_name=None))
    assert v["prompt"] == "(prompt unavailable)"


def test_review_href_targets_batch_jobs():
    v = batch_view(_row(job_ids=[42, 43]))
    assert v["review_href"] == "/?batch=42,43&anno=for_review"


def test_zero_ran_no_divide_by_zero():
    v = batch_view(_row(ran=0, completed=0, failed=0, awaiting_clips=0))
    assert v["pct_done"] == 0
    assert v["pct_reviewed"] == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/unit/test_batch_view.py -q`
Expected: FAIL — `ImportError: cannot import name 'batch_view'`.

- [ ] **Step 3: Implement `batch_view`**

Append to `backend/app/ui/view_models.py`:

```python
def batch_view(row: dict) -> dict:
    """Shape a `JobsRepo.list_batches` row into the dict the Batches table
    renders. Pure function (no I/O) so it is unit-tested in isolation.

    Status mirrors the design: running → 'Running X/Y'; not running with
    drafts still awaiting → 'Awaiting review' / 'N to review'; otherwise
    'Applied'.
    """
    ran = int(row["ran"])
    completed = int(row["completed"])
    failed = int(row["failed"])
    awaiting = int(row["awaiting_clips"])
    running = int(row["running_jobs"]) > 0 or int(row["in_flight"]) > 0
    reviewed = max(0, completed - awaiting)

    if running:
        status_state, status_label = "accent", f"Running {completed + failed}/{ran}"
    elif awaiting > 0:
        status_state = ""
        status_label = "Awaiting review" if reviewed == 0 else f"{awaiting} to review"
    else:
        status_state, status_label = "ok", "Applied"

    name = row.get("prompt_name") or "(prompt unavailable)"
    if row.get("prompt_name") and int(row.get("prompt_count", 1)) > 1:
        name = f"{name} + {int(row['prompt_count']) - 1} more"

    job_ids = list(row["job_ids"])
    started = row.get("started_at") or ""
    try:
        from datetime import datetime as _dt

        started = _dt.fromisoformat(started).strftime("%d %b %H:%M")
    except (ValueError, TypeError):
        pass

    return {
        "batch_key": row["batch_key"],
        "id": int(row["primary_job_id"]),
        "job_ids": job_ids,
        "prompt": name,
        "version": row.get("version_num"),
        "model": row.get("model") or "",
        "started": started,
        "ran": ran,
        "completed": completed,
        "failed": failed,
        "reviewed": reviewed,
        "awaiting": awaiting,
        "running": running,
        "pct_done": round((completed + failed) / ran * 100) if ran else 0,
        "pct_reviewed": round(reviewed / completed * 100) if completed else 0,
        "status_state": status_state,
        "status_label": status_label,
        "review_href": f"/?batch={','.join(str(i) for i in job_ids)}&anno=for_review",
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/unit/test_batch_view.py -q`
Expected: PASS (9 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/ui/view_models.py tests/unit/test_batch_view.py
git commit -m "feat(batches): batch_view view-model (status, bars, review href)"
```

---

## Task 5: `run_job(only_clip_ids=…)` + `start_job_in_background`

**Files:**
- Modify: `backend/app/services/annotator.py`
- Modify: `backend/app/routes/jobs.py`
- Test: `tests/integration/test_run_job_only_clip_ids.py`

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_run_job_only_clip_ids.py`:

```python
import datetime as dt
import json
from pathlib import Path

import pytest

from backend.app.archive.model import CanonicalClip, MediaRef
from backend.app.repositories.annotations import AnnotationsRepo
from backend.app.repositories.jobs import JobsRepo
from backend.app.repositories.prompts import PromptsRepo
from backend.app.repositories.review_items import ReviewItemsRepo
from backend.app.repositories.studio_runs import StudioRunsRepo
from backend.app.services.annotator import run_job
from backend.app.services.events import EventBus


class _Resolver:
    def __init__(self, files):
        self.files = files

    async def path_for_clip_id(self, clip_id):
        return self.files[clip_id]

    def is_managed(self, path):
        return True


class _AIStore:
    id = "gcs:bucket"

    async def status(self, clip_key):
        return None

    async def ensure_uploaded(self, clip_key, local_path, mime):
        from backend.app.archive.ai_store_model import UploadedRef

        return UploadedRef(
            handle=f"gs://b/{clip_key[1]}.mov", mime_type=mime,
            size_bytes=1, sha256="x", uploaded_at=dt.datetime.now(dt.UTC), expires_at=None,
        )

    async def reference_for_gemini(self, ref):
        return {"file_data": {"file_uri": ref.handle, "mime_type": ref.mime_type}}


class _Archive:
    async def get_clip(self, clip_id_str):
        return CanonicalClip(
            key=("catdv", clip_id_str), name=f"Clip_{clip_id_str}", duration_secs=0.0,
            fps=25.0, markers=tuple(), fields={}, notes={},
            media=MediaRef(mime_type="video/quicktime", size_bytes=None,
                           cached_path=None, upstream_handle=clip_id_str),
            provider_data={}, fetched_at=dt.datetime.now(dt.UTC),
        )


class _Gemini:
    def annotate(self, *, file_ref, prompt, schema, model):
        out = json.dumps({"scenes": [{"name": "s", "in": {"secs": 0.0}, "out": {"secs": 1.0}}]})
        return {"text": out, "raw": {"candidates": [{"text": out}]}}


@pytest.mark.asyncio
async def test_run_job_only_clip_ids_processes_just_that_clip(db, tmp_path):
    prompts = PromptsRepo()
    _, vid = await prompts.create_with_initial_version(
        db, name="t", description=None, body="describe",
        target_map={"scenes": {"kind": "markers"}}, output_schema={}, model="m",
    )
    jobs = JobsRepo()
    job_id = await jobs.create_job(db, prompt_version_id=vid, clip_ids=[101, 102])

    files = {}
    for cid in (101, 102):
        p: Path = tmp_path / f"{cid}.mov"
        p.write_bytes(b"X" * 10)
        files[cid] = p

    await run_job(
        db=db, job_id=job_id, archive=_Archive(), proxy_resolver=_Resolver(files),
        ai_store=_AIStore(), gemini=_Gemini(), event_bus=EventBus(),
        annotations_repo=AnnotationsRepo(), review_items_repo=ReviewItemsRepo(),
        jobs_repo=jobs, prompts_repo=prompts, studio_runs_repo=StudioRunsRepo(),
        only_clip_ids={102},
    )

    items = {it.catdv_clip_id: it.status for it in await jobs.list_items(db, job_id)}
    assert items[101] == "pending"        # skipped by the filter
    assert items[102] == "review_ready"   # processed
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/integration/test_run_job_only_clip_ids.py -q`
Expected: FAIL — `TypeError: run_job() got an unexpected keyword argument 'only_clip_ids'`.

- [ ] **Step 3: Add the `only_clip_ids` filter to `run_job`**

In `backend/app/services/annotator.py`, change the `run_job` signature — add the new keyword-only param after `studio_runs_repo`:

```python
    studio_runs_repo: StudioRunsRepo,
    only_clip_ids: set[int] | None = None,
) -> None:
```

Then in the item loop, immediately after the existing status check (`if item.status not in ("pending", "error"): continue`), add:

```python
        if only_clip_ids is not None and item.catdv_clip_id not in only_clip_ids:
            continue
```

- [ ] **Step 4: Extract `start_job_in_background` and thread `only_clip_ids` in `routes/jobs.py`**

In `backend/app/routes/jobs.py`, replace the `_run_in_bg` function and the auto-start block of `create_job`.

Change `_run_in_bg` to accept and pass `only_clip_ids`:

```python
async def _run_in_bg(ctx, job_id: int, *, only_clip_ids: set[int] | None = None) -> None:
    try:
        await run_job(
            db=ctx.db,
            job_id=job_id,
            archive=ctx.archive,
            proxy_resolver=ctx.proxy_resolver,
            ai_store=ctx.ai_store,
            gemini=ctx.gemini,
            event_bus=ctx.event_bus,
            annotations_repo=ctx.annotations_repo,
            review_items_repo=ctx.review_items_repo,
            jobs_repo=ctx.jobs_repo,
            prompts_repo=ctx.prompts_repo,
            studio_runs_repo=ctx.studio_runs_repo,
            only_clip_ids=only_clip_ids,
        )
    finally:
        ctx._running_jobs.pop(job_id, None)


def start_job_in_background(
    core, live, job_id: int, *, only_clip_ids: set[int] | None = None
) -> None:
    """Spawn run_job for `job_id` as a tracked background task. Shared by
    POST /api/jobs (auto-start) and the Batches retry-failed route."""
    task = asyncio.create_task(_run_in_bg(live, job_id, only_clip_ids=only_clip_ids))
    core._running_jobs[job_id] = task
```

Then in `create_job`, replace the auto-start spawn:

```python
    started = bool(body.auto_start and live is not None and live.proxy_resolver is not None)
    if started:
        start_job_in_background(ctx, live, job_id)
    return {"id": job_id, "started": started}
```

(`ctx` here is the `CoreCtx` from `get_core_ctx(request)`; `live` is `request.app.state.live_ctx`.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/integration/test_run_job_only_clip_ids.py tests/integration/test_annotator_worker.py tests/integration/test_routes_jobs.py -q`
Expected: PASS (new test + existing annotator/jobs route tests stay green).

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/annotator.py backend/app/routes/jobs.py tests/integration/test_run_job_only_clip_ids.py
git commit -m "feat(batches): run_job only_clip_ids filter + start_job_in_background helper"
```

---

## Task 6: `routes/batches.py` + registration

**Files:**
- Create: `backend/app/routes/batches.py`
- Modify: `backend/app/main.py`
- Test: `tests/integration/test_routes_batches.py`

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_routes_batches.py`:

```python
import asyncio
import importlib
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from backend.app.repositories.jobs import JobsRepo
from backend.app.repositories.prompts import PromptsRepo
from tests._helpers.live_ctx import install_live_ctx


def _make_client(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("CATDV_BASE_URL", "http://localhost:0")
    monkeypatch.setenv("CATDV_USERNAME", "")
    monkeypatch.setenv("CATDV_PASSWORD", "p")
    monkeypatch.setenv("CATDV_CATALOG_ID", "7")
    monkeypatch.setenv("GCP_PROJECT_ID", "p")
    monkeypatch.setenv("GCS_BUCKET_NAME", "b")
    monkeypatch.setenv("PROXY_SOURCE", "rest")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from backend.app import main as main_mod

    importlib.reload(main_mod)
    return TestClient(main_mod.app)


async def _seed_batch(ctx):
    prompts = PromptsRepo()
    _, vid = await prompts.create_with_initial_version(
        ctx.db, name="Scénické značky CZ", description=None, body="p",
        target_map={"x": {"kind": "markers"}}, output_schema={}, model="gemini-2.5-pro",
    )
    jobs = JobsRepo()
    jid = await jobs.create_job(ctx.db, prompt_version_id=vid, clip_ids=[101, 102], run_group="rg-1")
    its = await jobs.list_items(ctx.db, jid)
    await jobs.update_item_status(ctx.db, its[0].id, "review_ready")
    await jobs.update_item_status(ctx.db, its[1].id, "error", error="ProxyNotFound")
    return jid


def test_batches_page_renders(monkeypatch, tmp_path):
    with _make_client(monkeypatch, tmp_path) as client:
        asyncio.run(_seed_batch(client.app.state.core_ctx))
        r = client.get("/batches")
        assert r.status_code == 200
        assert "<!doctype html>" in r.text.lower()
        assert "Scénické značky CZ" in r.text
        assert "gemini-2.5-pro" in r.text
        # rail marks Batches active
        assert 'title="Batches"' in r.text
        assert "rail-btn active" in r.text
        # failed count surfaced
        assert "1 failed" in r.text


def test_batches_table_partial(monkeypatch, tmp_path):
    with _make_client(monkeypatch, tmp_path) as client:
        asyncio.run(_seed_batch(client.app.state.core_ctx))
        r = client.get("/batches/table")
        assert r.status_code == 200
        assert "<!doctype html>" not in r.text.lower()
        assert "Scénické značky CZ" in r.text


def test_batches_page_empty_state(monkeypatch, tmp_path):
    with _make_client(monkeypatch, tmp_path) as client:
        r = client.get("/batches")
        assert r.status_code == 200
        assert "No batches yet" in r.text


def test_retry_failed_503_when_offline(monkeypatch, tmp_path):
    with _make_client(monkeypatch, tmp_path) as client:
        jid = asyncio.run(_seed_batch(client.app.state.core_ctx))
        # No live_ctx installed → get_live_ctx raises 503
        r = client.post("/batches/retry-failed", json={"job_ids": [jid]})
        assert r.status_code == 503


def test_retry_failed_starts_only_jobs_with_failures(monkeypatch, tmp_path):
    with _make_client(monkeypatch, tmp_path) as client:
        jid = asyncio.run(_seed_batch(client.app.state.core_ctx))
        install_live_ctx(client.app, proxy_resolver=MagicMock())  # online + resolver present

        started: list[int] = []
        import backend.app.routes.batches as batches_mod

        monkeypatch.setattr(
            batches_mod, "start_job_in_background",
            lambda core, live, job_id, **kw: started.append(job_id),
        )
        r = client.post("/batches/retry-failed", json={"job_ids": [jid]})
        assert r.status_code == 200
        assert started == [jid]
        assert r.json()["started"] == [jid]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/integration/test_routes_batches.py -q`
Expected: FAIL — 404 on `/batches` (router not registered yet).

- [ ] **Step 3: Create the router**

Create `backend/app/routes/batches.py`:

```python
"""Batches hub — a dedicated overview of annotation runs (jobs grouped by
run_group). Read path is pure DB (offline-safe, get_core_ctx); retry needs
live services (get_live_ctx → typed 503 offline)."""

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from backend.app.deps import get_core_ctx, get_live_ctx
from backend.app.routes.jobs import start_job_in_background
from backend.app.routes.pages.templates import templates
from backend.app.ui.view_models import batch_view

router = APIRouter(tags=["batches"])


async def _load_batches_ctx(ctx, limit: int) -> dict:
    rows = await ctx.jobs_repo.list_batches(ctx.db, limit=limit)
    views = [batch_view(r) for r in rows]

    all_job_ids = [jid for r in rows for jid in r["job_ids"]]
    fails = await ctx.jobs_repo.failed_items_for_jobs(ctx.db, all_job_ids)
    job_to_key = {jid: r["batch_key"] for r in rows for jid in r["job_ids"]}
    fails_by_key: dict[str, list[dict]] = {}
    for f in fails:
        key = job_to_key.get(f["job_id"])
        fails_by_key.setdefault(key, []).append(
            {
                "id": f["catdv_clip_id"],
                "name": f["clip_name"] or f"Clip {f['catdv_clip_id']}",
                "error": f["error_message"] or "Unknown error",
            }
        )
    for v in views:
        v["fails"] = fails_by_key.get(v["batch_key"], [])

    total_batches = await ctx.jobs_repo.count_total_batches(ctx.db)
    metrics = {
        "total_batches": total_batches,
        "shown": len(views),
        "drafts_produced": sum(v["completed"] for v in views),
        "awaiting_review": sum(v["awaiting"] for v in views),
        "awaiting_batches": sum(1 for v in views if not v["running"] and v["awaiting"] > 0),
        "failed_clips": sum(v["failed"] for v in views),
    }
    return {"batches": views, "metrics": metrics}


@router.get("/batches", response_class=HTMLResponse)
async def batches_page(request: Request, limit: int = 50):
    ctx = get_core_ctx(request)
    ctx_dict = await _load_batches_ctx(ctx, limit)
    return templates.TemplateResponse(request, "pages/batches.html", ctx_dict)


@router.get("/batches/table", response_class=HTMLResponse)
async def batches_table(request: Request, limit: int = 50):
    """HTMX/fetch partial — the table region only, for live refresh."""
    ctx = get_core_ctx(request)
    ctx_dict = await _load_batches_ctx(ctx, limit)
    return templates.TemplateResponse(request, "pages/_batches_table.html", ctx_dict)


class RetryFailed(BaseModel):
    job_ids: list[int]
    clip_ids: list[int] | None = None


@router.post("/batches/retry-failed")
async def retry_failed(request: Request, body: RetryFailed):
    """Re-run failed clips. Reuses annotator.run_job (which only re-processes
    'error'/'pending' items); only_clip_ids narrows to a single clip when
    given. Requires live services + a proxy resolver."""
    live = get_live_ctx(request)  # 503 when offline
    if live.proxy_resolver is None:
        raise HTTPException(503, "Proxy resolver offline — cannot run annotations")
    core = live.core
    only = set(body.clip_ids) if body.clip_ids else None

    started: list[int] = []
    for jid in body.job_ids:
        items = await core.jobs_repo.list_items(core.db, jid)
        has_failed = any(
            it.status == "error" and (only is None or it.catdv_clip_id in only)
            for it in items
        )
        if not has_failed:
            continue
        start_job_in_background(core, live, jid, only_clip_ids=only)
        started.append(jid)
    return {"started": started}
```

- [ ] **Step 4: Register the router in `main.py`**

In `backend/app/main.py`, add the import alongside the other route imports (near line 13):

```python
from backend.app.routes.batches import router as batches_router
```

And register it alongside the other `include_router` calls (e.g. right after `app.include_router(jobs_router)`):

```python
    app.include_router(batches_router)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/pytest tests/integration/test_routes_batches.py -q`
Expected: PASS (5 tests). (These depend on Tasks 7–8 templates existing — if `batches.html`/`_batches_table.html` are not yet created, the render tests will 500. Implement Tasks 7–8 before re-running, or stub the templates first. To keep this task self-contained, create the templates in Tasks 7–8 then re-run this test at the end of Task 8.)

> **Sequencing note:** the route tests render real templates. Run Step 5 only *after* Tasks 7–8 land. Commit the route code now; the test goes green at the end of Task 8.

- [ ] **Step 6: Commit**

```bash
git add backend/app/routes/batches.py backend/app/main.py tests/integration/test_routes_batches.py
git commit -m "feat(batches): /batches page + table partial + retry-failed route"
```

---

## Task 7: Rail icon + nav entry

**Files:**
- Create: `backend/app/templates/icons/_batches.svg`
- Modify: `backend/app/templates/pages/_rail.html`

- [ ] **Step 1: Create the icon**

Create `backend/app/templates/icons/_batches.svg`:

```html
<svg viewBox="0 0 24 24" width="19" height="19" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polygon points="12 3 21 8 12 13 3 8 12 3"></polygon><polyline points="3 13 12 18 21 13"></polyline></svg>
```

- [ ] **Step 2: Add the rail button**

In `backend/app/templates/pages/_rail.html`, insert the Batches button between the Studio button and the Cache button:

```html
<a class="rail-btn{% if _active == 'studio' %} active{% endif %}"
   href="/studio" title="Studio">{% include "icons/_flask.svg" %}</a>
<a class="rail-btn{% if _active == 'batches' %} active{% endif %}"
   href="/batches" title="Batches">{% include "icons/_batches.svg" %}</a>
<a class="rail-btn{% if _active == 'cache' %} active{% endif %}"
   href="/cache" title="Cache">{% include "icons/_cache.svg" %}</a>
```

Also update the top-of-file comment's button list to include Batches (one-line edit; replace "Clips, Preview, Cache" with the current set "Clips, Preview, Prompts, Studio, Batches, Cache").

- [ ] **Step 3: Verify via an existing page render test**

Run: `.venv/bin/pytest tests/integration/test_routes_pages.py -k rail -q`
Expected: PASS — adding a button does not break the existing presence assertions (they check for substrings, not counts).

- [ ] **Step 4: Commit**

```bash
git add backend/app/templates/icons/_batches.svg backend/app/templates/pages/_rail.html
git commit -m "feat(batches): add Batches rail icon + nav entry on every page"
```

---

## Task 8: Page + table templates + inline controller

**Files:**
- Create: `backend/app/templates/pages/batches.html`
- Create: `backend/app/templates/pages/_batches_table.html`

- [ ] **Step 1: Create the table partial**

Create `backend/app/templates/pages/_batches_table.html`:

```html
{% if not batches %}
  <div class="batches-empty">
    No batches yet.
    <a href="/">Select clips and run Annotate</a> to create your first batch.
  </div>
{% else %}
<table class="batch-tbl">
  <thead>
    <tr>
      <th>Batch</th><th>Started</th><th>Ran</th>
      <th>Completed</th><th>Reviewed</th><th>Status</th><th></th>
    </tr>
  </thead>
  <tbody>
    {% for b in batches %}
    <tr>
      <td>
        <div class="bt-id">
          <span class="bt-num">#{{ b.id }}</span>
          <span class="bt-prompt">{{ b.prompt }}</span>
          <span class="bt-meta">v{{ b.version }} · {{ b.model }}</span>
        </div>
      </td>
      <td><span class="bt-when">{{ b.started }}</span></td>
      <td><span class="bt-ran">{{ b.ran }}</span></td>
      <td class="bcell-prog">
        <div class="miniprog">
          <div class="mp-label">
            <span>{{ b.completed }}/{{ b.ran }}</span>
            {% if b.failed %}
            <button type="button" class="mp-fail-btn" @click="toggle('{{ b.batch_key }}')">·
              {{ b.failed }} failed
              <span x-text="expanded['{{ b.batch_key }}'] ? '▴' : '▾'">▾</span>
            </button>
            {% endif %}
          </div>
          <div class="mp-bar">
            <span class="{{ 'fill-run' if b.running else 'fill-done' }}"
                  style="width: {{ b.pct_done }}%"></span>
          </div>
        </div>
      </td>
      <td class="bcell-prog">
        <div class="miniprog">
          <div class="mp-label"><span>{{ b.reviewed }}/{{ b.completed }}</span></div>
          <div class="mp-bar">
            <span class="fill-rev" style="width: {{ b.pct_reviewed }}%"></span>
          </div>
        </div>
      </td>
      <td>
        <span class="pill {{ b.status_state }}">
          {% if b.running %}<span class="ca-spinner" aria-hidden="true"></span>{% endif %}
          <span class="led"></span>{{ b.status_label }}
        </span>
      </td>
      <td class="bt-actions">
        {% if not b.running and b.failed %}
        <button type="button" class="btn sm" @click="retryFailed({{ b.job_ids|tojson }})">
          ↻ Retry failed ({{ b.failed }})
        </button>
        {% endif %}
        {% if not b.running and b.reviewed < b.completed %}
        <a class="btn sm primary" href="{{ b.review_href }}">Review →</a>
        {% endif %}
        {% if b.running or (b.reviewed >= b.completed and not b.failed) %}
        <a class="btn sm ghost" href="{{ b.review_href if b.completed else '/' }}">Open</a>
        {% endif %}
      </td>
    </tr>
    {% if b.fails %}
    <tr class="bt-detail" x-show="expanded['{{ b.batch_key }}']" x-cloak>
      <td colspan="7">
        <div class="bt-fails">
          <div class="bt-fails-h">
            <span>Failed clips ({{ b.fails|length }})</span>
            <span class="grow"></span>
            <button type="button" class="btn sm" @click="retryFailed({{ b.job_ids|tojson }})">
              ↻ Retry all failed
            </button>
          </div>
          {% for f in b.fails %}
          <div class="bt-fail-row">
            <div>
              <div class="bt-fail-name">{{ f.name }}</div>
              <div class="bt-fail-err">{{ f.error }}</div>
            </div>
            <span class="grow"></span>
            <button type="button" class="btn sm ghost"
                    @click="retryFailed({{ b.job_ids|tojson }}, {{ f.id }})">↻ Retry</button>
          </div>
          {% endfor %}
        </div>
      </td>
    </tr>
    {% endif %}
    {% endfor %}
  </tbody>
</table>
{% endif %}
```

- [ ] **Step 2: Create the page**

Create `backend/app/templates/pages/batches.html`:

```html
{% extends "pages/layout.html" %}
{% import "components/_ui.html" as ui %}
{% block rail_active %}batches{% endblock %}
{% block title %}Batches · CatDV Annotator{% endblock %}
{% block crumb %}{{ ui.breadcrumb([('Batches', None)]) }}{% endblock %}

{% block body %}
<div class="page batches-page" x-data="batchesPage()" data-screen-label="Batches">
  <div class="page-hdr">
    <h1>Batches</h1>
    <span class="meta">annotation runs</span>
    <div class="grow"></div>
    {{ ui.button('+ New batch', href='/', variant='primary') }}
  </div>

  <div class="batches-scroll">
    <div class="metric-strip">
      <div class="metric">
        <div class="m-label">Batches</div>
        <div class="m-value">{{ metrics.total_batches }}</div>
        <div class="m-sub">{{ metrics.shown }} shown</div>
      </div>
      <div class="metric">
        <div class="m-label">Drafts produced</div>
        <div class="m-value">{{ metrics.drafts_produced }}</div>
        <div class="m-sub">across recent batches</div>
      </div>
      <div class="metric">
        <div class="m-label">Awaiting review</div>
        <div class="m-value">{{ metrics.awaiting_review }}</div>
        <div class="m-sub">{{ metrics.awaiting_batches }} batch{{ '' if metrics.awaiting_batches == 1 else 'es' }}</div>
      </div>
      <div class="metric danger">
        <div class="m-label">Failed clips</div>
        <div class="m-value">{{ metrics.failed_clips }}</div>
        <div class="m-sub">across recent batches</div>
      </div>
    </div>

    <div id="batches-table-region">
      {% include "pages/_batches_table.html" %}
    </div>
  </div>
</div>

<script>
  // Page-specific controller. Read-only table + live refresh on the existing
  // global `jobs` SSE topic, and retry via fetch + toast. Reuses the single
  // lifecycle helper (window.htmxAlpine.reinit) for the fetch-injected table.
  function batchesPage() {
    return {
      expanded: {},
      _es: null,
      _t: null,

      init() {
        window.addEventListener("jobs-changed", () => this._schedule());
        try {
          this._es = new EventSource("/api/jobs/events");
          this._es.onmessage = () => this._schedule();
        } catch (e) { /* SSE unavailable; table is still usable */ }
      },

      _schedule() { clearTimeout(this._t); this._t = setTimeout(() => this.refresh(), 500); },

      async refresh() {
        try {
          const r = await fetch("/batches/table");
          if (!r.ok) return;
          const html = await r.text();
          const region = document.getElementById("batches-table-region");
          if (!region) return;
          region.innerHTML = html;
          window.htmxAlpine.reinit(region);
        } catch (e) { /* offline — keep current view */ }
      },

      toggle(key) { this.expanded[key] = !this.expanded[key]; },

      async retryFailed(jobIds, clipId = null) {
        const body = clipId == null
          ? { job_ids: jobIds }
          : { job_ids: jobIds, clip_ids: [clipId] };
        try {
          const r = await fetch("/batches/retry-failed", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
          });
          if (!r.ok) {
            const d = await r.json().catch(() => ({}));
            throw new Error(d.detail || ("HTTP " + r.status));
          }
          Alpine.store("toast").push("Re-running failed clip(s)…", { level: "success" });
          this._schedule();
        } catch (e) {
          Alpine.store("toast").push("Retry failed: " + e.message, { level: "error" });
        }
      },
    };
  }
  window.batchesPage = batchesPage;
</script>
{% endblock %}
```

- [ ] **Step 3: Run the route tests (now templates exist)**

Run: `.venv/bin/pytest tests/integration/test_routes_batches.py -q`
Expected: PASS (5 tests — page renders, partial, empty state, 503, retry-start).

- [ ] **Step 4: Commit**

```bash
git add backend/app/templates/pages/batches.html backend/app/templates/pages/_batches_table.html
git commit -m "feat(batches): batches page + table partial + live-refresh controller"
```

---

## Task 9: Page-scoped CSS

**Files:**
- Modify: `backend/app/static/app.css`

- [ ] **Step 1: Confirm the design tokens exist**

Run: `grep -nE -- '--panel:|--line:|--text-2:|--text-3:|--hover:|--surface:|--good:|--accent:|--info:|--bad:|--bg-2:|--r-2:|--f-mono:' backend/app/static/app.css`
Expected: every token resolves (these are the `:root` tokens the prototype copied). If any name differs, adjust the rules below to the real token name.

- [ ] **Step 2: Append the page-scoped styles**

Append to `backend/app/static/app.css` (a clearly-commented block; new classes are page-scoped under `.batches-page`/`.batch-tbl`, tokens only, `.btn` reused — no `*-btn`, no raw hex):

```css
/* ─── Batches hub (/batches) ─────────────────────────────────────────── */
.batches-page { display: flex; flex-direction: column; height: 100%; min-height: 0; }
.batches-scroll { overflow: auto; flex: 1; min-height: 0; padding: 0 20px 28px; }
.batches-empty { padding: 48px; text-align: center; color: var(--text-3); }

.batch-tbl { width: 100%; border-collapse: collapse; }
.batch-tbl thead th {
  position: sticky; top: 0; z-index: 1; text-align: left;
  background: var(--panel); border-bottom: 1px solid var(--line);
  padding: 9px 12px; font-size: 10.5px; letter-spacing: 0.06em;
  text-transform: uppercase; color: var(--text-3); font-weight: 600;
}
.batch-tbl tbody td { padding: 12px; border-bottom: 1px solid var(--line); vertical-align: middle; font-size: 13px; }
.batch-tbl tbody tr:hover { background: var(--hover); }
.bt-id { display: flex; flex-direction: column; gap: 2px; }
.bt-id .bt-num { font-family: var(--f-mono); color: var(--text-3); font-size: 11px; }
.bt-id .bt-prompt { font-weight: 500; }
.bt-meta { color: var(--text-3); font-size: 11px; }
.bt-when { color: var(--text-2); font-family: var(--f-mono); font-size: 12px; white-space: nowrap; }
.bt-ran { font-family: var(--f-mono); }

.bcell-prog { min-width: 130px; }
.miniprog { display: flex; flex-direction: column; gap: 4px; }
.miniprog .mp-label { font-family: var(--f-mono); font-size: 11.5px; color: var(--text-2); display: flex; gap: 6px; align-items: baseline; }
.miniprog .mp-bar { height: 5px; border-radius: 3px; background: var(--surface); overflow: hidden; }
.miniprog .mp-bar > span { display: block; height: 100%; border-radius: 3px; transition: width 0.4s; }
.miniprog .mp-bar > span.fill-done { background: var(--good); }
.miniprog .mp-bar > span.fill-rev { background: var(--accent); }
.miniprog .mp-bar > span.fill-run { background: var(--info); }

.bt-actions { text-align: right; white-space: nowrap; display: flex; gap: 6px; justify-content: flex-end; }
.mp-fail-btn { background: none; border: 0; padding: 0; color: var(--bad); cursor: pointer; font-family: var(--f-mono); font-size: 11.5px; }
.mp-fail-btn:hover { text-decoration: underline; }
.bt-detail > td { padding: 0 12px 14px; background: var(--bg-2); border-bottom: 1px solid var(--line); }
.bt-fails { border: 1px solid color-mix(in oklab, var(--bad) 30%, var(--line)); border-radius: var(--r-2); background: var(--panel); padding: 10px 12px; display: flex; flex-direction: column; gap: 2px; }
.bt-fails-h { display: flex; align-items: center; gap: 8px; padding-bottom: 6px; font-size: 10.5px; text-transform: uppercase; letter-spacing: 0.06em; color: var(--text-3); }
.bt-fail-row { display: flex; align-items: center; gap: 11px; padding: 8px 0; border-top: 1px solid var(--line); }
.bt-fail-name { font-size: 12.5px; }
.bt-fail-err { font-size: 11px; color: var(--bad); font-family: var(--f-mono); margin-top: 2px; }
```

- [ ] **Step 3: Smoke-check the page still renders**

Run: `.venv/bin/pytest tests/integration/test_routes_batches.py::test_batches_page_renders -q`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add backend/app/static/app.css
git commit -m "style(batches): page-scoped batch table + progress + fail-row styles"
```

---

## Task 10: Full verification, manual acceptance, ADR

**Files:**
- Create: `docs/adr/NNNN-batches-hub.md` (NNNN = one higher than the last ADR)
- Modify: `docs/decisions.md`

- [ ] **Step 1: Run the full backend test suite + linters**

Run:
```bash
.venv/bin/pytest -q
.venv/bin/lint-imports
.venv/bin/ruff check backend tests
```
Expected: all green. In particular the guardrail tests stay green:
`tests/unit/test_context_delegation.py`, `tests/unit/test_no_x_data_stack.py`,
`tests/unit/test_htmx_alpine_single_lifecycle.py`, `tests/unit/test_templates_shared.py`,
`tests/unit/test_no_sync_fs_in_async.py`, `tests/integration/test_clips_page_perf.py`.

If `ruff`/`lint-imports` are invoked differently in this repo, use the project's configured commands (check `pyproject.toml` / `Makefile`).

- [ ] **Step 2: Manual acceptance (run the app once, follow the spec flows)**

Use the `server-start` skill to launch the dev server (single-instance + graceful-shutdown discipline; CatDV seat is scarce). Then walk the spec's 10 flows — minimally:

1. `/batches` renders metric strip + one row per run_group (prompt·v·model, started, ran, Completed bar, Reviewed bar, status pill); Batches rail icon active.
2. Batches rail icon present + navigates from `/`, `/prompts`, `/studio`, `/cache`.
3. Start a batch via clips-list **Annotate selected**; `/batches` shows it running and advancing without manual refresh; settles to "Awaiting review".
4. Expand "· N failed ▾"; **Retry all failed** (online) re-runs only failed clips; counts update; success toast; no reload.
5. **Retry** a single failed clip → only that clip re-runs.
6. Offline **Retry** → error toast (503); rest of page still renders.
7. **Review →** lands on the clips list filtered to the batch's awaiting clips.
8. **+ New batch** → clips list.
9. Empty DB → empty-state message.

Then stop the server with the `server-stop` skill (SIGTERM; confirm the seat-release log lines).

- [ ] **Step 3: Write the ADR**

Find the last ADR number (`ls docs/adr | sort | tail -1`), create `docs/adr/NNNN-batches-hub.md` (NNNN = last + 1) in MADR-lite format documenting the load-bearing calls:
- "batch" = jobs grouped by `run_group` (singletons `job:<id>`); one row per run_group with multi-prompt summarized.
- Read path is pure-DB / offline-safe (`get_core_ctx`); retry reuses `run_job` (already re-runs `error` items) gated on `get_live_ctx`.
- "+ New batch" reuses the clips-list Annotate-selected flow rather than a second picker (CLAUDE.md reuse rule); the prototype's `clipData.js`/`clipList.js` were not ported.
- Live refresh piggybacks the existing `jobs` SSE topic via `htmxAlpine.reinit` + fetch (no `location.reload`, no per-page lifecycle hand-rolling).

Use any existing ADR (e.g. `docs/adr/0001-*.md`) as the template: `# NNNN. <Title>`, `**Date:** 2026-06-02`, `**Status:** Accepted`, `## Context` / `## Alternatives` / `## Decision` / `## Consequences`.

- [ ] **Step 4: Update the decisions index**

Add a row to the table in `docs/decisions.md` for the new ADR (match the existing column shape).

- [ ] **Step 5: Commit**

```bash
git add docs/adr docs/decisions.md
git commit -m "docs(batches): ADR for the batches hub design calls"
```

- [ ] **Step 6: Open the PR**

Push the branch and open a PR (per the project's PR workflow — non-trivial work lands via PR, main stays clean):

```bash
git push -u origin feat/batches-hub
gh pr create --fill --base main
```

Then use the `requesting-code-review` skill to verify the work meets the spec before merge.

---

## Self-Review

**Spec coverage:**
- Rail entry on every page → Task 7. ✓
- Overview metric strip (4 metrics, "N shown", "N batches") → Task 6 (`_load_batches_ctx`) + Task 8 (template). ✓
- One-row-per-run_group history table (prompt·v·model, started, ran, Completed bar w/ failed in red, Reviewed bar, status pill, actions) → Tasks 1, 4, 8. ✓
- Multi-prompt label `"<name> + N more"` → Task 4 (`batch_view`) + Task 1 (`prompt_count`). ✓
- Failed-clip inspect + retry (all / one) folding back into tallies → Tasks 2 (data), 5 (`only_clip_ids`), 6 (route), 8 (UI). ✓
- Review hand-off scoped to the batch (existing `batch=` filter) → Task 4 (`review_href`). ✓
- "+ New batch" → clips list → Task 8 (`ui.button(href='/')`). ✓
- Live updates via the `jobs` SSE topic → Task 8 (`batchesPage` controller). ✓
- Offline-safe read path; retry → typed 503 → Task 6 (`get_core_ctx` vs `get_live_ctx` + proxy guard). ✓
- N+1 guard → Task 3. ✓
- Empty state → Task 8 (`_batches_table.html`). ✓
- ADR + decisions index → Task 10. ✓

**Placeholder scan:** No TBD/TODO; every code step shows complete code. The only deferred literal is the ADR number (`NNNN`), resolved in Task 10 Step 3 by reading the last ADR. ✓

**Type consistency:** `list_batches` row keys (`batch_key`, `primary_job_id`, `started_at`, `job_ids`, `prompt_count`, `running_jobs`, `prompt_name`, `version_num`, `model`, `ran`, `failed`, `completed`, `in_flight`, `awaiting_clips`) are produced in Task 1 and consumed verbatim by `batch_view` (Task 4) and `_load_batches_ctx` (Task 6). `failed_items_for_jobs` keys (`job_id`, `catdv_clip_id`, `error_message`, `clip_name`) produced in Task 2, consumed in Task 6. `batch_view` output keys (`batch_key`, `id`, `job_ids`, `prompt`, `version`, `model`, `started`, `ran`, `completed`, `failed`, `reviewed`, `awaiting`, `running`, `pct_done`, `pct_reviewed`, `status_state`, `status_label`, `review_href`, + `fails` added in Task 6) are exactly the names used in the Task 8 templates. `start_job_in_background(core, live, job_id, *, only_clip_ids)` defined in Task 5, called identically in Task 6. ✓
