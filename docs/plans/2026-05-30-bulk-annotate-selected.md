# Bulk "Annotate selected" Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an "Annotate selected" action to the clips list that runs production prompts across multiple selected clips — one prompt per media kind — with a persistent top-bar progress indicator visible from any page.

**Architecture:** The multi-clip job machinery already exists (`POST /api/jobs` + `annotator.run_job`). We add (a) a global `jobs` SSE topic + an active-jobs query so a topbar indicator can aggregate progress across jobs, and (b) a clips-list picker that groups the selection by media kind and creates **one job per assigned kind**. No DB schema changes.

**Tech Stack:** FastAPI + aiosqlite (backend), Jinja partials + Alpine.js + HTMX + SSE (frontend), pytest (`pytest-asyncio`).

**Spec:** `docs/specs/2026-05-30-bulk-annotate-selected-design.md`

**Branch / worktree:** `feat/bulk-annotate-selected` at `.claude/worktrees/bulk-annotate` (already created). Run all commands from that worktree root.

---

## File structure

**Backend (modify):**
- `backend/app/repositories/jobs.py` — add `list_running()` and `progress()`.
- `backend/app/services/annotator.py` — add `publish_job_progress()` helper; call it from `run_job` on start / per-item / terminal.
- `backend/app/routes/events.py` — add `GET /api/jobs/events` (global `jobs` topic stream).
- `backend/app/routes/jobs.py` — add `GET /api/jobs/active`.

**Frontend (create):**
- `backend/app/static/bulkAnnotate.js` — Alpine mixin: group selection by kind, load prompts, run one job per kind.
- `backend/app/static/jobsIndicator.js` — Alpine component for the topbar indicator.
- `backend/app/templates/pages/_bulk_annotate_modal.html` — picker modal markup.

**Frontend (modify):**
- `backend/app/templates/pages/clips.html` — Actions menu item + include modal + compose mixin into `bulkSel()`.
- `backend/app/templates/pages/_topbar_pills.html` — indicator markup.
- `backend/app/templates/pages/layout.html` — load the two new JS files.
- `backend/app/static/app.css` — indicator + modal styling (tokens only).

**Tests:**
- `tests/integration/test_jobs_repo.py` — `list_running` / `progress`.
- `tests/unit/test_annotator_job_progress.py` (new) — `publish_job_progress`.
- `tests/integration/test_routes_jobs_active.py` (new) — `/api/jobs/active`.
- `tests/unit/test_bulk_annotate_wiring.py` (new) — template/static wiring guard.

---

## Task 1: JobsRepo — running jobs + progress counts

**Files:**
- Modify: `backend/app/repositories/jobs.py`
- Test: `tests/integration/test_jobs_repo.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/integration/test_jobs_repo.py`:

```python
@pytest.mark.asyncio
async def test_progress_counts_done_and_errors(db):
    vid = await _seed_version(db)
    jobs = JobsRepo()
    job_id = await jobs.create_job(db, prompt_version_id=vid, clip_ids=[1, 2, 3, 4])
    items = await jobs.list_items(db, job_id)
    # one finished ok, one errored, one mid-flight, one pending
    await jobs.update_item_status(db, items[0].id, "review_ready")
    await jobs.update_item_status(db, items[1].id, "error", error="boom")
    await jobs.update_item_status(db, items[2].id, "uploading")

    done, total, errors = await jobs.progress(db, job_id)
    assert (done, total, errors) == (2, 4, 1)


@pytest.mark.asyncio
async def test_list_running_returns_only_running_jobs(db):
    vid = await _seed_version(db)
    jobs = JobsRepo()
    running_id = await jobs.create_job(db, prompt_version_id=vid, clip_ids=[1])
    done_id = await jobs.create_job(db, prompt_version_id=vid, clip_ids=[2])
    await jobs.update_status(db, running_id, "running")
    await jobs.update_status(db, done_id, "completed")

    running = await jobs.list_running(db)
    assert [j.id for j in running] == [running_id]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/integration/test_jobs_repo.py -k "progress or running" -v`
Expected: FAIL — `AttributeError: 'JobsRepo' object has no attribute 'progress'`.

- [ ] **Step 3: Implement the methods**

In `backend/app/repositories/jobs.py`, add inside `class JobsRepo` (e.g. after `list_jobs`):

```python
    async def list_running(self, conn: aiosqlite.Connection) -> list[Job]:
        cur = await conn.execute(
            "SELECT id, prompt_version_id, status, total_clips, notes, kind "
            "FROM jobs WHERE status = 'running' ORDER BY id DESC",
        )
        return [
            Job(
                id=r[0], prompt_version_id=r[1], status=r[2],
                total_clips=r[3], notes=r[4], kind=r[5],
            )
            for r in await cur.fetchall()
        ]

    async def progress(
        self, conn: aiosqlite.Connection, job_id: int
    ) -> tuple[int, int, int]:
        """(done, total, errors) for a job. 'done' = items past the
        in-flight statuses (pending/resolving/uploading/prompting)."""
        cur = await conn.execute(
            """
            SELECT
              SUM(CASE WHEN status NOT IN
                  ('pending','resolving','uploading','prompting') THEN 1 ELSE 0 END),
              COUNT(*),
              SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END)
            FROM job_items WHERE job_id = ?
            """,
            (job_id,),
        )
        row = await cur.fetchone()
        return (int(row[0] or 0), int(row[1] or 0), int(row[2] or 0))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/integration/test_jobs_repo.py -k "progress or running" -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add backend/app/repositories/jobs.py tests/integration/test_jobs_repo.py
git commit -m "feat(jobs): add JobsRepo.list_running and progress helpers"
```

---

## Task 2: Annotator — publish job-level progress to the global `jobs` topic

**Files:**
- Modify: `backend/app/services/annotator.py`
- Test: `tests/unit/test_annotator_job_progress.py` (create)

The helper is unit-tested in isolation (no Gemini/CatDV). Wiring it into `run_job` is mechanical and exercised by the manual acceptance flows.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_annotator_job_progress.py`:

```python
import pytest

from backend.app.repositories.jobs import JobsRepo
from backend.app.repositories.prompts import PromptsRepo
from backend.app.services.annotator import publish_job_progress
from backend.app.services.events import EventBus


async def _seed_job(db) -> int:
    prompts = PromptsRepo()
    _, vid = await prompts.create_with_initial_version(
        db, name="t", description=None, body="p",
        target_map={"x": {"kind": "markers"}}, output_schema={}, model="m",
    )
    jobs = JobsRepo()
    return await jobs.create_job(db, prompt_version_id=vid, clip_ids=[1, 2])


@pytest.mark.asyncio
async def test_publish_job_progress_emits_to_global_topic(db):
    job_id = await _seed_job(db)
    bus = EventBus()
    q = bus.subscribe("jobs")

    await publish_job_progress(bus, JobsRepo(), db, job_id, status="running")

    payload = q.get_nowait()
    assert payload["job_id"] == job_id
    assert payload["status"] == "running"
    assert payload["total"] == 2
    assert payload["done"] == 0
    assert payload["errors"] == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_annotator_job_progress.py -v`
Expected: FAIL — `ImportError: cannot import name 'publish_job_progress'`.

- [ ] **Step 3: Add the helper and wire it into `run_job`**

In `backend/app/services/annotator.py`, add a module-level helper (near the top, after imports):

```python
JOBS_TOPIC = "jobs"


async def publish_job_progress(
    event_bus: EventBus,
    jobs_repo: JobsRepo,
    db: aiosqlite.Connection,
    job_id: int,
    *,
    status: str,
) -> None:
    """Publish job-level progress to the global `jobs` topic so the topbar
    indicator can aggregate across all active jobs."""
    done, total, errors = await jobs_repo.progress(db, job_id)
    await event_bus.publish(
        JOBS_TOPIC,
        {
            "job_id": job_id,
            "status": status,
            "done": done,
            "total": total,
            "errors": errors,
        },
    )
```

Then wire three calls into `run_job` (`backend/app/services/annotator.py`):

After `await jobs_repo.update_status(db, job_id, "running")` (currently line ~76):

```python
    await publish_job_progress(event_bus, jobs_repo, db, job_id, status="running")
```

Inside the `for item in items:` loop, as the **last statement of the loop body** (after the try/except block, so it fires whether the item succeeded or errored):

```python
        await publish_job_progress(event_bus, jobs_repo, db, job_id, status="running")
```

After the final `await jobs_repo.update_status(db, job_id, final_status)` (currently line ~137):

```python
    await publish_job_progress(event_bus, jobs_repo, db, job_id, status=final_status)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/test_annotator_job_progress.py -v`
Expected: PASS.

- [ ] **Step 5: Run the annotator suite to confirm no regression**

Run: `.venv/bin/python -m pytest tests/unit/test_annotator_studio_branch.py -v`
Expected: PASS (existing tests unaffected).

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/annotator.py tests/unit/test_annotator_job_progress.py
git commit -m "feat(annotator): publish job-level progress to global jobs topic"
```

---

## Task 3: Routes — `/api/jobs/active` and `/api/jobs/events`

**Files:**
- Modify: `backend/app/routes/jobs.py`, `backend/app/routes/events.py`
- Test: `tests/integration/test_routes_jobs_active.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_routes_jobs_active.py`:

```python
import importlib

from fastapi.testclient import TestClient


def _setenv(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("CATDV_BASE_URL", "http://localhost:0")
    monkeypatch.setenv("CATDV_USERNAME", "")
    monkeypatch.setenv("CATDV_PASSWORD", "p")
    monkeypatch.setenv("CATDV_CATALOG_ID", "881507")
    monkeypatch.setenv("GCP_PROJECT_ID", "p")
    monkeypatch.setenv("GCS_BUCKET_NAME", "b")
    monkeypatch.setenv("PROXY_SOURCE", "rest")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))


def _make_app(monkeypatch, tmp_path):
    _setenv(monkeypatch, tmp_path)
    from backend.app import main as main_mod

    importlib.reload(main_mod)
    return main_mod.app


def test_active_jobs_lists_running_with_progress(monkeypatch, tmp_path):
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        ctx = client.app.state.ctx
        import anyio

        async def seed():
            from backend.app.repositories.prompts import PromptsRepo
            _, vid = await PromptsRepo().create_with_initial_version(
                ctx.db, name="t", description=None, body="p",
                target_map={"x": {"kind": "markers"}}, output_schema={}, model="m",
            )
            jid = await ctx.jobs_repo.create_job(
                ctx.db, prompt_version_id=vid, clip_ids=[1, 2], kind="video"
            )
            await ctx.jobs_repo.update_status(ctx.db, jid, "running")
            return jid

        jid = anyio.from_thread.run(seed) if False else None  # see note
        # ctx.db coroutines run on the app loop; use the client's portal:
        import asyncio
        jid = asyncio.get_event_loop().run_until_complete(seed())

        r = client.get("/api/jobs/active")

    assert r.status_code == 200
    body = r.json()
    assert len(body) == 1
    assert body[0]["id"] == jid
    assert body[0]["total"] == 2
    assert body[0]["done"] == 0
    assert body[0]["kind"] == "video"
```

> Note: if `asyncio.get_event_loop().run_until_complete(seed())` raises a
> "loop already running" error under the TestClient, replace the seeding
> with direct repo calls wrapped via `client.portal.call(seed)` (Starlette
> exposes the anyio portal in newer versions) — or seed by POSTing to
> `/api/jobs` with `auto_start=False` and then PATCHing status. Pick
> whichever the existing integration tests in this repo already use for
> async DB seeding; mirror that pattern rather than inventing one.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/integration/test_routes_jobs_active.py -v`
Expected: FAIL — 404 on `/api/jobs/active`.

- [ ] **Step 3: Implement `/api/jobs/active`**

In `backend/app/routes/jobs.py`, add a route. Place it **before** `get_job` so `/active` is not captured by the `/{job_id}` path:

```python
@router.get("/active")
async def list_active_jobs(request: Request):
    """Running jobs with progress counts — powers the topbar indicator."""
    ctx = get_ctx(request)
    out = []
    for job in await ctx.jobs_repo.list_running(ctx.db):
        done, total, errors = await ctx.jobs_repo.progress(ctx.db, job.id)
        out.append(
            {
                "id": job.id,
                "kind": job.kind,
                "status": job.status,
                "done": done,
                "total": total,
                "errors": errors,
            }
        )
    return out
```

- [ ] **Step 4: Implement `/api/jobs/events`**

In `backend/app/routes/events.py`, add (mirrors `job_events`, but on the global topic):

```python
@router.get("/api/jobs/events")
async def jobs_events(request: Request):
    ctx = get_ctx(request)

    async def stream():
        async for frame in _event_generator(ctx.event_bus, topic="jobs"):
            if await request.is_disconnected():
                return
            yield {"data": frame.removeprefix("data: ").rstrip("\n")}

    return EventSourceResponse(stream())
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/integration/test_routes_jobs_active.py -v`
Expected: PASS.

- [ ] **Step 6: Run the broader route suite**

Run: `.venv/bin/python -m pytest tests/integration/test_routes_events.py tests/integration/test_routes_jobs_active.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/app/routes/jobs.py backend/app/routes/events.py tests/integration/test_routes_jobs_active.py
git commit -m "feat(jobs): add /api/jobs/active and global /api/jobs/events stream"
```

---

## Task 4: Frontend — bulk-annotate picker (modal + mixin + Actions item)

**Files:**
- Create: `backend/app/static/bulkAnnotate.js`, `backend/app/templates/pages/_bulk_annotate_modal.html`
- Modify: `backend/app/templates/pages/clips.html`, `backend/app/templates/pages/layout.html`, `backend/app/static/app.css`

No JS unit-test harness exists in this repo, so this task is verified by the manual acceptance flows (and the wiring guard in Task 6). Keep parity with `clipAnnotate.js`.

- [ ] **Step 1: Create the Alpine mixin**

Create `backend/app/static/bulkAnnotate.js`:

```javascript
// Bulk "Annotate selected" — composed into bulkSel() on the clips list.
// Groups the current selection by media kind, lets the user assign one
// production prompt per kind, then creates one /api/jobs job per assigned
// kind. Mirrors the prompt-loading + job-kickoff logic in clipAnnotate.js.
function bulkAnnotateMixin() {
  return {
    annoOpen: false,
    annoLoading: false,
    annoError: null,
    // [{ kind, clipIds: [int], promptVersionId: int|null }]
    annoGroups: [],
    annoPromptsByKind: {}, // kind -> [{id, name, current_production_version_id}]

    async openAnnotate() {
      // Group selected rows by their media kind (read from the .col-type cell,
      // same approach reviewSelected() uses for .col-drafts).
      const groups = {};
      for (const el of this._selected()) {
        const id = parseInt(el.value.split("/")[1], 10);
        if (isNaN(id)) continue;
        const kind = (
          el.closest("tr")?.querySelector(".col-type")?.textContent || "video"
        ).trim();
        (groups[kind] ||= []).push(id);
      }
      this.annoGroups = Object.entries(groups).map(([kind, clipIds]) => ({
        kind,
        clipIds,
        promptVersionId: null,
      }));
      if (!this.annoGroups.length) return;
      this.annoOpen = true;
      this.annoError = null;
      await this._loadPrompts();
    },

    async _loadPrompts() {
      this.annoLoading = true;
      try {
        const r = await fetch("/api/prompts?archived=0");
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const all = (await r.json()).filter(
          (p) => p.current_production_version_id != null,
        );
        for (const g of this.annoGroups) {
          this.annoPromptsByKind[g.kind] = all.filter(
            (p) => p.media_kind === g.kind || p.media_kind === "any",
          );
        }
      } catch (e) {
        this.annoError = String(e);
      } finally {
        this.annoLoading = false;
      }
    },

    annoSkippedCount() {
      return this.annoGroups
        .filter((g) => !g.promptVersionId)
        .reduce((n, g) => n + g.clipIds.length, 0);
    },
    annoRunCount() {
      return this.annoGroups
        .filter((g) => g.promptVersionId)
        .reduce((n, g) => n + g.clipIds.length, 0);
    },
    annoRunnable() {
      return this.annoGroups.some((g) => g.promptVersionId);
    },

    async runAnnotate() {
      if (!this.annoRunnable()) return;
      for (const g of this.annoGroups) {
        if (!g.promptVersionId) continue;
        await fetch("/api/jobs", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            prompt_version_id: g.promptVersionId,
            clip_ids: g.clipIds,
            auto_start: true,
          }),
        });
      }
      this.annoOpen = false;
      // Nudge the topbar indicator to pick up the new jobs immediately.
      window.dispatchEvent(new CustomEvent("jobs-changed"));
    },
  };
}
window.bulkAnnotateMixin = bulkAnnotateMixin;
```

- [ ] **Step 2: Create the modal partial**

Create `backend/app/templates/pages/_bulk_annotate_modal.html`:

```html
{# Bulk-annotate picker. Lives inside the page-clips x-data="bulkSel()" root
   so it shares the selection state. One prompt dropdown per media kind in
   the current selection; unassigned kinds are flagged as skipped. #}
<div class="modal-backdrop" x-show="annoOpen" x-cloak
     @keydown.escape.window="annoOpen = false" @click.self="annoOpen = false">
  <div class="modal-card" role="dialog" aria-label="Annotate selected clips">
    <h2 class="modal-title">Annotate selected</h2>

    <template x-if="annoLoading"><p class="muted">Loading prompts…</p></template>
    <template x-if="annoError"><p class="error" x-text="annoError"></p></template>

    <template x-if="!annoLoading">
      <div class="ba-groups">
        <template x-for="g in annoGroups" :key="g.kind">
          <div class="ba-group">
            <div class="ba-group-head">
              <span class="tag mono" x-text="g.kind"></span>
              <span class="muted" x-text="g.clipIds.length + ' clip(s)'"></span>
            </div>
            <template x-if="(annoPromptsByKind[g.kind] || []).length">
              <select x-model.number="g.promptVersionId" class="ba-select">
                <option :value="null">— skip this kind —</option>
                <template x-for="p in annoPromptsByKind[g.kind]" :key="p.id">
                  <option :value="p.current_production_version_id"
                          x-text="p.name"></option>
                </template>
              </select>
            </template>
            <template x-if="!(annoPromptsByKind[g.kind] || []).length">
              <p class="ba-skip muted">
                No compatible production prompt — these clips will be skipped.
              </p>
            </template>
          </div>
        </template>
      </div>
    </template>

    <template x-if="!annoLoading && annoSkippedCount() > 0">
      <p class="ba-skip-note error"
         x-text="annoSkippedCount() + ' clip(s) will be skipped (no prompt assigned).'">
      </p>
    </template>

    <div class="modal-actions">
      <button type="button" class="btn ghost" @click="annoOpen = false">Cancel</button>
      <button type="button" class="btn primary" :disabled="!annoRunnable()"
              @click="runAnnotate()"
              x-text="annoRunCount() ? ('Annotate ' + annoRunCount() + ' clip(s)' +
                       (annoSkippedCount() ? ' (skipping ' + annoSkippedCount() + ')' : ''))
                       : 'Select a prompt'">
      </button>
    </div>
  </div>
</div>
```

- [ ] **Step 3: Wire the mixin, Actions item, and include into `clips.html`**

In `backend/app/templates/pages/clips.html`:

(a) Compose the mixin into `bulkSel()` — change the `return` at line ~181 from
`return Object.assign(rowSelect(), {` to:

```javascript
    return Object.assign(rowSelect(), bulkAnnotateMixin(), {
```

(b) Add the Actions menu item. After the `Apply drafts (selected)` button (line ~103) and before the `<div class="actions-sep">`:

```html
          <button type="button" class="actions-item" @click="open = false; openAnnotate()">
            Annotate selected →
          </button>
```

(c) Include the modal inside the `page-clips` root — add just before the closing `</div>` of `<div class="page page-clips" ...>` (line ~125, after the `_clips_tbody.html` include):

```html
  {% include "pages/_bulk_annotate_modal.html" %}
```

- [ ] **Step 4: Load the script in `layout.html`**

In `backend/app/templates/pages/layout.html`, after the `clipAnnotate.js` line (line 14) add:

```html
  <script defer src="/static/bulkAnnotate.js"></script>
```

- [ ] **Step 5: Add modal styling to `app.css`**

Append to `backend/app/static/app.css` (reuse existing tokens — do not add raw hex; if `.modal-backdrop`/`.modal-card` already exist in app.css, skip those and keep only the `.ba-*` rules):

```css
/* Bulk-annotate picker */
.modal-backdrop {
  position: fixed; inset: 0; z-index: 50;
  background: rgba(0, 0, 0, 0.5);
  display: flex; align-items: center; justify-content: center;
}
.modal-card {
  background: var(--surface); color: var(--text);
  border: 1px solid var(--border); border-radius: var(--radius);
  padding: var(--space-4); min-width: 360px; max-width: 480px;
}
.modal-title { margin: 0 0 var(--space-3); }
.modal-actions { display: flex; justify-content: flex-end; gap: var(--space-2); margin-top: var(--space-4); }
.ba-groups { display: flex; flex-direction: column; gap: var(--space-3); }
.ba-group-head { display: flex; align-items: center; gap: var(--space-2); margin-bottom: var(--space-1); }
.ba-select { width: 100%; }
.ba-skip-note { margin-top: var(--space-3); }
```

> Before writing these, grep `app.css` for `--surface`, `--border`,
> `--radius`, `--space-*` and use the names that actually exist; if the
> repo names them differently (e.g. `--bg-elevated`), use those.

- [ ] **Step 6: Manual smoke**

Start the dev server (use the `server-start` skill). On `/`, select 2+ clips of differing kinds, open **Actions → Annotate selected**, confirm the modal lists one row per kind with prompt dropdowns and a skip note for unassigned kinds. Don't run yet (Task 5 adds the indicator). Stop the server with `server-stop`.

- [ ] **Step 7: Commit**

```bash
git add backend/app/static/bulkAnnotate.js backend/app/templates/pages/_bulk_annotate_modal.html \
        backend/app/templates/pages/clips.html backend/app/templates/pages/layout.html \
        backend/app/static/app.css
git commit -m "feat(clips): add 'Annotate selected' per-kind picker to Actions menu"
```

---

## Task 5: Frontend — persistent top-bar batch indicator

**Files:**
- Create: `backend/app/static/jobsIndicator.js`
- Modify: `backend/app/templates/pages/_topbar_pills.html`, `backend/app/templates/pages/layout.html`, `backend/app/static/app.css`

- [ ] **Step 1: Create the indicator component**

Create `backend/app/static/jobsIndicator.js`:

```javascript
// Persistent top-bar batch indicator. Aggregates progress across all active
// jobs. Renders on every page (lives in the topbar pillset), so it re-derives
// state from /api/jobs/active on load, then live-updates via /api/jobs/events.
function jobsIndicator() {
  return {
    jobs: {}, // id -> { done, total, errors, status }
    failed: false,

    async init() {
      await this.refresh();
      window.addEventListener("jobs-changed", () => this.refresh());
      const es = new EventSource("/api/jobs/events");
      es.onmessage = (evt) => {
        let p;
        try { p = JSON.parse(evt.data); } catch { return; }
        if (["completed", "cancelled"].includes(p.status) && !p.errors) {
          delete this.jobs[p.job_id];
        } else {
          this.jobs[p.job_id] = {
            done: p.done, total: p.total, errors: p.errors, status: p.status,
          };
          if (p.status === "failed" || p.errors) this.failed = true;
        }
      };
    },

    async refresh() {
      try {
        const r = await fetch("/api/jobs/active");
        if (!r.ok) return;
        const list = await r.json();
        const next = {};
        for (const j of list) {
          next[j.id] = { done: j.done, total: j.total, errors: j.errors, status: j.status };
        }
        this.jobs = next;
      } catch { /* offline — leave current state */ }
    },

    activeIds() { return Object.keys(this.jobs).map(Number); },
    visible() { return this.activeIds().length > 0 || this.failed; },
    done() { return this.activeIds().reduce((n, id) => n + (this.jobs[id].done || 0), 0); },
    total() { return this.activeIds().reduce((n, id) => n + (this.jobs[id].total || 0), 0); },
    hasErrors() {
      return this.failed ||
        this.activeIds().some((id) => (this.jobs[id].errors || 0) > 0);
    },

    open() {
      const ids = this.activeIds();
      const target = ids.length ? ids[0] : null;
      window.location.href = target ? `/?batch=${target}` : "/";
    },

    async cancel() {
      for (const id of this.activeIds()) {
        await fetch(`/api/jobs/${id}/cancel`, { method: "POST" });
      }
      await this.refresh();
      this.failed = false;
    },

    dismiss() { this.failed = false; this.jobs = {}; },
  };
}
window.jobsIndicator = jobsIndicator;
```

- [ ] **Step 2: Add indicator markup to the topbar**

In `backend/app/templates/pages/_topbar_pills.html`, add as the **first** child of `<span class="pillset">` (before `_connection_chip`):

```html
  <span class="job-indicator" x-data="jobsIndicator()" x-show="visible()" x-cloak
        :class="{ 'job-indicator--error': hasErrors() }">
    <button type="button" class="job-indicator-main" @click="open()"
            :title="hasErrors() ? 'Batch finished with errors — click to view' : 'Click to view batch'">
      <span class="ji-spinner" x-show="!hasErrors() && total() > done()" aria-hidden="true"></span>
      <span x-show="!hasErrors()" x-text="'Annotating ' + done() + '/' + total()"></span>
      <span x-show="hasErrors()">Annotation failed</span>
    </button>
    <button type="button" class="job-indicator-x" @click="hasErrors() ? dismiss() : cancel()"
            :title="hasErrors() ? 'Dismiss' : 'Cancel batch'">✕</button>
  </span>
```

- [ ] **Step 3: Load the script in `layout.html`**

In `backend/app/templates/pages/layout.html`, after the `bulkAnnotate.js` line add:

```html
  <script defer src="/static/jobsIndicator.js"></script>
```

- [ ] **Step 4: Style the indicator in `app.css`**

Append to `backend/app/static/app.css` (tokens only; mirror existing `.env-pill` / `.conn-chip` pill styling — open those rules first and match their padding/radius/border conventions):

```css
/* Top-bar batch indicator */
.job-indicator { display: inline-flex; align-items: center; gap: var(--space-1); }
.job-indicator-main, .job-indicator-x {
  border: 1px solid var(--border); background: var(--surface); color: var(--text);
  border-radius: var(--radius); padding: 2px var(--space-2); cursor: pointer;
}
.job-indicator--error .job-indicator-main { border-color: var(--danger); color: var(--danger); }
.ji-spinner {
  display: inline-block; width: 10px; height: 10px; margin-right: 4px;
  border: 2px solid var(--border); border-top-color: var(--accent);
  border-radius: 50%; animation: ji-spin 0.7s linear infinite;
}
@keyframes ji-spin { to { transform: rotate(360deg); } }
```

> Reuse the existing spinner if one exists — grep app.css for `spin`/
> `spinner` (e.g. `.ca-spinner` referenced in `_annotate_dropdown.html`)
> and reuse that class instead of `ji-spinner` if it's generic.

- [ ] **Step 5: Manual end-to-end smoke**

Start the server (`server-start`). Select mixed-kind clips → Annotate selected → assign prompts → Run. Confirm: indicator appears in the topbar, count advances, navigating to `/prompts` keeps it visible, clicking it lands on `/?batch=<id>`, and ✕ cancels. Stop the server (`server-stop`).

- [ ] **Step 6: Commit**

```bash
git add backend/app/static/jobsIndicator.js backend/app/templates/pages/_topbar_pills.html \
        backend/app/templates/pages/layout.html backend/app/static/app.css
git commit -m "feat(topbar): persistent batch progress indicator with cancel"
```

---

## Task 6: Wiring guard test + ADR + docs

**Files:**
- Create: `tests/unit/test_bulk_annotate_wiring.py`, `docs/adr/NNNN-bulk-annotate-selected.md`
- Modify: `docs/decisions.md`

- [ ] **Step 1: Write the wiring guard test**

Create `tests/unit/test_bulk_annotate_wiring.py` (mirrors the static-asset assertion style of `tests/unit/test_studio_css_no_phantom_tokens.py`):

```python
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TPL = ROOT / "backend" / "app" / "templates" / "pages"
STATIC = ROOT / "backend" / "app" / "static"


def test_clips_actions_menu_has_annotate_selected():
    html = (TPL / "clips.html").read_text()
    assert "openAnnotate()" in html
    assert "bulkAnnotateMixin()" in html
    assert "_bulk_annotate_modal.html" in html


def test_topbar_has_job_indicator():
    html = (TPL / "_topbar_pills.html").read_text()
    assert "jobsIndicator()" in html


def test_new_scripts_loaded_in_layout():
    html = (TPL / "layout.html").read_text()
    assert "bulkAnnotate.js" in html
    assert "jobsIndicator.js" in html


def test_static_files_exist():
    assert (STATIC / "bulkAnnotate.js").exists()
    assert (STATIC / "jobsIndicator.js").exists()
    assert (TPL / "_bulk_annotate_modal.html").exists()
```

- [ ] **Step 2: Run the wiring test**

Run: `.venv/bin/python -m pytest tests/unit/test_bulk_annotate_wiring.py -v`
Expected: PASS (proves Tasks 4–5 wiring is intact).

- [ ] **Step 3: Write the ADR**

Find the next ADR number: `ls docs/adr | sort | tail -1`. Create `docs/adr/NNNN-bulk-annotate-selected.md` (next number) using the MADR-lite format:

```markdown
# NNNN. Bulk "Annotate selected" — one job per media kind

**Date:** 2026-05-30
**Status:** Accepted

## Context

The clips list had no way to run annotations across many clips; only the
single-clip Annotate dropdown existed. A selection is often mixed media
kinds, and a prompt's `media_kind` is video/image/any — one prompt can't
serve every clip.

## Alternatives

1. Extend the `jobs` schema to carry multiple prompts (one per kind) in a
   single job.
2. Create one job per assigned media kind, reusing the existing
   single-prompt job model unchanged.

## Decision

Chose (2): one job per kind. The existing `POST /api/jobs` + `run_job`
already handle a single prompt over many clips, so per-kind jobs reuse all
of it with zero schema change. The new top-bar indicator aggregates
progress across the resulting jobs via a global `jobs` SSE topic and
`GET /api/jobs/active`.

## Consequences

- Several batch entries appear in the Batch filter for one user action
  (one per kind) — acceptable; each is independently reviewable/cancellable.
- Per-kind jobs run as concurrent asyncio tasks sharing the single aiosqlite
  connection; writes are serialized by aiosqlite. Acceptable at current
  scale.
- No DB migration required.
```

- [ ] **Step 4: Update the decisions index**

Add a row to the table in `docs/decisions.md` for the new ADR (match the existing column format — open the file first to see the columns).

- [ ] **Step 5: Run the full test suite**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: PASS (no regressions).

- [ ] **Step 6: Commit**

```bash
git add tests/unit/test_bulk_annotate_wiring.py docs/adr/ docs/decisions.md
git commit -m "test+docs: bulk-annotate wiring guard, ADR, decisions index"
```

---

## Verification (end-to-end, after all tasks)

Run the spec's **Manual acceptance flows** (`docs/specs/2026-05-30-bulk-annotate-selected-design.md`) on a running app. In particular:

1. Start the server via the **server-start** skill (respects the CatDV seat discipline).
2. Mixed-kind selection → Annotate selected → see per-kind dropdowns + skip notice → Run.
3. Indicator shows progress; navigate away → still visible and advancing.
4. Click indicator → lands on `/?batch=<id>`; drafts appear as items finish.
5. ✕ cancels mid-run; failure turns the indicator red and survives navigation.
6. Single-clip Annotate on a clip page is unchanged.
7. Stop the server via the **server-stop** skill and confirm the seat-release log lines.

Then open a PR from `feat/bulk-annotate-selected` into `main` (see the finishing-a-development-branch skill).

---

## Self-review notes (addressed)

- **Spec coverage:** Actions item (T4), per-kind picker + skip-before-run (T4), one job per kind (T4), global stream + active query (T2/T3), topbar indicator with click-to-batch + cancel + red-on-failure (T5), single-clip unaffected (no change to `clipAnnotate.js`; guarded by manual flow 7).
- **`media_kind` reality:** prompts are video/image/any only; audio clips match only `any` prompts — handled by the `=== g.kind || === 'any'` filter and the empty-prompts skip branch.
- **Route ordering:** `/api/jobs/active` registered before `/{job_id}` to avoid path capture (T3 Step 3).
- **Type/name consistency:** `bulkAnnotateMixin` / `openAnnotate` / `jobsIndicator` / `publish_job_progress` / `list_running` / `progress` used identically across tasks and tests.
- **Token caution:** CSS steps instruct grepping for real token/class names before writing, to avoid phantom tokens (the repo has a test guarding against those).
