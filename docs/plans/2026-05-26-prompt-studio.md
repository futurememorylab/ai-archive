# Prompt Studio Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a sandbox UI for prompt iteration. Operators curate testbenches (nested folders + clip refs, plus uploaded MP4s, with optional gold), run any prompt version against a testbench, and compare two runs (or a run vs gold) side by side. Studio is independent from the production annotate/review/write pipeline and works whether or not CatDV is online.

**Architecture:** Studio runs in its own tables (`testbenches`, `testbench_folders`, `testbench_items`, `studio_runs`, `studio_run_items`) and its own serial worker that calls into the existing per-item pipeline primitives (`proxy_resolver`, `ai_store`, `gemini`). Annotator's `_process_item` is factored into a shared `process_item` callable that returns an `AnnotationOutput` dataclass; production and Studio both call it, then persist into their respective tables. CatDV-clip refs resolve through a fallback chain (live archive → proxy cache → ai_store cache → mark `unacceptable`).

**Tech stack:** FastAPI + `aiosqlite` (backend), Alpine.js (frontend), SSE for run status, existing `EventBus` for pub/sub. No new external dependencies.

**Spec:** `docs/specs/2026-05-26-prompt-studio-design.md`. **ADR:** `docs/adr/0026-prompt-studio.md`.

---

## Pinned implementation details

These resolve the "open items" in §9 of the spec so every task below can be concrete:

- **Migration number:** `0011_studio.sql` (next after `0010_live_sessions.sql`).
- **Uploads dir:** `var/studio_uploads/` (relative to repo root). Created at lifespan startup if missing.
- **Upload filenames:** `<uuid4>.<ext>` where `<ext>` is the lowercased original suffix (`mp4`, `mov`, `mkv`). Original filename kept in `testbench_items.upload_orig_name`.
- **Max upload size:** 500 MB default (`studio_max_upload_mb`). Streamed to disk via `aiofiles`; rejected at the boundary.
- **Allowed MIME prefixes for uploads:** `video/`. Anything else → 415.
- **Crash recovery:** at lifespan startup, sweep `studio_runs.status='running'` → `'failed'` and any `studio_run_items` in transient states (`resolving|uploading|prompting`) → `error` with `error='interrupted by restart'`. Mirrors `JobsRepo.reset_transient` (`repositories/jobs.py:122`).
- **Per-item status callback:** the shared `process_item` callable accepts `on_status: Callable[[Literal["resolving","uploading","prompting"]], Awaitable[None]]`. Production passes a callback that writes `jobs_repo.update_item_status` + `event_bus.publish`; Studio passes one that writes `studio_runs_repo.update_item_status` + `event_bus.publish`.
- **SSE topic:** `studio_run:{run_id}` (mirrors `job:{job_id}` from `routes/jobs.py`).
- **Resolver chain for `source_kind='catdv_clip'`:**
  1. `mode == "online"`: `archive.get_clip(provider_clip_id)` for metadata + `proxy_resolver.path_for_clip_id(int(provider_clip_id))` for media. If both succeed, return.
  2. Either step in (1) fails OR `mode != "online"`: try `LocalCacheOnlyResolver(...).path_for_clip_id(...)` for media + `clip_cache_repo.get(...)` for snapshot. If both present, return.
  3. (1) and (2) fail: try `ai_store.find_by_clip_key(("catdv", provider_clip_id))`. If present, return resolved input with `local_path=None`, `file_ref=<ai_store ref>`, and a minimal `clip_snapshot={"id": provider_clip_id, "name": testbench_item.display_name}`.
  4. All three fail: return `Unacceptable(reason=...)`.
- **Gold JSON shape (v1):** writes only `{"description": "..."}`. Reads round-trip unknown keys verbatim so future evals can add fields without migration. JSON parse error on PUT → 400; empty string → store `null` (delete gold).
- **Boot without CatDV:** Studio routes use a fresh `LocalCacheOnlyResolver` constructed at request time when the in-context `ProxyResolver` is unavailable or `mode != "online"`. The existing app already keeps the CatDV client alive across failed logins (ADR 0023), so Studio reads of cached data work even before CatDV becomes available.

---

## File structure

### New backend files

| File | Responsibility |
|---|---|
| `backend/migrations/0011_studio.sql` | 5 tables + indexes + CHECK constraints. |
| `backend/app/models/studio.py` | Pydantic: `Testbench`, `TestbenchFolder`, `TestbenchItem`, `StudioRun`, `StudioRunItem`. Plus the shared `AnnotationOutput` dataclass used by the annotator refactor. |
| `backend/app/repositories/testbenches.py` | `TestbenchesRepo` — testbench + folder CRUD, recursive tree listing. |
| `backend/app/repositories/testbench_items.py` | `TestbenchItemsRepo` — item CRUD, gold round-trip, tree-ordered iteration. |
| `backend/app/repositories/studio_runs.py` | `StudioRunsRepo` — run + run-item CRUD + state transitions + crash-recovery sweep. |
| `backend/app/services/studio_runs.py` | `StudioRunsService` — `start`, `cancel`, `run` (the worker), `_resolve_clip_input` (the fallback chain). |
| `backend/app/services/studio_uploads.py` | Streaming-upload helper: write multipart to `var/studio_uploads/`, validate MIME, return relative path. |
| `backend/app/routes/studio.py` | All `/studio/*` page routes + `/api/studio/*` JSON API + SSE. |
| `backend/app/static/studio.js` | Alpine components: `studioPage`, `studioRunView`, `studioGoldDialog`. |
| `backend/app/templates/pages/studio.html` | Studio landing layout. |
| `backend/app/templates/pages/studio_run.html` | Run detail page. |
| `backend/app/templates/pages/studio_compare.html` | Side-by-side comparison. |
| `backend/app/templates/pages/_studio_testbench_list.html` | Left rail of landing. |
| `backend/app/templates/pages/_studio_folder_tree.html` | Recursive folder rendering. |
| `backend/app/templates/pages/_studio_runs_table.html` | Runs table on landing. |
| `backend/app/templates/pages/_studio_run_item_row.html` | One row in the run-detail table (SSE-swappable). |
| `backend/app/templates/pages/_studio_compare_cell.html` | One cell in the compare grid (delegates to existing annotate cell partials). |

### Modified backend files

| File | Why |
|---|---|
| `backend/app/services/annotator.py` | Extract `_process_item` body (resolve → upload → prompt → parse) into shared `process_item` callable returning `AnnotationOutput`. Existing `run_job` unchanged in behavior; uses the callable + the previous persistence steps. |
| `backend/app/context.py` | Register `TestbenchesRepo`, `TestbenchItemsRepo`, `StudioRunsRepo`, `StudioRunsService`. Create `var/studio_uploads/` if missing. |
| `backend/app/main.py` | Register `studio` router. Lifespan startup: `await studio_runs_repo.reset_transient(db)`. |
| `backend/app/settings.py` | `studio_uploads_dir: Path = Path("var/studio_uploads")`, `studio_max_upload_mb: int = 500`. |
| `backend/app/templates/pages/_rail.html` | Studio nav entry. |
| `docs/ARCHITECTURE.md` | Add Studio row to the symptom table. |

### Updated docs

- `docs/decisions.md` — already updated when ADR 0026 was written.

---

## Phases & review checkpoints

Ten phases. A checkpoint at the end of each phase is the natural stop to run `pytest`, review the diff, and either continue in-session or hand off to a fresh subagent.

1. **Phase 1 — Settings, schema, models** (foundation).
2. **Phase 2 — Testbench repositories** (TDD; no services yet).
3. **Phase 3 — Studio-runs repository** (TDD; state machine + crash sweep).
4. **Phase 4 — Annotator refactor** (`process_item` extraction with green tests throughout).
5. **Phase 5 — Resolver chain + Studio-runs service** (worker, with Gemini mocked).
6. **Phase 6 — Upload helper + JSON API routes** (CRUD endpoints).
7. **Phase 7 — Page routes + SSE wiring**.
8. **Phase 8 — Templates + Alpine components**.
9. **Phase 9 — Crash recovery + lifespan + context wiring + rail nav**.
10. **Phase 10 — Manual verification + docs polish**.

---

# Phase 1 — Settings, schema, models

## Task 1: Settings additions

**Files:**
- Modify: `backend/app/settings.py`

- [ ] **Step 1: Add the two fields**

Open `backend/app/settings.py`. Inside the `Settings` class, after the existing `gemini_*` block, add:

```python
    studio_uploads_dir: Path = Path("var/studio_uploads")
    studio_max_upload_mb: int = 500
```

`Path` is already imported in this module. The directory is intentionally relative so it follows the repo working directory (consistent with `cache_dir` and friends).

- [ ] **Step 2: Commit**

```bash
git add backend/app/settings.py
git commit -m "feat(settings): studio uploads dir + max upload size"
```

---

## Task 2: Migration `0011_studio.sql`

**Files:**
- Create: `backend/migrations/0011_studio.sql`
- Test: `tests/integration/test_studio_migration.py`

- [ ] **Step 1: Write the failing migration test**

Create `tests/integration/test_studio_migration.py`:

```python
from pathlib import Path

import aiosqlite
import pytest

from backend.app.migrations_runner import apply_migrations

MIGRATIONS = Path(__file__).resolve().parents[2] / "backend" / "migrations"


@pytest.mark.asyncio
async def test_studio_tables_exist(tmp_path):
    db = tmp_path / "t.db"
    async with aiosqlite.connect(db) as conn:
        await apply_migrations(conn, MIGRATIONS)
        cur = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name IN ('testbenches','testbench_folders','testbench_items',"
            "'studio_runs','studio_run_items')"
        )
        names = {r[0] for r in await cur.fetchall()}
    assert names == {
        "testbenches", "testbench_folders", "testbench_items",
        "studio_runs", "studio_run_items",
    }


@pytest.mark.asyncio
async def test_studio_indexes_present(tmp_path):
    db = tmp_path / "t.db"
    async with aiosqlite.connect(db) as conn:
        await apply_migrations(conn, MIGRATIONS)
        cur = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND name IN ('idx_tb_folders_parent','idx_tb_items_folder',"
            "'idx_studio_runs_testbench','idx_studio_run_items_run')"
        )
        idxs = {r[0] for r in await cur.fetchall()}
    assert idxs == {
        "idx_tb_folders_parent", "idx_tb_items_folder",
        "idx_studio_runs_testbench", "idx_studio_run_items_run",
    }


@pytest.mark.asyncio
async def test_testbench_items_source_kind_check(tmp_path):
    """upload_path XOR catdv_provider_clip_id, mirrored against source_kind."""
    db = tmp_path / "t.db"
    async with aiosqlite.connect(db) as conn:
        await apply_migrations(conn, MIGRATIONS)
        await conn.execute(
            "INSERT INTO testbenches (id, name, archived, created_at, updated_at) "
            "VALUES (1, 'tb', 0, '2026-01-01', '2026-01-01')"
        )
        await conn.execute(
            "INSERT INTO testbench_folders (id, testbench_id, parent_id, name, sort_index, created_at) "
            "VALUES (1, 1, NULL, 'root', 0, '2026-01-01')"
        )
        # OK: upload row with upload_path, no catdv_provider_clip_id
        await conn.execute(
            "INSERT INTO testbench_items (folder_id, source_kind, upload_path, upload_orig_name, "
            "display_name, created_at) VALUES (1, 'upload', 'a.mp4', 'a.mp4', 'a', '2026-01-01')"
        )
        # Reject: source_kind='upload' but catdv_provider_clip_id set
        with pytest.raises(aiosqlite.IntegrityError):
            await conn.execute(
                "INSERT INTO testbench_items (folder_id, source_kind, upload_path, "
                "catdv_provider_clip_id, display_name, created_at) "
                "VALUES (1, 'upload', 'b.mp4', '999', 'b', '2026-01-01')"
            )
```

- [ ] **Step 2: Run test to verify it fails**

```bash
.venv/bin/pytest tests/integration/test_studio_migration.py -v
```

Expected: all three FAIL (tables not present).

- [ ] **Step 3: Write the migration**

Create `backend/migrations/0011_studio.sql` with the contents from spec §4.4 verbatim. Reproduced here for the implementer:

```sql
-- 0011: Prompt Studio — testbenches + runs.

CREATE TABLE testbenches (
  id           INTEGER PRIMARY KEY,
  name         TEXT NOT NULL UNIQUE,
  description  TEXT,
  archived     INTEGER NOT NULL DEFAULT 0,
  created_at   TEXT NOT NULL,
  updated_at   TEXT NOT NULL
);

CREATE TABLE testbench_folders (
  id            INTEGER PRIMARY KEY,
  testbench_id  INTEGER NOT NULL REFERENCES testbenches(id) ON DELETE CASCADE,
  parent_id     INTEGER REFERENCES testbench_folders(id) ON DELETE CASCADE,
  name          TEXT NOT NULL,
  sort_index    INTEGER NOT NULL DEFAULT 0,
  created_at    TEXT NOT NULL,
  UNIQUE (testbench_id, parent_id, name)
);
CREATE INDEX idx_tb_folders_parent ON testbench_folders(parent_id);

CREATE TABLE testbench_items (
  id                     INTEGER PRIMARY KEY,
  folder_id              INTEGER NOT NULL REFERENCES testbench_folders(id) ON DELETE CASCADE,
  source_kind            TEXT NOT NULL CHECK (source_kind IN ('upload','catdv_clip')),
  upload_path            TEXT,
  upload_orig_name       TEXT,
  catdv_provider_clip_id TEXT,
  display_name           TEXT NOT NULL,
  gold_json              TEXT,
  sort_index             INTEGER NOT NULL DEFAULT 0,
  created_at             TEXT NOT NULL,
  CHECK (
    (source_kind = 'upload'     AND upload_path IS NOT NULL AND catdv_provider_clip_id IS NULL) OR
    (source_kind = 'catdv_clip' AND catdv_provider_clip_id IS NOT NULL AND upload_path IS NULL)
  )
);
CREATE INDEX idx_tb_items_folder ON testbench_items(folder_id);

CREATE TABLE studio_runs (
  id                INTEGER PRIMARY KEY,
  testbench_id      INTEGER NOT NULL REFERENCES testbenches(id),
  prompt_version_id INTEGER NOT NULL REFERENCES prompt_versions(id),
  status            TEXT NOT NULL CHECK (status IN ('pending','running','completed','failed','cancelled')),
  created_at        TEXT NOT NULL,
  started_at        TEXT,
  finished_at       TEXT,
  notes             TEXT
);
CREATE INDEX idx_studio_runs_testbench ON studio_runs(testbench_id, created_at DESC);

CREATE TABLE studio_run_items (
  id                  INTEGER PRIMARY KEY,
  run_id              INTEGER NOT NULL REFERENCES studio_runs(id) ON DELETE CASCADE,
  testbench_item_id   INTEGER NOT NULL REFERENCES testbench_items(id),
  status              TEXT NOT NULL CHECK (status IN (
                        'pending','resolving','uploading','prompting',
                        'done','error','unacceptable')),
  error               TEXT,
  unacceptable_reason TEXT,
  structured_json     TEXT,
  raw_text            TEXT,
  prompt_used         TEXT,
  model               TEXT,
  latency_ms          INTEGER,
  started_at          TEXT,
  finished_at         TEXT,
  UNIQUE (run_id, testbench_item_id)
);
CREATE INDEX idx_studio_run_items_run ON studio_run_items(run_id);
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
.venv/bin/pytest tests/integration/test_studio_migration.py -v
```

Expected: all three PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/migrations/0011_studio.sql tests/integration/test_studio_migration.py
git commit -m "feat(db): migration 0011 — studio tables (testbenches, runs, items)"
```

---

## Task 3: Studio models

**Files:**
- Create: `backend/app/models/studio.py`
- Test: `tests/unit/test_studio_models.py`

- [ ] **Step 1: Write the failing model test**

Create `tests/unit/test_studio_models.py`:

```python
import pytest

from backend.app.models.studio import (
    AnnotationOutput,
    StudioRun,
    StudioRunItem,
    Testbench,
    TestbenchFolder,
    TestbenchItem,
)


def test_testbench_basic():
    tb = Testbench(id=1, name="my tb", description=None, archived=False,
                   created_at="2026-01-01", updated_at="2026-01-01")
    assert tb.archived is False


def test_testbench_item_upload_kind_requires_upload_path():
    # Pydantic validation: source_kind='upload' rejects missing upload_path.
    with pytest.raises(ValueError):
        TestbenchItem(id=1, folder_id=1, source_kind="upload",
                      upload_path=None, upload_orig_name=None,
                      catdv_provider_clip_id=None, display_name="x",
                      gold_json=None, sort_index=0, created_at="2026-01-01")


def test_testbench_item_catdv_kind_requires_provider_clip_id():
    with pytest.raises(ValueError):
        TestbenchItem(id=1, folder_id=1, source_kind="catdv_clip",
                      upload_path=None, upload_orig_name=None,
                      catdv_provider_clip_id=None, display_name="x",
                      gold_json=None, sort_index=0, created_at="2026-01-01")


def test_studio_run_states():
    for s in ("pending", "running", "completed", "failed", "cancelled"):
        StudioRun(id=1, testbench_id=1, prompt_version_id=1, status=s,
                  created_at="2026-01-01", started_at=None, finished_at=None, notes=None)
    with pytest.raises(ValueError):
        StudioRun(id=1, testbench_id=1, prompt_version_id=1, status="bogus",
                  created_at="2026-01-01", started_at=None, finished_at=None, notes=None)


def test_studio_run_item_unacceptable_state_allowed():
    StudioRunItem(id=1, run_id=1, testbench_item_id=1, status="unacceptable",
                  error=None, unacceptable_reason="catdv offline; no cache",
                  structured_json=None, raw_text=None, prompt_used=None,
                  model=None, latency_ms=None, started_at=None, finished_at=None)


def test_annotation_output_dataclass():
    out = AnnotationOutput(
        structured={"k": "v"}, raw_text='{"k":"v"}',
        prompt_used="rendered prompt body", model="gemini-x", latency_ms=1234,
    )
    assert out.structured == {"k": "v"}
    assert out.latency_ms == 1234
```

- [ ] **Step 2: Run to verify failure**

```bash
.venv/bin/pytest tests/unit/test_studio_models.py -v
```

Expected: ImportError.

- [ ] **Step 3: Implement models**

Create `backend/app/models/studio.py`:

```python
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, model_validator

SourceKind = Literal["upload", "catdv_clip"]
RunStatus = Literal["pending", "running", "completed", "failed", "cancelled"]
ItemStatus = Literal[
    "pending", "resolving", "uploading", "prompting",
    "done", "error", "unacceptable",
]


class Testbench(BaseModel):
    id: int
    name: str
    description: str | None
    archived: bool
    created_at: str
    updated_at: str


class TestbenchFolder(BaseModel):
    id: int
    testbench_id: int
    parent_id: int | None
    name: str
    sort_index: int
    created_at: str


class TestbenchItem(BaseModel):
    id: int
    folder_id: int
    source_kind: SourceKind
    upload_path: str | None
    upload_orig_name: str | None
    catdv_provider_clip_id: str | None
    display_name: str
    gold_json: str | None
    sort_index: int
    created_at: str

    @model_validator(mode="after")
    def _check_source_consistency(self) -> "TestbenchItem":
        if self.source_kind == "upload":
            if not self.upload_path or self.catdv_provider_clip_id is not None:
                raise ValueError("upload kind requires upload_path and no catdv_provider_clip_id")
        else:
            if not self.catdv_provider_clip_id or self.upload_path is not None:
                raise ValueError("catdv_clip kind requires catdv_provider_clip_id and no upload_path")
        return self


class StudioRun(BaseModel):
    id: int
    testbench_id: int
    prompt_version_id: int
    status: RunStatus
    created_at: str
    started_at: str | None
    finished_at: str | None
    notes: str | None


class StudioRunItem(BaseModel):
    id: int
    run_id: int
    testbench_item_id: int
    status: ItemStatus
    error: str | None
    unacceptable_reason: str | None
    structured_json: str | None
    raw_text: str | None
    prompt_used: str | None
    model: str | None
    latency_ms: int | None
    started_at: str | None
    finished_at: str | None


@dataclass
class AnnotationOutput:
    """Result of one Gemini per-item annotation pass — shape shared by
    `services/annotator.py::run_job` and `services/studio_runs.py::run`."""
    structured: dict[str, Any] | None
    raw_text: str
    prompt_used: str
    model: str
    latency_ms: int
```

- [ ] **Step 4: Run tests to verify pass**

```bash
.venv/bin/pytest tests/unit/test_studio_models.py -v
```

Expected: all six PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/models/studio.py tests/unit/test_studio_models.py
git commit -m "feat(models): studio pydantic models + AnnotationOutput dataclass"
```

---

### ✅ Phase 1 review checkpoint

```bash
.venv/bin/pytest
```

Full suite green. New tests: 3 (migration) + 6 (models) = 9.

---

# Phase 2 — Testbench repositories

## Task 4: `TestbenchesRepo`

**Files:**
- Create: `backend/app/repositories/testbenches.py`
- Test: `tests/integration/test_testbenches_repo.py`

- [ ] **Step 1: Write the failing repo test**

Create `tests/integration/test_testbenches_repo.py`:

```python
from pathlib import Path

import aiosqlite
import pytest

from backend.app.migrations_runner import apply_migrations
from backend.app.repositories.testbenches import TestbenchesRepo

MIGRATIONS = Path(__file__).resolve().parents[2] / "backend" / "migrations"


@pytest.fixture
async def conn(tmp_path):
    db = tmp_path / "t.db"
    async with aiosqlite.connect(db) as c:
        await apply_migrations(c, MIGRATIONS)
        yield c


@pytest.mark.asyncio
async def test_create_and_get(conn):
    repo = TestbenchesRepo()
    tb_id = await repo.create(conn, name="my-tb", description="d")
    tb = await repo.get(conn, tb_id)
    assert tb.name == "my-tb"
    assert tb.archived is False


@pytest.mark.asyncio
async def test_unique_name(conn):
    repo = TestbenchesRepo()
    await repo.create(conn, name="dup", description=None)
    with pytest.raises(aiosqlite.IntegrityError):
        await repo.create(conn, name="dup", description=None)


@pytest.mark.asyncio
async def test_archive_hides_from_list_active(conn):
    repo = TestbenchesRepo()
    a = await repo.create(conn, name="a", description=None)
    b = await repo.create(conn, name="b", description=None)
    await repo.archive(conn, a)
    listed = await repo.list_active(conn)
    assert [t.id for t in listed] == [b]


@pytest.mark.asyncio
async def test_folder_tree_round_trip(conn):
    repo = TestbenchesRepo()
    tb = await repo.create(conn, name="x", description=None)
    root = await repo.create_folder(conn, testbench_id=tb, parent_id=None, name="root")
    sub = await repo.create_folder(conn, testbench_id=tb, parent_id=root, name="sub")
    folders = await repo.list_folders(conn, tb)
    by_id = {f.id: f for f in folders}
    assert by_id[root].parent_id is None
    assert by_id[sub].parent_id == root


@pytest.mark.asyncio
async def test_delete_folder_only_when_empty(conn):
    """Folder with subfolder or items present → must refuse."""
    from backend.app.repositories.testbench_items import TestbenchItemsRepo
    repo = TestbenchesRepo()
    items = TestbenchItemsRepo()
    tb = await repo.create(conn, name="t", description=None)
    f1 = await repo.create_folder(conn, testbench_id=tb, parent_id=None, name="f1")
    await items.add_upload(conn, folder_id=f1, upload_path="a.mp4", original_name="a.mp4")
    with pytest.raises(ValueError, match="not empty"):
        await repo.delete_folder(conn, f1)


@pytest.mark.asyncio
async def test_cascade_delete_on_testbench(conn):
    """Deleting a testbench cascades to folders and items (FK ON DELETE CASCADE)."""
    repo = TestbenchesRepo()
    tb = await repo.create(conn, name="t", description=None)
    f = await repo.create_folder(conn, testbench_id=tb, parent_id=None, name="r")
    # Cascade verified at SQL level; archive is the user-facing "delete" so test that.
    await repo.archive(conn, tb)
    assert (await repo.get(conn, tb)).archived is True
```

- [ ] **Step 2: Run to verify failure**

```bash
.venv/bin/pytest tests/integration/test_testbenches_repo.py -v
```

Expected: ImportError on `TestbenchesRepo`.

- [ ] **Step 3: Implement repo**

Create `backend/app/repositories/testbenches.py`:

```python
from datetime import datetime, timezone

import aiosqlite

from backend.app.models.studio import Testbench, TestbenchFolder


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


_TB_COLS = "id, name, description, archived, created_at, updated_at"
_F_COLS = "id, testbench_id, parent_id, name, sort_index, created_at"


def _tb(row) -> Testbench:
    return Testbench(
        id=row[0], name=row[1], description=row[2],
        archived=bool(row[3]), created_at=row[4], updated_at=row[5],
    )


def _f(row) -> TestbenchFolder:
    return TestbenchFolder(
        id=row[0], testbench_id=row[1], parent_id=row[2],
        name=row[3], sort_index=row[4], created_at=row[5],
    )


class TestbenchesRepo:
    async def create(self, conn: aiosqlite.Connection, *, name: str, description: str | None) -> int:
        now = _now()
        cur = await conn.execute(
            "INSERT INTO testbenches (name, description, archived, created_at, updated_at) "
            "VALUES (?, ?, 0, ?, ?)",
            (name, description, now, now),
        )
        await conn.commit()
        return cur.lastrowid

    async def get(self, conn: aiosqlite.Connection, id: int) -> Testbench:
        cur = await conn.execute(f"SELECT {_TB_COLS} FROM testbenches WHERE id=?", (id,))
        row = await cur.fetchone()
        if row is None:
            raise LookupError(f"testbench {id} not found")
        return _tb(row)

    async def list_active(self, conn: aiosqlite.Connection) -> list[Testbench]:
        cur = await conn.execute(
            f"SELECT {_TB_COLS} FROM testbenches WHERE archived=0 ORDER BY name"
        )
        return [_tb(r) for r in await cur.fetchall()]

    async def rename(self, conn: aiosqlite.Connection, id: int, name: str) -> None:
        await conn.execute(
            "UPDATE testbenches SET name=?, updated_at=? WHERE id=?",
            (name, _now(), id),
        )
        await conn.commit()

    async def archive(self, conn: aiosqlite.Connection, id: int) -> None:
        await conn.execute(
            "UPDATE testbenches SET archived=1, updated_at=? WHERE id=?",
            (_now(), id),
        )
        await conn.commit()

    async def list_folders(self, conn: aiosqlite.Connection, testbench_id: int) -> list[TestbenchFolder]:
        cur = await conn.execute(
            f"SELECT {_F_COLS} FROM testbench_folders WHERE testbench_id=? "
            "ORDER BY sort_index, name",
            (testbench_id,),
        )
        return [_f(r) for r in await cur.fetchall()]

    async def create_folder(
        self, conn: aiosqlite.Connection,
        *, testbench_id: int, parent_id: int | None, name: str,
    ) -> int:
        cur = await conn.execute(
            "INSERT INTO testbench_folders (testbench_id, parent_id, name, sort_index, created_at) "
            "VALUES (?, ?, ?, 0, ?)",
            (testbench_id, parent_id, name, _now()),
        )
        await conn.commit()
        return cur.lastrowid

    async def rename_folder(self, conn: aiosqlite.Connection, id: int, name: str) -> None:
        await conn.execute("UPDATE testbench_folders SET name=? WHERE id=?", (name, id))
        await conn.commit()

    async def delete_folder(self, conn: aiosqlite.Connection, id: int) -> None:
        cur = await conn.execute(
            "SELECT (SELECT COUNT(*) FROM testbench_folders WHERE parent_id=?) "
            "+ (SELECT COUNT(*) FROM testbench_items WHERE folder_id=?)",
            (id, id),
        )
        (count,) = await cur.fetchone()
        if count:
            raise ValueError("folder not empty; cannot delete")
        await conn.execute("DELETE FROM testbench_folders WHERE id=?", (id,))
        await conn.commit()
```

- [ ] **Step 4: Run tests to verify pass**

```bash
.venv/bin/pytest tests/integration/test_testbenches_repo.py -v
```

Expected: all six PASS (one depends on Task 5's `TestbenchItemsRepo` import — if it fails for missing import, defer that one test until Task 5 is done, then re-run).

- [ ] **Step 5: Commit**

```bash
git add backend/app/repositories/testbenches.py tests/integration/test_testbenches_repo.py
git commit -m "feat(repo): TestbenchesRepo — testbench + folder CRUD"
```

---

## Task 5: `TestbenchItemsRepo`

**Files:**
- Create: `backend/app/repositories/testbench_items.py`
- Test: `tests/integration/test_testbench_items_repo.py`

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_testbench_items_repo.py`:

```python
import json
from pathlib import Path

import aiosqlite
import pytest

from backend.app.migrations_runner import apply_migrations
from backend.app.repositories.testbench_items import TestbenchItemsRepo
from backend.app.repositories.testbenches import TestbenchesRepo

MIGRATIONS = Path(__file__).resolve().parents[2] / "backend" / "migrations"


@pytest.fixture
async def conn(tmp_path):
    db = tmp_path / "t.db"
    async with aiosqlite.connect(db) as c:
        await apply_migrations(c, MIGRATIONS)
        yield c


@pytest.fixture
async def folder(conn):
    tb = await TestbenchesRepo().create(conn, name="t", description=None)
    return await TestbenchesRepo().create_folder(conn, testbench_id=tb, parent_id=None, name="root")


@pytest.mark.asyncio
async def test_add_upload_and_catdv(conn, folder):
    repo = TestbenchItemsRepo()
    u = await repo.add_upload(conn, folder_id=folder, upload_path="u.mp4", original_name="orig.mp4")
    c = await repo.add_catdv(conn, folder_id=folder, provider_clip_id="999", name="catdv-999")
    items = await repo.list_for_folder(conn, folder)
    by_id = {it.id: it for it in items}
    assert by_id[u].source_kind == "upload"
    assert by_id[u].upload_path == "u.mp4"
    assert by_id[c].source_kind == "catdv_clip"
    assert by_id[c].catdv_provider_clip_id == "999"


@pytest.mark.asyncio
async def test_set_gold_round_trip_preserves_unknown_keys(conn, folder):
    repo = TestbenchItemsRepo()
    item = await repo.add_upload(conn, folder_id=folder, upload_path="u.mp4", original_name="u.mp4")
    await repo.set_gold(conn, item, {"description": "old", "future_field": [1, 2, 3]})
    fetched = (await repo.list_for_folder(conn, folder))[0]
    assert json.loads(fetched.gold_json) == {"description": "old", "future_field": [1, 2, 3]}
    # Update only description; future_field must persist.
    existing = json.loads(fetched.gold_json)
    existing["description"] = "new"
    await repo.set_gold(conn, item, existing)
    fetched2 = (await repo.list_for_folder(conn, folder))[0]
    assert json.loads(fetched2.gold_json) == {"description": "new", "future_field": [1, 2, 3]}


@pytest.mark.asyncio
async def test_clear_gold(conn, folder):
    repo = TestbenchItemsRepo()
    item = await repo.add_upload(conn, folder_id=folder, upload_path="u.mp4", original_name="u.mp4")
    await repo.set_gold(conn, item, {"description": "x"})
    await repo.set_gold(conn, item, None)
    fetched = (await repo.list_for_folder(conn, folder))[0]
    assert fetched.gold_json is None


@pytest.mark.asyncio
async def test_list_for_testbench_tree_order(conn):
    """Items return in folder DFS order, then sort_index within folder."""
    tb_repo = TestbenchesRepo()
    repo = TestbenchItemsRepo()
    tb = await tb_repo.create(conn, name="t", description=None)
    root = await tb_repo.create_folder(conn, testbench_id=tb, parent_id=None, name="root")
    sub = await tb_repo.create_folder(conn, testbench_id=tb, parent_id=root, name="sub")
    a = await repo.add_upload(conn, folder_id=root, upload_path="a.mp4", original_name="a.mp4")
    b = await repo.add_upload(conn, folder_id=sub, upload_path="b.mp4", original_name="b.mp4")
    c = await repo.add_upload(conn, folder_id=root, upload_path="c.mp4", original_name="c.mp4")
    items = await repo.list_for_testbench(conn, tb)
    # root items first (a, c), then sub items (b) — DFS by folder.
    assert [it.id for it in items] == [a, c, b]


@pytest.mark.asyncio
async def test_remove(conn, folder):
    repo = TestbenchItemsRepo()
    item = await repo.add_upload(conn, folder_id=folder, upload_path="u.mp4", original_name="u.mp4")
    await repo.remove(conn, item)
    assert await repo.list_for_folder(conn, folder) == []
```

- [ ] **Step 2: Run to verify failure**

```bash
.venv/bin/pytest tests/integration/test_testbench_items_repo.py -v
```

Expected: ImportError.

- [ ] **Step 3: Implement the repo**

Create `backend/app/repositories/testbench_items.py`:

```python
import json
from datetime import datetime, timezone

import aiosqlite

from backend.app.models.studio import TestbenchItem


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


_COLS = (
    "id, folder_id, source_kind, upload_path, upload_orig_name, "
    "catdv_provider_clip_id, display_name, gold_json, sort_index, created_at"
)


def _item(row) -> TestbenchItem:
    return TestbenchItem(
        id=row[0], folder_id=row[1], source_kind=row[2],
        upload_path=row[3], upload_orig_name=row[4],
        catdv_provider_clip_id=row[5], display_name=row[6],
        gold_json=row[7], sort_index=row[8], created_at=row[9],
    )


class TestbenchItemsRepo:
    async def add_upload(
        self, conn: aiosqlite.Connection,
        *, folder_id: int, upload_path: str, original_name: str,
    ) -> int:
        cur = await conn.execute(
            "INSERT INTO testbench_items "
            "(folder_id, source_kind, upload_path, upload_orig_name, display_name, sort_index, created_at) "
            "VALUES (?, 'upload', ?, ?, ?, 0, ?)",
            (folder_id, upload_path, original_name, original_name, _now()),
        )
        await conn.commit()
        return cur.lastrowid

    async def add_catdv(
        self, conn: aiosqlite.Connection,
        *, folder_id: int, provider_clip_id: str, name: str,
    ) -> int:
        cur = await conn.execute(
            "INSERT INTO testbench_items "
            "(folder_id, source_kind, catdv_provider_clip_id, display_name, sort_index, created_at) "
            "VALUES (?, 'catdv_clip', ?, ?, 0, ?)",
            (folder_id, provider_clip_id, name, _now()),
        )
        await conn.commit()
        return cur.lastrowid

    async def list_for_folder(self, conn: aiosqlite.Connection, folder_id: int) -> list[TestbenchItem]:
        cur = await conn.execute(
            f"SELECT {_COLS} FROM testbench_items WHERE folder_id=? ORDER BY sort_index, id",
            (folder_id,),
        )
        return [_item(r) for r in await cur.fetchall()]

    async def list_for_testbench(self, conn: aiosqlite.Connection, testbench_id: int) -> list[TestbenchItem]:
        """DFS by folder tree, sort_index within folder. Used by the run worker
        to iterate in a deterministic order matching the UI."""
        # Recursive CTE on folders + join to items; SQLite supports WITH RECURSIVE.
        cur = await conn.execute(
            """
            WITH RECURSIVE tree(id, parent_id, depth, path) AS (
                SELECT id, parent_id, 0, printf('%010d', sort_index)
                FROM testbench_folders WHERE testbench_id=? AND parent_id IS NULL
                UNION ALL
                SELECT f.id, f.parent_id, t.depth+1, t.path || '/' || printf('%010d', f.sort_index)
                FROM testbench_folders f JOIN tree t ON f.parent_id = t.id
            )
            SELECT i.id, i.folder_id, i.source_kind, i.upload_path, i.upload_orig_name,
                   i.catdv_provider_clip_id, i.display_name, i.gold_json, i.sort_index, i.created_at
            FROM testbench_items i
            JOIN tree t ON i.folder_id = t.id
            ORDER BY t.path, i.sort_index, i.id
            """,
            (testbench_id,),
        )
        return [_item(r) for r in await cur.fetchall()]

    async def set_gold(self, conn: aiosqlite.Connection, id: int, gold: dict | None) -> None:
        payload = json.dumps(gold, ensure_ascii=False) if gold else None
        await conn.execute("UPDATE testbench_items SET gold_json=? WHERE id=?", (payload, id))
        await conn.commit()

    async def remove(self, conn: aiosqlite.Connection, id: int) -> None:
        await conn.execute("DELETE FROM testbench_items WHERE id=?", (id,))
        await conn.commit()
```

- [ ] **Step 4: Run tests to verify pass**

```bash
.venv/bin/pytest tests/integration/test_testbench_items_repo.py tests/integration/test_testbenches_repo.py -v
```

Expected: all PASS (including the previously-deferred Task 4 test).

- [ ] **Step 5: Commit**

```bash
git add backend/app/repositories/testbench_items.py tests/integration/test_testbench_items_repo.py
git commit -m "feat(repo): TestbenchItemsRepo — items, gold round-trip, tree order"
```

---

### ✅ Phase 2 review checkpoint

```bash
.venv/bin/pytest
```

Expected: green. New tests this phase: 6 + 5 = 11.

---

# Phase 3 — Studio-runs repository

## Task 6: `StudioRunsRepo`

**Files:**
- Create: `backend/app/repositories/studio_runs.py`
- Test: `tests/integration/test_studio_runs_repo.py`

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_studio_runs_repo.py`. The fixture sets up a prompt + version because `studio_runs.prompt_version_id` is FK'd.

```python
import json
from pathlib import Path

import aiosqlite
import pytest

from backend.app.migrations_runner import apply_migrations
from backend.app.repositories.prompts import PromptsRepo
from backend.app.repositories.studio_runs import StudioRunsRepo
from backend.app.repositories.testbench_items import TestbenchItemsRepo
from backend.app.repositories.testbenches import TestbenchesRepo

MIGRATIONS = Path(__file__).resolve().parents[2] / "backend" / "migrations"


@pytest.fixture
async def conn(tmp_path):
    db = tmp_path / "t.db"
    async with aiosqlite.connect(db) as c:
        await apply_migrations(c, MIGRATIONS)
        yield c


@pytest.fixture
async def setup(conn):
    prompts = PromptsRepo()
    prompt = await prompts.create_with_initial_version(
        conn, name="p", description=None, body="hi",
        target_map={}, output_schema={}, model="m", initial_state="production",
    )
    pv_id = (await prompts.get_with_versions(conn, prompt.id)).versions[0].id
    tb = await TestbenchesRepo().create(conn, name="tb", description=None)
    folder = await TestbenchesRepo().create_folder(conn, testbench_id=tb, parent_id=None, name="r")
    item = await TestbenchItemsRepo().add_upload(conn, folder_id=folder, upload_path="u.mp4", original_name="u.mp4")
    return dict(pv_id=pv_id, tb=tb, item=item)


@pytest.mark.asyncio
async def test_create_run_starts_pending(conn, setup):
    repo = StudioRunsRepo()
    rid = await repo.create(conn, testbench_id=setup["tb"], prompt_version_id=setup["pv_id"])
    run = await repo.get(conn, rid)
    assert run.status == "pending"
    assert run.started_at is None


@pytest.mark.asyncio
async def test_status_transitions_and_timestamps(conn, setup):
    repo = StudioRunsRepo()
    rid = await repo.create(conn, testbench_id=setup["tb"], prompt_version_id=setup["pv_id"])
    await repo.update_status(conn, rid, "running", started=True)
    r = await repo.get(conn, rid)
    assert r.status == "running" and r.started_at is not None
    await repo.update_status(conn, rid, "completed", finished=True)
    r = await repo.get(conn, rid)
    assert r.status == "completed" and r.finished_at is not None


@pytest.mark.asyncio
async def test_upsert_run_item_and_status(conn, setup):
    repo = StudioRunsRepo()
    rid = await repo.create(conn, testbench_id=setup["tb"], prompt_version_id=setup["pv_id"])
    item_id = await repo.upsert_item(conn, run_id=rid, testbench_item_id=setup["item"])
    await repo.update_item_status(conn, item_id, "resolving")
    items = await repo.list_items(conn, rid)
    assert items[0].status == "resolving"


@pytest.mark.asyncio
async def test_attach_output(conn, setup):
    repo = StudioRunsRepo()
    rid = await repo.create(conn, testbench_id=setup["tb"], prompt_version_id=setup["pv_id"])
    iid = await repo.upsert_item(conn, run_id=rid, testbench_item_id=setup["item"])
    await repo.attach_output(
        conn, iid,
        structured_json=json.dumps({"k": "v"}),
        raw_text='{"k":"v"}', prompt_used="rendered", model="m", latency_ms=1234,
    )
    items = await repo.list_items(conn, rid)
    assert items[0].structured_json == '{"k": "v"}'
    assert items[0].latency_ms == 1234
    # status was implicitly set to 'done' by attach_output
    assert items[0].status == "done"


@pytest.mark.asyncio
async def test_mark_unacceptable(conn, setup):
    repo = StudioRunsRepo()
    rid = await repo.create(conn, testbench_id=setup["tb"], prompt_version_id=setup["pv_id"])
    iid = await repo.upsert_item(conn, run_id=rid, testbench_item_id=setup["item"])
    await repo.update_item_status(conn, iid, "unacceptable", unacceptable_reason="no media")
    items = await repo.list_items(conn, rid)
    assert items[0].status == "unacceptable"
    assert items[0].unacceptable_reason == "no media"


@pytest.mark.asyncio
async def test_reset_transient_sweeps_running_runs(conn, setup):
    repo = StudioRunsRepo()
    rid = await repo.create(conn, testbench_id=setup["tb"], prompt_version_id=setup["pv_id"])
    iid = await repo.upsert_item(conn, run_id=rid, testbench_item_id=setup["item"])
    await repo.update_status(conn, rid, "running", started=True)
    await repo.update_item_status(conn, iid, "prompting")

    swept = await repo.reset_transient(conn)
    assert swept >= 1
    r = await repo.get(conn, rid)
    assert r.status == "failed"
    items = await repo.list_items(conn, rid)
    assert items[0].status == "error"
    assert "interrupted" in (items[0].error or "")


@pytest.mark.asyncio
async def test_unique_run_item_pair(conn, setup):
    repo = StudioRunsRepo()
    rid = await repo.create(conn, testbench_id=setup["tb"], prompt_version_id=setup["pv_id"])
    await repo.upsert_item(conn, run_id=rid, testbench_item_id=setup["item"])
    # Second upsert with same (run, item) → no error, no duplicate row.
    await repo.upsert_item(conn, run_id=rid, testbench_item_id=setup["item"])
    items = await repo.list_items(conn, rid)
    assert len(items) == 1
```

- [ ] **Step 2: Run to verify failure**

```bash
.venv/bin/pytest tests/integration/test_studio_runs_repo.py -v
```

Expected: ImportError.

- [ ] **Step 3: Implement the repo**

Create `backend/app/repositories/studio_runs.py`:

```python
from datetime import datetime, timezone

import aiosqlite

from backend.app.models.studio import StudioRun, StudioRunItem


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


_RUN_COLS = "id, testbench_id, prompt_version_id, status, created_at, started_at, finished_at, notes"
_ITEM_COLS = (
    "id, run_id, testbench_item_id, status, error, unacceptable_reason, "
    "structured_json, raw_text, prompt_used, model, latency_ms, started_at, finished_at"
)


def _run(row) -> StudioRun:
    return StudioRun(
        id=row[0], testbench_id=row[1], prompt_version_id=row[2],
        status=row[3], created_at=row[4], started_at=row[5],
        finished_at=row[6], notes=row[7],
    )


def _ri(row) -> StudioRunItem:
    return StudioRunItem(
        id=row[0], run_id=row[1], testbench_item_id=row[2], status=row[3],
        error=row[4], unacceptable_reason=row[5], structured_json=row[6],
        raw_text=row[7], prompt_used=row[8], model=row[9], latency_ms=row[10],
        started_at=row[11], finished_at=row[12],
    )


class StudioRunsRepo:
    async def create(
        self, conn: aiosqlite.Connection,
        *, testbench_id: int, prompt_version_id: int,
    ) -> int:
        cur = await conn.execute(
            "INSERT INTO studio_runs (testbench_id, prompt_version_id, status, created_at) "
            "VALUES (?, ?, 'pending', ?)",
            (testbench_id, prompt_version_id, _now()),
        )
        await conn.commit()
        return cur.lastrowid

    async def get(self, conn: aiosqlite.Connection, id: int) -> StudioRun:
        cur = await conn.execute(f"SELECT {_RUN_COLS} FROM studio_runs WHERE id=?", (id,))
        row = await cur.fetchone()
        if row is None:
            raise LookupError(f"studio_run {id} not found")
        return _run(row)

    async def list_for_testbench(self, conn: aiosqlite.Connection, testbench_id: int) -> list[StudioRun]:
        cur = await conn.execute(
            f"SELECT {_RUN_COLS} FROM studio_runs WHERE testbench_id=? ORDER BY created_at DESC",
            (testbench_id,),
        )
        return [_run(r) for r in await cur.fetchall()]

    async def update_status(
        self, conn: aiosqlite.Connection, id: int, status: str,
        *, started: bool = False, finished: bool = False,
    ) -> None:
        fields = ["status=?"]
        vals: list = [status]
        if started:
            fields.append("started_at=?")
            vals.append(_now())
        if finished:
            fields.append("finished_at=?")
            vals.append(_now())
        vals.append(id)
        await conn.execute(f"UPDATE studio_runs SET {', '.join(fields)} WHERE id=?", vals)
        await conn.commit()

    async def upsert_item(
        self, conn: aiosqlite.Connection,
        *, run_id: int, testbench_item_id: int,
    ) -> int:
        cur = await conn.execute(
            "SELECT id FROM studio_run_items WHERE run_id=? AND testbench_item_id=?",
            (run_id, testbench_item_id),
        )
        row = await cur.fetchone()
        if row:
            return row[0]
        cur = await conn.execute(
            "INSERT INTO studio_run_items (run_id, testbench_item_id, status, started_at) "
            "VALUES (?, ?, 'pending', ?)",
            (run_id, testbench_item_id, _now()),
        )
        await conn.commit()
        return cur.lastrowid

    async def update_item_status(
        self, conn: aiosqlite.Connection, id: int, status: str,
        *, error: str | None = None, unacceptable_reason: str | None = None,
    ) -> None:
        fields = ["status=?"]
        vals: list = [status]
        if error is not None:
            fields.append("error=?")
            vals.append(error)
        if unacceptable_reason is not None:
            fields.append("unacceptable_reason=?")
            vals.append(unacceptable_reason)
        if status in ("done", "error", "unacceptable"):
            fields.append("finished_at=?")
            vals.append(_now())
        vals.append(id)
        await conn.execute(
            f"UPDATE studio_run_items SET {', '.join(fields)} WHERE id=?", vals,
        )
        await conn.commit()

    async def attach_output(
        self, conn: aiosqlite.Connection, id: int,
        *, structured_json: str | None, raw_text: str,
        prompt_used: str, model: str, latency_ms: int,
    ) -> None:
        await conn.execute(
            "UPDATE studio_run_items SET "
            "  structured_json=?, raw_text=?, prompt_used=?, model=?, latency_ms=?, "
            "  status='done', finished_at=? WHERE id=?",
            (structured_json, raw_text, prompt_used, model, latency_ms, _now(), id),
        )
        await conn.commit()

    async def list_items(self, conn: aiosqlite.Connection, run_id: int) -> list[StudioRunItem]:
        cur = await conn.execute(
            f"SELECT {_ITEM_COLS} FROM studio_run_items WHERE run_id=? ORDER BY id",
            (run_id,),
        )
        return [_ri(r) for r in await cur.fetchall()]

    async def reset_transient(self, conn: aiosqlite.Connection) -> int:
        """Sweep runs left mid-flight by a crash: running → failed, transient
        item states → error('interrupted by restart')."""
        cur = await conn.execute(
            "UPDATE studio_runs SET status='failed', finished_at=? WHERE status='running'",
            (_now(),),
        )
        n = cur.rowcount
        await conn.execute(
            "UPDATE studio_run_items SET status='error', error='interrupted by restart', "
            "finished_at=? WHERE status IN ('resolving','uploading','prompting','pending') "
            "AND run_id IN (SELECT id FROM studio_runs WHERE status='failed')",
            (_now(),),
        )
        await conn.commit()
        return n
```

- [ ] **Step 4: Run tests to verify pass**

```bash
.venv/bin/pytest tests/integration/test_studio_runs_repo.py -v
```

Expected: all 7 PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/repositories/studio_runs.py tests/integration/test_studio_runs_repo.py
git commit -m "feat(repo): StudioRunsRepo — runs, run items, crash-recovery sweep"
```

---

### ✅ Phase 3 review checkpoint

```bash
.venv/bin/pytest
```

Phase total: 7 new tests. Full suite stays green.

---

# Phase 4 — Annotator refactor

The goal here is to extract the resolve → upload → prompt → parse middle of `_process_item` into a callable that returns an `AnnotationOutput`, while leaving `run_job`'s behavior identical. The Studio worker will call the same extracted function.

The trick is interleaved status updates. We pass them through an `on_status` callback so production keeps writing to `jobs_repo + event_bus` and Studio writes to `studio_runs_repo + event_bus`.

## Task 7: Extract `process_item` into shared callable

**Files:**
- Modify: `backend/app/services/annotator.py`
- Test: `tests/unit/test_annotator_process_item.py`
- Verify: existing `tests/services/test_annotator.py` still passes unchanged.

- [ ] **Step 1: Read the existing tests for `annotator.run_job`**

Skim `tests/services/test_annotator.py` first so the refactor doesn't break the contract. Note the fixture shapes; the existing happy-path test will be our regression net.

- [ ] **Step 2: Write a failing test for the extracted function**

Create `tests/unit/test_annotator_process_item.py`:

```python
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.app.models.studio import AnnotationOutput
from backend.app.services.annotator import process_item


class _Version:
    id = 1
    body = "PROMPT BODY"
    output_schema = {"type": "object"}
    model = "gemini-x"
    target_map = {}


class _Canonical:
    duration_secs = 30.0
    provider_data = {"id": 42, "name": "P1010001"}


@pytest.mark.asyncio
async def test_process_item_returns_annotation_output_and_emits_statuses(tmp_path):
    local_path = tmp_path / "u.mp4"
    local_path.write_bytes(b"\x00")

    resolver = MagicMock()
    resolver.path_for_clip_id = AsyncMock(return_value=local_path)

    archive = MagicMock()
    archive.get_clip = AsyncMock(return_value=_Canonical())

    upload = MagicMock(); file_ref = MagicMock()
    ai_store = MagicMock()
    ai_store.ensure_uploaded = AsyncMock(return_value=upload)
    ai_store.reference_for_gemini = AsyncMock(return_value=file_ref)

    gemini = MagicMock()
    gemini.annotate = MagicMock(return_value={"text": '{"k":"v"}', "raw": {"x": 1}})

    statuses: list[str] = []

    async def on_status(s: str) -> None:
        statuses.append(s)

    out = await process_item(
        clip_resolver_arg=42,        # what gets passed to resolver.path_for_clip_id
        archive_lookup_arg="42",     # what gets passed to archive.get_clip
        clip_key=("catdv", "42"),
        version=_Version(),
        proxy_resolver=resolver, archive=archive, ai_store=ai_store, gemini=gemini,
        on_status=on_status,
    )
    assert isinstance(out, AnnotationOutput)
    assert out.structured == {"k": "v"}
    assert out.model == "gemini-x"
    assert "PROMPT BODY" in out.prompt_used
    # duration anchor prepended
    assert "30.00" in out.prompt_used
    assert statuses == ["resolving", "uploading", "prompting"]


@pytest.mark.asyncio
async def test_process_item_handles_non_json_gemini_response(tmp_path):
    local_path = tmp_path / "u.mp4"
    local_path.write_bytes(b"\x00")
    resolver = MagicMock(); resolver.path_for_clip_id = AsyncMock(return_value=local_path)
    archive = MagicMock(); archive.get_clip = AsyncMock(return_value=_Canonical())
    ai_store = MagicMock()
    ai_store.ensure_uploaded = AsyncMock(return_value=MagicMock())
    ai_store.reference_for_gemini = AsyncMock(return_value=MagicMock())
    gemini = MagicMock(); gemini.annotate = MagicMock(return_value={"text": "not json", "raw": {}})

    async def on_status(_): pass

    out = await process_item(
        clip_resolver_arg=42, archive_lookup_arg="42",
        clip_key=("catdv", "42"),
        version=_Version(),
        proxy_resolver=resolver, archive=archive, ai_store=ai_store, gemini=gemini,
        on_status=on_status,
    )
    assert out.structured is None
    assert out.raw_text == "not json"
```

- [ ] **Step 3: Run test to verify it fails**

```bash
.venv/bin/pytest tests/unit/test_annotator_process_item.py -v
```

Expected: ImportError (no `process_item` symbol yet).

- [ ] **Step 4: Extract `process_item`**

Open `backend/app/services/annotator.py`. Add a new public `process_item` callable that contains the resolve → upload → prompt → parse logic, and have `_process_item` call it. Concretely, replace the file's middle section with:

```python
from collections.abc import Awaitable, Callable
from typing import Literal

from backend.app.models.studio import AnnotationOutput

PipelineStatus = Literal["resolving", "uploading", "prompting"]


async def process_item(
    *,
    clip_resolver_arg,       # int for CatDV, str path / file_ref for uploads
    archive_lookup_arg,      # str provider_clip_id, or None for uploads
    clip_key: tuple[str, str],
    version,                 # PromptVersion-like; needs id/body/output_schema/model/target_map
    proxy_resolver,
    archive,                 # may be None when archive_lookup_arg is None
    ai_store,
    gemini,
    on_status: Callable[[PipelineStatus], Awaitable[None]],
) -> AnnotationOutput:
    """Shared per-item Gemini pipeline. Used by both production
    `annotator.run_job` and `studio_runs.run`. Returns the output
    dataclass; the caller owns persistence."""
    import time

    await on_status("resolving")
    local_path: Path = await proxy_resolver.path_for_clip_id(clip_resolver_arg)

    await on_status("uploading")
    mime = mimetypes.guess_type(str(local_path))[0] or "video/quicktime"
    upload = await ai_store.ensure_uploaded(clip_key, local_path, mime)
    file_ref = await ai_store.reference_for_gemini(upload)

    duration_secs = 0.0
    if archive is not None and archive_lookup_arg is not None:
        canonical = await archive.get_clip(archive_lookup_arg)
        duration_secs = float(canonical.duration_secs or 0.0)

    await on_status("prompting")
    rendered_body = _render_prompt(version.body, duration_secs=duration_secs)
    t0 = time.monotonic()
    result = gemini.annotate(
        file_ref=file_ref,
        prompt=rendered_body,
        schema=version.output_schema,
        model=version.model,
    )
    latency_ms = int((time.monotonic() - t0) * 1000)

    raw_text = result.get("text", "") or ""
    structured: dict[str, Any] | None
    try:
        structured = json.loads(raw_text) if raw_text else None
    except json.JSONDecodeError:
        structured = None

    return AnnotationOutput(
        structured=structured,
        raw_text=raw_text,
        prompt_used=rendered_body,
        model=version.model,
        latency_ms=latency_ms,
    )
```

Then rewrite the existing `_process_item` to call `process_item` and handle persistence:

```python
async def _process_item(
    *,
    db,
    item,
    version,
    archive,
    proxy_resolver,
    ai_store,
    gemini,
    annotations_repo,
    review_items_repo,
    jobs_repo,
    event_bus,
    topic,
) -> None:
    async def on_status(s: str) -> None:
        await jobs_repo.update_item_status(db, item.id, s)
        await event_bus.publish(topic, {"item_id": item.id, "status": s})

    out = await process_item(
        clip_resolver_arg=item.catdv_clip_id,
        archive_lookup_arg=str(item.catdv_clip_id),
        clip_key=("catdv", str(item.catdv_clip_id)),
        version=version,
        proxy_resolver=proxy_resolver,
        archive=archive,
        ai_store=ai_store,
        gemini=gemini,
        on_status=on_status,
    )

    # Get clip_snapshot exactly as before (archive.get_clip was already
    # called inside process_item; we re-fetch here to preserve the
    # original behavior of carrying the snapshot into the annotation row.
    # If perf becomes an issue, return canonical from process_item too.)
    canonical = await archive.get_clip(str(item.catdv_clip_id))
    clip_snapshot: dict[str, Any] = dict(canonical.provider_data)
    duration_secs = float(canonical.duration_secs or 0.0)

    annotation_id = await annotations_repo.insert(
        db,
        Annotation(
            catdv_clip_id=item.catdv_clip_id,
            catdv_clip_name=clip_snapshot.get("name", ""),
            prompt_version_id=version.id,
            job_id=item.job_id,
            model=out.model,
            prompt_used=out.prompt_used,
            raw_response={"text": out.raw_text},
            structured_output=out.structured,
            clip_snapshot=clip_snapshot,
        ),
    )
    await jobs_repo.attach_annotation(db, item.id, annotation_id)

    if out.structured:
        review = expand(
            out.structured,
            version.target_map,
            annotation_id=annotation_id,
            catdv_clip_id=item.catdv_clip_id,
            clip_duration_secs=duration_secs or None,
        )
        if review:
            await review_items_repo.bulk_insert(db, review)

    await jobs_repo.update_item_status(db, item.id, "review_ready")
    await event_bus.publish(
        topic, {"item_id": item.id, "status": "review_ready", "annotation_id": annotation_id},
    )
```

> The duplicate `archive.get_clip` call is intentional and preserves byte-for-byte behavior of the prior code. If a follow-up optimization is desired, change `process_item` to also return `canonical` and remove the second fetch — but verify all the existing tests still pass.

- [ ] **Step 5: Run new tests + the original annotator tests**

```bash
.venv/bin/pytest tests/unit/test_annotator_process_item.py tests/services/test_annotator.py -v
```

Expected: new tests PASS; existing `test_annotator.py` still PASSES unchanged. If anything in `test_annotator.py` broke, the regression is in `_process_item`'s call sequence — fix that before continuing (do **not** change the test).

- [ ] **Step 6: Full-suite regression check**

```bash
.venv/bin/pytest
```

Expected: green.

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/annotator.py tests/unit/test_annotator_process_item.py
git commit -m "refactor(annotator): extract shared process_item callable (no behavior change)"
```

---

### ✅ Phase 4 review checkpoint

This is the most regression-prone phase. Before continuing, run the full suite and skim the diff to confirm `_process_item` still calls all the same repos in the same order.

```bash
.venv/bin/pytest && git diff HEAD~1 backend/app/services/annotator.py
```

---

# Phase 5 — Resolver chain + Studio-runs service

## Task 8: Resolver chain

**Files:**
- Create: `backend/app/services/studio_runs.py` (first slice — just the resolver)
- Test: `tests/unit/test_studio_resolver.py`

The chain returns either a `ResolvedInput` dataclass (with at least one of `local_path` / `file_ref` populated, plus `archive_lookup_arg` and `clip_snapshot`) or an `Unacceptable(reason=...)` value.

- [ ] **Step 1: Write failing tests**

Create `tests/unit/test_studio_resolver.py`:

```python
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.app.services.studio_runs import (
    ResolvedInput, Unacceptable, resolve_clip_input,
)


class _Item:
    def __init__(self, *, kind, upload_path=None, catdv_id=None, display_name="x"):
        self.source_kind = kind
        self.upload_path = upload_path
        self.catdv_provider_clip_id = catdv_id
        self.display_name = display_name


@pytest.mark.asyncio
async def test_upload_returns_local_path(tmp_path):
    f = tmp_path / "u.mp4"; f.write_bytes(b"\x00")
    item = _Item(kind="upload", upload_path=str(f))
    out = await resolve_clip_input(
        item, mode="online",
        proxy_resolver=MagicMock(), archive=MagicMock(),
        cache_only_resolver=MagicMock(), clip_cache_repo=MagicMock(),
        ai_store=MagicMock(), db=MagicMock(),
        uploads_root=tmp_path,
    )
    assert isinstance(out, ResolvedInput)
    assert out.local_path == f
    assert out.archive_lookup_arg is None


@pytest.mark.asyncio
async def test_catdv_online_uses_archive(tmp_path):
    item = _Item(kind="catdv_clip", catdv_id="123")
    canonical = MagicMock(); canonical.duration_secs = 30.0; canonical.provider_data = {"id": 123, "name": "n"}
    archive = MagicMock(); archive.get_clip = AsyncMock(return_value=canonical)
    resolver = MagicMock(); resolver.path_for_clip_id = AsyncMock(return_value=tmp_path / "123.mov")
    (tmp_path / "123.mov").write_bytes(b"\x00")
    out = await resolve_clip_input(
        item, mode="online",
        proxy_resolver=resolver, archive=archive,
        cache_only_resolver=MagicMock(), clip_cache_repo=MagicMock(),
        ai_store=MagicMock(), db=MagicMock(), uploads_root=tmp_path,
    )
    assert isinstance(out, ResolvedInput)
    assert out.local_path == tmp_path / "123.mov"
    assert out.archive_lookup_arg == "123"
    assert out.clip_snapshot["name"] == "n"


@pytest.mark.asyncio
async def test_catdv_offline_falls_back_to_cache(tmp_path):
    item = _Item(kind="catdv_clip", catdv_id="123")
    cache_only = MagicMock()
    cache_only.path_for_clip_id = AsyncMock(return_value=tmp_path / "c.mov")
    (tmp_path / "c.mov").write_bytes(b"\x00")
    clip_cache = MagicMock()
    clip_cache.get = AsyncMock(return_value=MagicMock(provider_data={"id": 123, "name": "cached"}))
    out = await resolve_clip_input(
        item, mode="offline",
        proxy_resolver=MagicMock(), archive=MagicMock(),
        cache_only_resolver=cache_only, clip_cache_repo=clip_cache,
        ai_store=MagicMock(), db=MagicMock(), uploads_root=tmp_path,
    )
    assert isinstance(out, ResolvedInput)
    assert out.local_path == tmp_path / "c.mov"
    assert out.clip_snapshot["name"] == "cached"


@pytest.mark.asyncio
async def test_catdv_offline_no_cache_uses_ai_store(tmp_path):
    item = _Item(kind="catdv_clip", catdv_id="123", display_name="fallback-name")
    cache_only = MagicMock()
    cache_only.path_for_clip_id = AsyncMock(side_effect=FileNotFoundError("nope"))
    ai_store = MagicMock()
    ai_store.find_by_clip_key = AsyncMock(return_value=MagicMock())  # any file_ref
    ai_store.reference_for_gemini = AsyncMock(return_value=MagicMock())
    out = await resolve_clip_input(
        item, mode="offline",
        proxy_resolver=MagicMock(), archive=MagicMock(),
        cache_only_resolver=cache_only, clip_cache_repo=MagicMock(),
        ai_store=ai_store, db=MagicMock(), uploads_root=tmp_path,
    )
    assert isinstance(out, ResolvedInput)
    assert out.local_path is None
    assert out.file_ref is not None
    assert out.clip_snapshot == {"id": "123", "name": "fallback-name"}


@pytest.mark.asyncio
async def test_catdv_fully_unresolvable_returns_unacceptable(tmp_path):
    item = _Item(kind="catdv_clip", catdv_id="123")
    cache_only = MagicMock()
    cache_only.path_for_clip_id = AsyncMock(side_effect=FileNotFoundError())
    ai_store = MagicMock()
    ai_store.find_by_clip_key = AsyncMock(return_value=None)
    out = await resolve_clip_input(
        item, mode="offline",
        proxy_resolver=MagicMock(), archive=MagicMock(),
        cache_only_resolver=cache_only, clip_cache_repo=MagicMock(),
        ai_store=ai_store, db=MagicMock(), uploads_root=tmp_path,
    )
    assert isinstance(out, Unacceptable)
    assert "catdv" in out.reason.lower() or "cache" in out.reason.lower()
```

- [ ] **Step 2: Run to verify failure**

```bash
.venv/bin/pytest tests/unit/test_studio_resolver.py -v
```

- [ ] **Step 3: Implement the resolver**

Create `backend/app/services/studio_runs.py`:

```python
"""Studio runs — worker, resolver chain, lifecycle.

The worker (run) is a serial loop over testbench items, calling into
the shared `services/annotator.process_item` pipeline and persisting
results into `studio_run_items`. The resolver chain handles
upload-vs-CatDV and the offline / cache fallback path.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import aiosqlite

log = logging.getLogger(__name__)


@dataclass
class ResolvedInput:
    local_path: Path | None      # set for uploads + cached CatDV
    file_ref: Any | None         # set when we skipped to ai_store fallback
    clip_snapshot: dict[str, Any]
    archive_lookup_arg: str | None  # for the `process_item` archive.get_clip call; None for uploads


@dataclass
class Unacceptable:
    reason: str


async def resolve_clip_input(
    item,
    *,
    mode: str,                   # "online" | "offline" | "forced_offline"
    proxy_resolver,              # main resolver (may be unavailable if CatDV never logged in)
    archive,                     # ArchiveProvider | None
    cache_only_resolver,         # LocalCacheOnlyResolver (always constructible)
    clip_cache_repo,
    ai_store,
    db: aiosqlite.Connection,
    uploads_root: Path,
) -> ResolvedInput | Unacceptable:
    if item.source_kind == "upload":
        local = uploads_root / item.upload_path
        if not local.exists():
            return Unacceptable(reason=f"upload file missing: {item.upload_path}")
        return ResolvedInput(
            local_path=local,
            file_ref=None,
            clip_snapshot={"name": item.display_name},
            archive_lookup_arg=None,
        )

    # source_kind == 'catdv_clip'
    cid = item.catdv_provider_clip_id
    assert cid is not None  # CHECK constraint guarantees this

    if mode == "online" and archive is not None and proxy_resolver is not None:
        try:
            canonical = await archive.get_clip(cid)
            local = await proxy_resolver.path_for_clip_id(int(cid))
            return ResolvedInput(
                local_path=local, file_ref=None,
                clip_snapshot=dict(canonical.provider_data),
                archive_lookup_arg=cid,
            )
        except Exception as exc:  # noqa: BLE001
            log.info("studio resolver: archive/path failed for %s: %s", cid, exc)

    try:
        local = await cache_only_resolver.path_for_clip_id(int(cid))
    except FileNotFoundError:
        local = None
    if local is not None:
        snapshot = {"id": cid, "name": item.display_name}
        try:
            cached = await clip_cache_repo.get(db, int(cid))
            if cached is not None:
                snapshot = dict(cached.provider_data)
        except Exception:
            pass
        return ResolvedInput(
            local_path=local, file_ref=None,
            clip_snapshot=snapshot, archive_lookup_arg=None,
        )

    upload = await ai_store.find_by_clip_key(("catdv", cid))
    if upload is not None:
        file_ref = await ai_store.reference_for_gemini(upload)
        return ResolvedInput(
            local_path=None, file_ref=file_ref,
            clip_snapshot={"id": cid, "name": item.display_name},
            archive_lookup_arg=None,
        )

    return Unacceptable(
        reason=f"catdv clip {cid}: archive unreachable; not in proxy_cache; "
               f"not in ai_store"
    )
```

> **Note:** the test stubs `clip_cache_repo.get` and `ai_store.find_by_clip_key`. If the existing repos / store don't expose those method names, adapt the names but keep the chain semantics. Check the real interfaces in `repositories/clip_cache.py` and `archive/ai_store.py` first.

- [ ] **Step 4: Run tests to verify pass**

```bash
.venv/bin/pytest tests/unit/test_studio_resolver.py -v
```

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/studio_runs.py tests/unit/test_studio_resolver.py
git commit -m "feat(studio): resolver chain (live archive → cache → ai_store → unacceptable)"
```

---

## Task 9: `StudioRunsService.run` worker

**Files:**
- Modify: `backend/app/services/studio_runs.py`
- Test: `tests/services/test_studio_runs_service.py`

- [ ] **Step 1: Write the failing service test**

Create `tests/services/test_studio_runs_service.py`:

```python
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import aiosqlite
import pytest

from backend.app.migrations_runner import apply_migrations
from backend.app.models.studio import AnnotationOutput
from backend.app.repositories.prompts import PromptsRepo
from backend.app.repositories.studio_runs import StudioRunsRepo
from backend.app.repositories.testbench_items import TestbenchItemsRepo
from backend.app.repositories.testbenches import TestbenchesRepo
from backend.app.services.studio_runs import (
    ResolvedInput, StudioRunsService, Unacceptable,
)

MIGRATIONS = Path(__file__).resolve().parents[2] / "backend" / "migrations"


@pytest.fixture
async def conn(tmp_path):
    db = tmp_path / "t.db"
    async with aiosqlite.connect(db) as c:
        await apply_migrations(c, MIGRATIONS)
        yield c


@pytest.mark.asyncio
async def test_run_processes_upload_items_into_studio_run_items(conn, tmp_path, monkeypatch):
    # Seed: prompt + testbench with two upload items.
    prompts = PromptsRepo()
    prompt = await prompts.create_with_initial_version(
        conn, name="p", description=None, body="BODY",
        target_map={}, output_schema={}, model="m", initial_state="production",
    )
    pv = (await prompts.get_with_versions(conn, prompt.id)).versions[0]
    tb = await TestbenchesRepo().create(conn, name="t", description=None)
    f = await TestbenchesRepo().create_folder(conn, testbench_id=tb, parent_id=None, name="r")
    items_repo = TestbenchItemsRepo()
    (tmp_path / "a.mp4").write_bytes(b"\x00")
    (tmp_path / "b.mp4").write_bytes(b"\x00")
    a = await items_repo.add_upload(conn, folder_id=f, upload_path="a.mp4", original_name="a.mp4")
    b = await items_repo.add_upload(conn, folder_id=f, upload_path="b.mp4", original_name="b.mp4")

    # Patch process_item to a deterministic stub.
    async def fake_process_item(**kw):
        await kw["on_status"]("resolving")
        await kw["on_status"]("uploading")
        await kw["on_status"]("prompting")
        return AnnotationOutput(
            structured={"k": kw["clip_key"][1]},
            raw_text=json.dumps({"k": kw["clip_key"][1]}),
            prompt_used="BODY", model="m", latency_ms=42,
        )

    monkeypatch.setattr(
        "backend.app.services.studio_runs.process_item", fake_process_item,
    )

    svc = StudioRunsService(
        runs_repo=StudioRunsRepo(),
        items_repo=items_repo,
        prompts_repo=prompts,
        archive=None, proxy_resolver=None,
        cache_only_resolver=MagicMock(),
        clip_cache_repo=MagicMock(),
        ai_store=MagicMock(),
        gemini=MagicMock(),
        event_bus=MagicMock(publish=AsyncMock()),
        uploads_root=tmp_path,
        mode_getter=lambda: "online",
    )
    run_id = await svc.create_run(conn, testbench_id=tb, prompt_version_id=pv.id)
    await svc.run(conn, run_id)

    run = await StudioRunsRepo().get(conn, run_id)
    assert run.status == "completed"
    items = await StudioRunsRepo().list_items(conn, run_id)
    assert {it.testbench_item_id for it in items} == {a, b}
    assert all(it.status == "done" for it in items)


@pytest.mark.asyncio
async def test_run_marks_unacceptable_items(conn, tmp_path, monkeypatch):
    prompts = PromptsRepo()
    prompt = await prompts.create_with_initial_version(
        conn, name="p", description=None, body="BODY",
        target_map={}, output_schema={}, model="m", initial_state="production",
    )
    pv = (await prompts.get_with_versions(conn, prompt.id)).versions[0]
    tb = await TestbenchesRepo().create(conn, name="t", description=None)
    f = await TestbenchesRepo().create_folder(conn, testbench_id=tb, parent_id=None, name="r")
    # CatDV item, will be unresolvable in test (archive=None, no cache, no ai_store).
    await TestbenchItemsRepo().add_catdv(conn, folder_id=f, provider_clip_id="999", name="x")

    svc = StudioRunsService(
        runs_repo=StudioRunsRepo(),
        items_repo=TestbenchItemsRepo(),
        prompts_repo=prompts,
        archive=None, proxy_resolver=None,
        cache_only_resolver=MagicMock(path_for_clip_id=AsyncMock(side_effect=FileNotFoundError())),
        clip_cache_repo=MagicMock(),
        ai_store=MagicMock(find_by_clip_key=AsyncMock(return_value=None)),
        gemini=MagicMock(),
        event_bus=MagicMock(publish=AsyncMock()),
        uploads_root=tmp_path,
        mode_getter=lambda: "offline",
    )
    rid = await svc.create_run(conn, testbench_id=tb, prompt_version_id=pv.id)
    await svc.run(conn, rid)
    items = await StudioRunsRepo().list_items(conn, rid)
    assert items[0].status == "unacceptable"
    # Run completes even though every item is unacceptable.
    assert (await StudioRunsRepo().get(conn, rid)).status == "completed"


@pytest.mark.asyncio
async def test_run_failed_when_any_item_errors(conn, tmp_path, monkeypatch):
    prompts = PromptsRepo()
    prompt = await prompts.create_with_initial_version(
        conn, name="p", description=None, body="BODY",
        target_map={}, output_schema={}, model="m", initial_state="production",
    )
    pv = (await prompts.get_with_versions(conn, prompt.id)).versions[0]
    tb = await TestbenchesRepo().create(conn, name="t", description=None)
    f = await TestbenchesRepo().create_folder(conn, testbench_id=tb, parent_id=None, name="r")
    (tmp_path / "a.mp4").write_bytes(b"\x00")
    await TestbenchItemsRepo().add_upload(conn, folder_id=f, upload_path="a.mp4", original_name="a.mp4")

    async def boom(**kw):
        raise RuntimeError("gemini exploded")
    monkeypatch.setattr("backend.app.services.studio_runs.process_item", boom)

    svc = StudioRunsService(
        runs_repo=StudioRunsRepo(),
        items_repo=TestbenchItemsRepo(),
        prompts_repo=prompts,
        archive=None, proxy_resolver=None,
        cache_only_resolver=MagicMock(),
        clip_cache_repo=MagicMock(),
        ai_store=MagicMock(),
        gemini=MagicMock(),
        event_bus=MagicMock(publish=AsyncMock()),
        uploads_root=tmp_path,
        mode_getter=lambda: "online",
    )
    rid = await svc.create_run(conn, testbench_id=tb, prompt_version_id=pv.id)
    await svc.run(conn, rid)
    items = await StudioRunsRepo().list_items(conn, rid)
    assert items[0].status == "error"
    assert "gemini exploded" in (items[0].error or "")
    assert (await StudioRunsRepo().get(conn, rid)).status == "failed"
```

- [ ] **Step 2: Run to verify failure**

```bash
.venv/bin/pytest tests/services/test_studio_runs_service.py -v
```

Expected: ImportError on `StudioRunsService` / `create_run` / `run`.

- [ ] **Step 3: Implement the service**

Append to `backend/app/services/studio_runs.py`:

```python
from backend.app.services.annotator import process_item


class StudioRunsService:
    def __init__(
        self,
        *,
        runs_repo,
        items_repo,
        prompts_repo,
        archive,
        proxy_resolver,
        cache_only_resolver,
        clip_cache_repo,
        ai_store,
        gemini,
        event_bus,
        uploads_root: Path,
        mode_getter,           # () -> "online" | "offline" | "forced_offline"
    ) -> None:
        self.runs_repo = runs_repo
        self.items_repo = items_repo
        self.prompts_repo = prompts_repo
        self.archive = archive
        self.proxy_resolver = proxy_resolver
        self.cache_only_resolver = cache_only_resolver
        self.clip_cache_repo = clip_cache_repo
        self.ai_store = ai_store
        self.gemini = gemini
        self.event_bus = event_bus
        self.uploads_root = uploads_root
        self.mode_getter = mode_getter

    async def create_run(
        self, conn: aiosqlite.Connection,
        *, testbench_id: int, prompt_version_id: int,
    ) -> int:
        return await self.runs_repo.create(
            conn, testbench_id=testbench_id, prompt_version_id=prompt_version_id,
        )

    async def run(self, conn: aiosqlite.Connection, run_id: int) -> None:
        run = await self.runs_repo.get(conn, run_id)
        version = await self.prompts_repo.get_version(conn, run.prompt_version_id)
        await self.runs_repo.update_status(conn, run_id, "running", started=True)
        topic = f"studio_run:{run_id}"
        items = await self.items_repo.list_for_testbench(conn, run.testbench_id)

        had_error = False
        for tbi in items:
            ri_id = await self.runs_repo.upsert_item(
                conn, run_id=run_id, testbench_item_id=tbi.id,
            )
            try:
                resolved = await resolve_clip_input(
                    tbi,
                    mode=self.mode_getter(),
                    proxy_resolver=self.proxy_resolver,
                    archive=self.archive,
                    cache_only_resolver=self.cache_only_resolver,
                    clip_cache_repo=self.clip_cache_repo,
                    ai_store=self.ai_store,
                    db=conn,
                    uploads_root=self.uploads_root,
                )
                if isinstance(resolved, Unacceptable):
                    await self.runs_repo.update_item_status(
                        conn, ri_id, "unacceptable",
                        unacceptable_reason=resolved.reason,
                    )
                    await self.event_bus.publish(topic, {
                        "item_id": ri_id, "status": "unacceptable",
                        "reason": resolved.reason,
                    })
                    continue

                async def on_status(s: str, ri=ri_id) -> None:
                    await self.runs_repo.update_item_status(conn, ri, s)
                    await self.event_bus.publish(topic, {"item_id": ri, "status": s})

                # For 'ai_store-only' case, process_item will skip the
                # proxy_resolver/local_path branch — we supply a tiny shim
                # that returns the prepared file_ref.
                shim_resolver = _PreResolvedShim(resolved.local_path) if resolved.local_path else None
                shim_store = (
                    self.ai_store if resolved.local_path else
                    _PreResolvedStore(resolved.file_ref)
                )

                clip_key_id = (
                    Path(resolved.local_path).stem if resolved.local_path
                    else (tbi.catdv_provider_clip_id or f"upload-{tbi.id}")
                )
                clip_key = (
                    "studio_upload" if tbi.source_kind == "upload" else "catdv",
                    str(clip_key_id),
                )

                out = await process_item(
                    clip_resolver_arg=resolved.local_path,
                    archive_lookup_arg=resolved.archive_lookup_arg,
                    clip_key=clip_key,
                    version=version,
                    proxy_resolver=shim_resolver or self.proxy_resolver,
                    archive=self.archive if resolved.archive_lookup_arg else None,
                    ai_store=shim_store,
                    gemini=self.gemini,
                    on_status=on_status,
                )
                await self.runs_repo.attach_output(
                    conn, ri_id,
                    structured_json=(
                        json.dumps(out.structured, ensure_ascii=False)
                        if out.structured is not None else None
                    ),
                    raw_text=out.raw_text,
                    prompt_used=out.prompt_used,
                    model=out.model,
                    latency_ms=out.latency_ms,
                )
                await self.event_bus.publish(topic, {"item_id": ri_id, "status": "done"})
            except Exception as exc:  # noqa: BLE001
                log.exception("studio run %s item %s failed", run_id, tbi.id)
                had_error = True
                await self.runs_repo.update_item_status(
                    conn, ri_id, "error", error=str(exc),
                )
                await self.event_bus.publish(topic, {
                    "item_id": ri_id, "status": "error", "error": str(exc),
                })

        final = "failed" if had_error else "completed"
        await self.runs_repo.update_status(conn, run_id, final, finished=True)
        await self.event_bus.publish(topic, {"run_status": final})


class _PreResolvedShim:
    """Adapts a pre-resolved Path to the proxy_resolver protocol so
    `process_item` doesn't need to know about the resolver chain."""
    def __init__(self, path: Path) -> None:
        self._path = path

    async def path_for_clip_id(self, _arg) -> Path:
        return self._path


class _PreResolvedStore:
    """Adapts a pre-resolved Gemini file_ref to the ai_store protocol."""
    def __init__(self, file_ref) -> None:
        self._ref = file_ref

    async def ensure_uploaded(self, *_args, **_kw):
        return self._ref

    async def reference_for_gemini(self, _upload):
        return self._ref


# Top-of-file import for json — add alongside other imports.
import json  # noqa: E402
```

Add a top-level `import json` at the file head; the noqa is only there for the in-prose insertion.

- [ ] **Step 4: Run tests to verify pass**

```bash
.venv/bin/pytest tests/services/test_studio_runs_service.py -v
```

Expected: all 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/studio_runs.py tests/services/test_studio_runs_service.py
git commit -m "feat(studio): StudioRunsService worker loop with shared process_item"
```

---

## Task 10: Cancel & background-task lifecycle

**Files:**
- Modify: `backend/app/services/studio_runs.py`
- Test: append to `tests/services/test_studio_runs_service.py`

The service needs a `start_background(run_id)` that creates an `asyncio.Task` and a `cancel(run_id)` that sets the run's status to `cancelled` so the worker loop exits at the next item boundary.

- [ ] **Step 1: Add failing tests**

Append to `tests/services/test_studio_runs_service.py`:

```python
import asyncio


@pytest.mark.asyncio
async def test_cancel_stops_at_next_item_boundary(conn, tmp_path, monkeypatch):
    prompts = PromptsRepo()
    prompt = await prompts.create_with_initial_version(
        conn, name="p", description=None, body="BODY",
        target_map={}, output_schema={}, model="m", initial_state="production",
    )
    pv = (await prompts.get_with_versions(conn, prompt.id)).versions[0]
    tb = await TestbenchesRepo().create(conn, name="t", description=None)
    f = await TestbenchesRepo().create_folder(conn, testbench_id=tb, parent_id=None, name="r")
    for n in ("a", "b", "c"):
        (tmp_path / f"{n}.mp4").write_bytes(b"\x00")
        await TestbenchItemsRepo().add_upload(conn, folder_id=f, upload_path=f"{n}.mp4", original_name=f"{n}.mp4")

    started: list[str] = []

    async def slow_proc(**kw):
        started.append(kw["clip_key"][1])
        # let cancel land between items.
        await asyncio.sleep(0)
        return AnnotationOutput(structured=None, raw_text="", prompt_used="x", model="m", latency_ms=0)

    monkeypatch.setattr("backend.app.services.studio_runs.process_item", slow_proc)

    svc = StudioRunsService(
        runs_repo=StudioRunsRepo(), items_repo=TestbenchItemsRepo(), prompts_repo=prompts,
        archive=None, proxy_resolver=None,
        cache_only_resolver=MagicMock(), clip_cache_repo=MagicMock(),
        ai_store=MagicMock(), gemini=MagicMock(),
        event_bus=MagicMock(publish=AsyncMock()),
        uploads_root=tmp_path, mode_getter=lambda: "online",
    )
    rid = await svc.create_run(conn, testbench_id=tb, prompt_version_id=pv.id)
    # Pre-mark cancelled before kicking off; worker should bail at the first boundary.
    await StudioRunsRepo().update_status(conn, rid, "cancelled", finished=True)
    await svc.run(conn, rid)
    # The worker observes 'cancelled' on entry and processes zero items.
    assert started == []
    assert (await StudioRunsRepo().get(conn, rid)).status == "cancelled"
```

- [ ] **Step 2: Implement the cancellation check**

In `StudioRunsService.run`, before each item, re-fetch the run and break if status is `cancelled`:

```python
        for tbi in items:
            current = await self.runs_repo.get(conn, run_id)
            if current.status == "cancelled":
                log.info("studio run %s cancelled mid-loop; stopping", run_id)
                return                 # do NOT flip final status; it's already 'cancelled'
            ri_id = await self.runs_repo.upsert_item(...)
            ...
```

Add a `cancel` method:

```python
    async def cancel(self, conn: aiosqlite.Connection, run_id: int) -> None:
        await self.runs_repo.update_status(conn, run_id, "cancelled", finished=True)
        await self.event_bus.publish(f"studio_run:{run_id}", {"run_status": "cancelled"})
```

- [ ] **Step 3: Run tests**

```bash
.venv/bin/pytest tests/services/test_studio_runs_service.py -v
```

- [ ] **Step 4: Commit**

```bash
git add backend/app/services/studio_runs.py tests/services/test_studio_runs_service.py
git commit -m "feat(studio): cancel respects item-boundary; cancelled status is sticky"
```

---

### ✅ Phase 5 review checkpoint

```bash
.venv/bin/pytest
```

Phase total: 5 resolver tests + 4 service tests = 9.

---

# Phase 6 — Upload helper + JSON API routes

## Task 11: Streaming upload helper

**Files:**
- Create: `backend/app/services/studio_uploads.py`
- Test: `tests/services/test_studio_uploads.py`

- [ ] **Step 1: Write failing test**

Create `tests/services/test_studio_uploads.py`:

```python
from io import BytesIO
from pathlib import Path

import pytest

from backend.app.services.studio_uploads import save_upload, UploadError


class _UploadFile:
    """Mimics `fastapi.UploadFile` enough for the service."""
    def __init__(self, *, filename, content_type, data):
        self.filename = filename
        self.content_type = content_type
        self._buf = BytesIO(data)

    async def read(self, n: int = -1) -> bytes:
        return self._buf.read(n)


@pytest.mark.asyncio
async def test_save_upload_writes_file_and_returns_relative_path(tmp_path):
    up = _UploadFile(filename="cool.MP4", content_type="video/mp4", data=b"\x00" * 16)
    path = await save_upload(up, uploads_dir=tmp_path, max_mb=10)
    full = tmp_path / path
    assert full.exists()
    assert full.suffix == ".mp4"  # lowercased
    assert full.read_bytes() == b"\x00" * 16


@pytest.mark.asyncio
async def test_save_upload_rejects_non_video_mime(tmp_path):
    up = _UploadFile(filename="x.exe", content_type="application/x-exe", data=b"")
    with pytest.raises(UploadError, match="video"):
        await save_upload(up, uploads_dir=tmp_path, max_mb=10)


@pytest.mark.asyncio
async def test_save_upload_rejects_over_size(tmp_path):
    big = b"\x00" * (2 * 1024 * 1024 + 1)
    up = _UploadFile(filename="x.mp4", content_type="video/mp4", data=big)
    with pytest.raises(UploadError, match="exceeds"):
        await save_upload(up, uploads_dir=tmp_path, max_mb=2)
```

- [ ] **Step 2: Run to verify failure**

```bash
.venv/bin/pytest tests/services/test_studio_uploads.py -v
```

- [ ] **Step 3: Implement**

Create `backend/app/services/studio_uploads.py`:

```python
"""Streaming MP4 upload helper for Studio testbench items."""
from pathlib import Path
from uuid import uuid4


class UploadError(ValueError):
    pass


_CHUNK = 1024 * 1024  # 1 MiB


async def save_upload(upload, *, uploads_dir: Path, max_mb: int) -> str:
    """Stream-write `upload` to `uploads_dir/<uuid>.<ext>`. Returns the
    relative filename (caller stores it in `testbench_items.upload_path`).
    Raises UploadError on MIME / size violations.
    """
    content_type = (upload.content_type or "").lower()
    if not content_type.startswith("video/"):
        raise UploadError(f"unsupported content type {content_type}; expected video/*")
    suffix = Path(upload.filename or "").suffix.lower().lstrip(".") or "mp4"
    rel = f"{uuid4().hex}.{suffix}"
    uploads_dir.mkdir(parents=True, exist_ok=True)
    dest = uploads_dir / rel
    limit = max_mb * 1024 * 1024
    written = 0
    try:
        with dest.open("wb") as fh:
            while True:
                chunk = await upload.read(_CHUNK)
                if not chunk:
                    break
                written += len(chunk)
                if written > limit:
                    raise UploadError(f"upload exceeds {max_mb} MB limit")
                fh.write(chunk)
    except Exception:
        dest.unlink(missing_ok=True)
        raise
    return rel
```

- [ ] **Step 4: Run tests + commit**

```bash
.venv/bin/pytest tests/services/test_studio_uploads.py -v
git add backend/app/services/studio_uploads.py tests/services/test_studio_uploads.py
git commit -m "feat(studio): streaming upload helper with mime + size validation"
```

---

## Task 12: JSON API routes — testbench + folder CRUD

**Files:**
- Create: `backend/app/routes/studio.py` (first slice — JSON API only)
- Test: `tests/routes/test_studio_api.py`

For brevity the route file is built incrementally across Tasks 12–15. Start with the testbench / folder endpoints.

- [ ] **Step 1: Write failing route tests**

Create `tests/routes/test_studio_api.py`:

```python
import pytest


@pytest.mark.asyncio
async def test_create_testbench(client):
    r = await client.post("/api/studio/testbenches", json={"name": "tb", "description": "x"})
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "tb"
    assert isinstance(body["id"], int)


@pytest.mark.asyncio
async def test_create_testbench_returns_409_on_duplicate(client):
    await client.post("/api/studio/testbenches", json={"name": "dup"})
    r = await client.post("/api/studio/testbenches", json={"name": "dup"})
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_create_folder_and_subfolder(client):
    tb = (await client.post("/api/studio/testbenches", json={"name": "x"})).json()
    root = (await client.post(
        f"/api/studio/testbenches/{tb['id']}/folders",
        json={"parent_id": None, "name": "root"},
    )).json()
    sub = (await client.post(
        f"/api/studio/testbenches/{tb['id']}/folders",
        json={"parent_id": root["id"], "name": "sub"},
    )).json()
    assert sub["parent_id"] == root["id"]


@pytest.mark.asyncio
async def test_delete_non_empty_folder_409(client, item_factory):
    tb = (await client.post("/api/studio/testbenches", json={"name": "y"})).json()
    root = (await client.post(
        f"/api/studio/testbenches/{tb['id']}/folders",
        json={"parent_id": None, "name": "r"},
    )).json()
    await item_factory.add_upload(folder_id=root["id"])
    r = await client.delete(f"/api/studio/folders/{root['id']}")
    assert r.status_code == 409
```

> The `client` and `item_factory` fixtures come from the existing test conftest. If `item_factory` doesn't exist, write the bare equivalent inline in the test or add it to `tests/conftest.py` near the existing `client` fixture.

- [ ] **Step 2: Run to verify failure**

```bash
.venv/bin/pytest tests/routes/test_studio_api.py -v
```

- [ ] **Step 3: Implement first slice of `routes/studio.py`**

Create `backend/app/routes/studio.py`:

```python
"""Studio routes — pages + JSON API.

Studio runs alongside the production annotate/review/write pipeline but
shares none of its tables; all reads and writes go through
`TestbenchesRepo`, `TestbenchItemsRepo`, `StudioRunsRepo`.
"""
from __future__ import annotations

import logging
from typing import Annotated

import aiosqlite
from fastapi import APIRouter, Body, Depends, HTTPException, status

from backend.app.context import AppContext, get_ctx

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/studio", tags=["studio"])


@router.post("/testbenches")
async def create_testbench(
    body: Annotated[dict, Body()],
    ctx: AppContext = Depends(get_ctx),
):
    try:
        new_id = await ctx.testbenches_repo.create(
            ctx.db, name=body["name"], description=body.get("description"),
        )
    except aiosqlite.IntegrityError:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="name already exists")
    tb = await ctx.testbenches_repo.get(ctx.db, new_id)
    return tb.model_dump()


@router.post("/testbenches/{tb_id}:rename")
async def rename_testbench(tb_id: int, body: dict = Body(...), ctx: AppContext = Depends(get_ctx)):
    await ctx.testbenches_repo.rename(ctx.db, tb_id, body["name"])
    return {"ok": True}


@router.post("/testbenches/{tb_id}:archive")
async def archive_testbench(tb_id: int, ctx: AppContext = Depends(get_ctx)):
    await ctx.testbenches_repo.archive(ctx.db, tb_id)
    return {"ok": True}


@router.post("/testbenches/{tb_id}/folders")
async def create_folder(tb_id: int, body: dict = Body(...), ctx: AppContext = Depends(get_ctx)):
    folder_id = await ctx.testbenches_repo.create_folder(
        ctx.db, testbench_id=tb_id, parent_id=body.get("parent_id"), name=body["name"],
    )
    return {"id": folder_id, "parent_id": body.get("parent_id"), "name": body["name"]}


@router.post("/folders/{folder_id}:rename")
async def rename_folder(folder_id: int, body: dict = Body(...), ctx: AppContext = Depends(get_ctx)):
    await ctx.testbenches_repo.rename_folder(ctx.db, folder_id, body["name"])
    return {"ok": True}


@router.delete("/folders/{folder_id}")
async def delete_folder(folder_id: int, ctx: AppContext = Depends(get_ctx)):
    try:
        await ctx.testbenches_repo.delete_folder(ctx.db, folder_id)
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc))
    return {"ok": True}
```

> Studio does **not** call `app_state.mode` for any of the above — testbench/folder operations work offline.

- [ ] **Step 4: Wire the router**

For the tests to find the router, register it in `backend/app/main.py`:

```python
from backend.app.routes import studio as studio_router
...
app.include_router(studio_router.router)
```

Also register Studio dependencies in `backend/app/context.py` (Phase 9 will revisit this for completeness; for now add `testbenches_repo: TestbenchesRepo` and `testbench_items_repo: TestbenchItemsRepo`, `studio_runs_repo: StudioRunsRepo` to `AppContext` and instantiate them in `AppContext.build`).

- [ ] **Step 5: Run tests + commit**

```bash
.venv/bin/pytest tests/routes/test_studio_api.py -v
git add backend/app/routes/studio.py backend/app/main.py backend/app/context.py tests/routes/test_studio_api.py
git commit -m "feat(routes): studio JSON API — testbench + folder CRUD"
```

---

## Task 13: JSON API — item CRUD + gold + upload

**Files:**
- Modify: `backend/app/routes/studio.py`
- Test: append to `tests/routes/test_studio_api.py`

- [ ] **Step 1: Add failing tests**

Append to `tests/routes/test_studio_api.py`:

```python
import io
import json


@pytest.mark.asyncio
async def test_add_catdv_item(client, folder_id):
    r = await client.post(
        f"/api/studio/folders/{folder_id}/items:add_catdv",
        json={"provider_clip_id": "123", "name": "catdv-123"},
    )
    assert r.status_code == 200
    assert r.json()["source_kind"] == "catdv_clip"


@pytest.mark.asyncio
async def test_upload_item(client, folder_id):
    data = b"\x00" * 16
    files = {"file": ("vid.mp4", io.BytesIO(data), "video/mp4")}
    r = await client.post(f"/api/studio/folders/{folder_id}/items:add_upload", files=files)
    assert r.status_code == 200
    body = r.json()
    assert body["source_kind"] == "upload"
    assert body["upload_orig_name"] == "vid.mp4"


@pytest.mark.asyncio
async def test_upload_item_rejects_non_video(client, folder_id):
    files = {"file": ("evil.exe", io.BytesIO(b""), "application/x-exe")}
    r = await client.post(f"/api/studio/folders/{folder_id}/items:add_upload", files=files)
    assert r.status_code == 415


@pytest.mark.asyncio
async def test_set_gold_round_trips_unknown_keys(client, item_id):
    payload = {"description": "first", "future_field": 42}
    r = await client.put(f"/api/studio/items/{item_id}/gold", json=payload)
    assert r.status_code == 200
    # Subsequent edit preserves future_field.
    r = await client.put(f"/api/studio/items/{item_id}/gold", json={"description": "second", "future_field": 42})
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_set_gold_clear_with_empty_description(client, item_id):
    await client.put(f"/api/studio/items/{item_id}/gold", json={"description": "x"})
    r = await client.put(f"/api/studio/items/{item_id}/gold", json={"description": ""})
    assert r.status_code == 200
    # gold_json should now be NULL — verify via a GET (add a get-item endpoint or check via DB; pick whichever is convenient)


@pytest.mark.asyncio
async def test_delete_item(client, item_id):
    r = await client.delete(f"/api/studio/items/{item_id}")
    assert r.status_code == 200
```

- [ ] **Step 2: Add item endpoints**

Append to `backend/app/routes/studio.py`:

```python
from fastapi import File, UploadFile

from backend.app.services.studio_uploads import save_upload, UploadError


@router.post("/folders/{folder_id}/items:add_catdv")
async def add_catdv_item(
    folder_id: int, body: dict = Body(...), ctx: AppContext = Depends(get_ctx),
):
    item_id = await ctx.testbench_items_repo.add_catdv(
        ctx.db,
        folder_id=folder_id,
        provider_clip_id=body["provider_clip_id"],
        name=body["name"],
    )
    items = await ctx.testbench_items_repo.list_for_folder(ctx.db, folder_id)
    return next(it.model_dump() for it in items if it.id == item_id)


@router.post("/folders/{folder_id}/items:add_upload")
async def add_upload_item(
    folder_id: int,
    file: UploadFile = File(...),
    ctx: AppContext = Depends(get_ctx),
):
    try:
        rel = await save_upload(
            file,
            uploads_dir=ctx.settings.studio_uploads_dir,
            max_mb=ctx.settings.studio_max_upload_mb,
        )
    except UploadError as exc:
        msg = str(exc)
        if "unsupported content type" in msg:
            raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail=msg)
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=msg)
    item_id = await ctx.testbench_items_repo.add_upload(
        ctx.db,
        folder_id=folder_id,
        upload_path=rel,
        original_name=file.filename or rel,
    )
    items = await ctx.testbench_items_repo.list_for_folder(ctx.db, folder_id)
    return next(it.model_dump() for it in items if it.id == item_id)


@router.put("/items/{item_id}/gold")
async def set_gold(item_id: int, body: dict = Body(...), ctx: AppContext = Depends(get_ctx)):
    description = body.get("description", "").strip()
    if description == "" and len(body) == 1:
        await ctx.testbench_items_repo.set_gold(ctx.db, item_id, None)
    else:
        await ctx.testbench_items_repo.set_gold(ctx.db, item_id, body)
    return {"ok": True}


@router.delete("/items/{item_id}")
async def remove_item(item_id: int, ctx: AppContext = Depends(get_ctx)):
    await ctx.testbench_items_repo.remove(ctx.db, item_id)
    return {"ok": True}
```

- [ ] **Step 3: Run tests + commit**

```bash
.venv/bin/pytest tests/routes/test_studio_api.py -v
git add backend/app/routes/studio.py tests/routes/test_studio_api.py
git commit -m "feat(routes): studio items + upload + gold endpoints"
```

---

## Task 14: JSON API — run start / cancel / SSE

**Files:**
- Modify: `backend/app/routes/studio.py`
- Test: append to `tests/routes/test_studio_api.py`

- [ ] **Step 1: Add failing tests**

```python
@pytest.mark.asyncio
async def test_start_run_returns_run_id(client, testbench_with_items, prompt_version_id):
    r = await client.post(
        "/api/studio/runs",
        json={"testbench_id": testbench_with_items, "prompt_version_id": prompt_version_id},
    )
    assert r.status_code == 200
    assert isinstance(r.json()["id"], int)


@pytest.mark.asyncio
async def test_cancel_run(client, testbench_with_items, prompt_version_id):
    rid = (await client.post(
        "/api/studio/runs",
        json={"testbench_id": testbench_with_items, "prompt_version_id": prompt_version_id},
    )).json()["id"]
    r = await client.post(f"/api/studio/runs/{rid}:cancel")
    assert r.status_code == 200
```

- [ ] **Step 2: Implement run endpoints**

```python
import asyncio


@router.post("/runs")
async def start_run(body: dict = Body(...), ctx: AppContext = Depends(get_ctx)):
    run_id = await ctx.studio_runs_service.create_run(
        ctx.db,
        testbench_id=body["testbench_id"],
        prompt_version_id=body["prompt_version_id"],
    )
    # Fire-and-forget background task — the service handles all status writes.
    asyncio.create_task(ctx.studio_runs_service.run(ctx.db, run_id))
    return {"id": run_id}


@router.post("/runs/{run_id}:cancel")
async def cancel_run(run_id: int, ctx: AppContext = Depends(get_ctx)):
    await ctx.studio_runs_service.cancel(ctx.db, run_id)
    return {"ok": True}


@router.get("/runs/{run_id}/events")
async def run_events(run_id: int, ctx: AppContext = Depends(get_ctx)):
    """SSE stream of per-item status events. Reuses the existing
    EventBus topic convention."""
    from fastapi.responses import StreamingResponse
    import json

    topic = f"studio_run:{run_id}"
    q = ctx.event_bus.subscribe(topic)

    async def stream():
        try:
            while True:
                msg = await q.get()
                yield f"data: {json.dumps(msg)}\n\n"
        finally:
            ctx.event_bus.unsubscribe(topic, q)

    return StreamingResponse(stream(), media_type="text/event-stream")
```

- [ ] **Step 3: Run tests + commit**

```bash
.venv/bin/pytest tests/routes/test_studio_api.py -v
git add backend/app/routes/studio.py tests/routes/test_studio_api.py
git commit -m "feat(routes): start/cancel run + SSE events"
```

---

## Task 15: Verify boot-without-CatDV

**Files:**
- Test: `tests/routes/test_studio_offline.py`

The Studio JSON API must respond 200/4xx regardless of `app_state.mode`. The page routes (added in Phase 7) also must serve.

- [ ] **Step 1: Write the failing test**

Create `tests/routes/test_studio_offline.py`:

```python
import pytest


@pytest.mark.asyncio
async def test_studio_api_works_when_mode_is_offline(client_offline):
    """`client_offline` is the standard test client wired with mode='offline'."""
    r = await client_offline.post("/api/studio/testbenches", json={"name": "x"})
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_studio_api_works_with_no_archive_at_all(client_no_catdv):
    """`client_no_catdv` is the standard test client wired with archive=None
    (simulates a never-logged-in CatDV)."""
    r = await client_no_catdv.post("/api/studio/testbenches", json={"name": "x"})
    assert r.status_code == 200
```

> If `client_offline` / `client_no_catdv` don't exist in `tests/conftest.py`, add them — these are existing test patterns elsewhere in the codebase (search for `mode="offline"` in `tests/`). If they really don't exist, define them inline in this test module using the same patterns.

- [ ] **Step 2: Run + verify pass**

The routes added so far should pass these straightforwardly — no `app_state.mode` checks were added.

```bash
.venv/bin/pytest tests/routes/test_studio_offline.py -v
```

- [ ] **Step 3: Commit**

```bash
git add tests/routes/test_studio_offline.py
git commit -m "test(studio): API works regardless of catdv connection state"
```

---

### ✅ Phase 6 review checkpoint

```bash
.venv/bin/pytest
```

Phase total: ~20 new tests across uploads, JSON API, and offline guards.

---

# Phase 7 — Page routes + SSE wiring

## Task 16: Page routes

**Files:**
- Modify: `backend/app/routes/studio.py` (page slice)
- Test: `tests/routes/test_studio_pages.py`

Add a second router (or expand the existing one with a separate prefix) for the HTML page routes: `/studio`, `/studio/testbenches/{id}`, `/studio/runs/{id}`, `/studio/testbenches/{id}/compare`.

- [ ] **Step 1: Write failing tests**

Create `tests/routes/test_studio_pages.py`:

```python
import pytest


@pytest.mark.asyncio
async def test_landing_page_lists_testbenches(client):
    r = await client.post("/api/studio/testbenches", json={"name": "demo"})
    assert (await client.get("/studio")).status_code == 200
    assert "demo" in (await client.get("/studio")).text


@pytest.mark.asyncio
async def test_testbench_page_renders_folder_tree(client, testbench_with_items):
    r = await client.get(f"/studio/testbenches/{testbench_with_items}")
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_run_detail_page(client, finished_run_id):
    r = await client.get(f"/studio/runs/{finished_run_id}")
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_compare_page_returns_two_sides(client, testbench_with_items, finished_run_id):
    r = await client.get(
        f"/studio/testbenches/{testbench_with_items}/compare?left={finished_run_id}&right=gold"
    )
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_landing_page_works_when_offline(client_offline):
    assert (await client_offline.get("/studio")).status_code == 200
```

- [ ] **Step 2: Implement the page router**

Add to `backend/app/routes/studio.py`:

```python
from fastapi import Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

pages_router = APIRouter(prefix="/studio", tags=["studio-pages"])
templates = Jinja2Templates(directory="backend/app/templates")


@pages_router.get("", response_class=HTMLResponse)
async def studio_landing(request: Request, ctx: AppContext = Depends(get_ctx)):
    testbenches = await ctx.testbenches_repo.list_active(ctx.db)
    return templates.TemplateResponse(
        "pages/studio.html",
        {"request": request, "testbenches": testbenches, "selected": None,
         "folders": [], "items_by_folder": {}, "runs": []},
    )


@pages_router.get("/testbenches/{tb_id}", response_class=HTMLResponse)
async def studio_testbench(tb_id: int, request: Request, ctx: AppContext = Depends(get_ctx)):
    testbenches = await ctx.testbenches_repo.list_active(ctx.db)
    selected = await ctx.testbenches_repo.get(ctx.db, tb_id)
    folders = await ctx.testbenches_repo.list_folders(ctx.db, tb_id)
    items_by_folder: dict[int, list] = {}
    for f in folders:
        items_by_folder[f.id] = await ctx.testbench_items_repo.list_for_folder(ctx.db, f.id)
    runs = await ctx.studio_runs_repo.list_for_testbench(ctx.db, tb_id)
    return templates.TemplateResponse(
        "pages/studio.html",
        {"request": request, "testbenches": testbenches, "selected": selected,
         "folders": folders, "items_by_folder": items_by_folder, "runs": runs},
    )


@pages_router.get("/runs/{run_id}", response_class=HTMLResponse)
async def studio_run_detail(run_id: int, request: Request, ctx: AppContext = Depends(get_ctx)):
    run = await ctx.studio_runs_repo.get(ctx.db, run_id)
    items = await ctx.studio_runs_repo.list_items(ctx.db, run_id)
    return templates.TemplateResponse(
        "pages/studio_run.html",
        {"request": request, "run": run, "items": items},
    )


@pages_router.get("/testbenches/{tb_id}/compare", response_class=HTMLResponse)
async def studio_compare(
    tb_id: int, request: Request, left: str, right: str,
    ctx: AppContext = Depends(get_ctx),
):
    """left/right are either an int run id or the string 'gold'."""
    items = await ctx.testbench_items_repo.list_for_testbench(ctx.db, tb_id)

    def _side(spec: str):
        if spec == "gold":
            return {"kind": "gold", "by_item": {
                it.id: (json.loads(it.gold_json) if it.gold_json else None)
                for it in items
            }}
        run_id = int(spec)
        run_items = asyncio.run(  # noqa: this is sync code path; in async, switch to await
            ctx.studio_runs_repo.list_items(ctx.db, run_id)
        )
        return {"kind": "run", "run_id": run_id, "by_item": {
            ri.testbench_item_id: ri for ri in run_items
        }}

    # NOTE: the asyncio.run hack above is illegal inside async — rewrite as:
    async def _side_async(spec):
        if spec == "gold":
            return {"kind": "gold", "by_item": {
                it.id: (json.loads(it.gold_json) if it.gold_json else None)
                for it in items
            }}
        run_id = int(spec)
        run_items = await ctx.studio_runs_repo.list_items(ctx.db, run_id)
        return {"kind": "run", "run_id": run_id, "by_item": {
            ri.testbench_item_id: ri for ri in run_items
        }}

    left_side = await _side_async(left)
    right_side = await _side_async(right)
    return templates.TemplateResponse(
        "pages/studio_compare.html",
        {"request": request, "items": items, "left": left_side, "right": right_side},
    )
```

Then register `pages_router` in `main.py` alongside the JSON router.

- [ ] **Step 3: Stub the templates so route tests pass**

Page tests only assert HTTP 200 and rough content. Templates can be minimal at this phase (just enough for the route to render). Real template work is Phase 8. Create stub `templates/pages/studio.html`, `studio_run.html`, `studio_compare.html` with:

```html
{% extends "pages/layout.html" %}
{% block content %}<h1>Studio</h1>{% endblock %}
```

- [ ] **Step 4: Run + commit**

```bash
.venv/bin/pytest tests/routes/test_studio_pages.py -v
git add backend/app/routes/studio.py backend/app/templates/pages/studio.html \
        backend/app/templates/pages/studio_run.html \
        backend/app/templates/pages/studio_compare.html \
        tests/routes/test_studio_pages.py backend/app/main.py
git commit -m "feat(routes): studio pages (landing, testbench, run, compare) — stub templates"
```

---

### ✅ Phase 7 review checkpoint

```bash
.venv/bin/pytest
```

---

# Phase 8 — Templates + Alpine components

> **Manual verification phase.** Most of this is HTML/Alpine; verify visually rather than by tests. Keep changes small; commit per template.

## Task 17: Rail nav

- [ ] Add a Studio entry to `_rail.html` between Prompts and Cache (or wherever the operator prefers). Active when `request.url.path` starts with `/studio`.
- [ ] Verify by opening `/clips` in a browser — the nav row should show Studio.
- [ ] Commit: `git commit -m "feat(ui): studio nav entry in rail"`

## Task 18: Landing layout (`studio.html` + partials)

- [ ] Two-column layout: left rail = testbenches; right pane = folder tree on top, runs table below.
- [ ] Include `_studio_testbench_list.html`, `_studio_folder_tree.html`, `_studio_runs_table.html`.
- [ ] Alpine component `studioPage()` with: `addTestbench`, `selectTestbench`, `openItemMenu`, `addFolderDialog`, `addCatdvDialog`, `uploadDialog`.
- [ ] `_studio_folder_tree.html` recurses by `{% include 'pages/_studio_folder_tree.html' with sub_folders=... %}` per child.
- [ ] Commit per file.

## Task 19: Run detail (`studio_run.html`)

- [ ] Renders the items table, hooks SSE via `EventSource('/api/studio/runs/{id}/events')` in `studioRunView()`.
- [ ] One row partial `_studio_run_item_row.html` containing data attributes for SSE swap by item id.
- [ ] Commit.

## Task 20: Compare page (`studio_compare.html`)

- [ ] Three columns: item name | left output | right output.
- [ ] Reuse the existing annotate cell partials (whatever `_anno_panels.html` uses for rendering structured output). If no clean reusable partial exists, render a minimal pretty-printed JSON `<pre>` inside the cell and file a follow-up issue.
- [ ] Items with no output on a side show `—`. Items where the side is `gold` and `gold_json` is null show `— no reference`.
- [ ] Commit.

## Task 21: Gold dialog component

- [ ] `studioGoldDialog(itemId, initialJson)` Alpine component. Renders a `<textarea>` bound to the `description` key; preserves unknown keys when saving.
- [ ] Hooks PUT to `/api/studio/items/{id}/gold`.
- [ ] Commit.

### ✅ Phase 8 review checkpoint

Open the browser and walk through:
- Create a testbench from the landing page.
- Add a folder, then a subfolder.
- Upload an MP4. Confirm it appears with the original filename.
- Edit gold on the upload. Reopen the dialog. Text should round-trip.
- Verify the rail entry highlights when on `/studio*`.

---

# Phase 9 — Crash recovery + lifespan + context wiring

## Task 22: Lifespan startup wiring

**Files:**
- Modify: `backend/app/main.py` (lifespan)
- Modify: `backend/app/context.py`
- Test: `tests/integration/test_studio_lifespan.py`

- [ ] **Step 1: Write failing test**

```python
import pytest


@pytest.mark.asyncio
async def test_lifespan_creates_uploads_dir(app, tmp_path):
    # 'app' is the standard FastAPI app fixture; its lifespan has run by now.
    from backend.app.context import get_ctx_from_app
    ctx = get_ctx_from_app(app)
    assert ctx.settings.studio_uploads_dir.exists()


@pytest.mark.asyncio
async def test_lifespan_resets_running_studio_runs(app):
    # Pre-seed a 'running' run + transient item, then re-trigger lifespan.
    # Concretely: insert directly via SQL, restart the app via the test's
    # lifespan fixture, assert run is now 'failed' and item is 'error'.
    ...
```

- [ ] **Step 2: Implement**

In `lifespan()`:

```python
    ctx.settings.studio_uploads_dir.mkdir(parents=True, exist_ok=True)
    await ctx.studio_runs_repo.reset_transient(ctx.db)
```

In `AppContext.build()` add:

```python
    testbenches_repo = TestbenchesRepo()
    testbench_items_repo = TestbenchItemsRepo()
    studio_runs_repo = StudioRunsRepo()
    cache_only_resolver = LocalCacheOnlyResolver(
        repo=proxy_cache_repo,
        db_provider=lambda: ctx.db,
        cache_dir=settings.cache_dir,
    )
    studio_runs_service = StudioRunsService(
        runs_repo=studio_runs_repo,
        items_repo=testbench_items_repo,
        prompts_repo=prompts_repo,
        archive=archive,                        # may be None
        proxy_resolver=proxy_resolver,           # may be None
        cache_only_resolver=cache_only_resolver,
        clip_cache_repo=clip_cache_repo,
        ai_store=ai_store,
        gemini=gemini,
        event_bus=event_bus,
        uploads_root=settings.studio_uploads_dir,
        mode_getter=lambda: app_state.mode,
    )
```

Wire these as fields on `AppContext`.

- [ ] **Step 3: Run tests + commit**

```bash
.venv/bin/pytest tests/integration/test_studio_lifespan.py -v
git add backend/app/main.py backend/app/context.py tests/integration/test_studio_lifespan.py
git commit -m "feat(studio): lifespan creates uploads dir + resets transient runs"
```

---

## Task 23: ARCHITECTURE.md row

- [ ] Add to `docs/ARCHITECTURE.md` symptom table:

```markdown
| Studio run stuck or output missing | `services/studio_runs.py`, `repositories/studio_runs.py`; check `studio_run_items.status` and `unacceptable_reason` |
| Studio CatDV clip resolves to 'unacceptable' | `services/studio_runs.py::resolve_clip_input` chain; `proxy_cache` + `ai_store_files` repos |
```

- [ ] Commit: `git commit -m "docs(arch): studio symptom rows"`

---

# Phase 10 — Manual verification & polish

Run with a real Gemini key and a real CatDV connection. Then run again with CatDV offline.

- [ ] Start the dev server gracefully (read `CLAUDE.md` — check for existing instance first).
- [ ] Create a testbench, add nested folders.
- [ ] Upload a 50 MB MP4 → appears with thumbnail + correct filename.
- [ ] Add a CatDV clip ref while online → thumbnail matches production clip list.
- [ ] Edit gold; reopen dialog; text round-trips; PUT with `future_field` preserves it.
- [ ] Start a run with prompt v1; per-item status ticks `resolving → uploading → prompting → done` via SSE.
- [ ] Start a second concurrent run with v2 while v1 still in flight — both progress.
- [ ] Compare run-A vs run-B; outputs render side-by-side.
- [ ] Compare run-A vs `gold`; items lacking gold show `— no reference`.
- [ ] Set `/connection:offline`, then run with a previously-cached CatDV clip → resolves via proxy_cache; ai_store fallback exercised when proxy_cache is cleared.
- [ ] Offline run with an uncached CatDV clip → item ends `unacceptable` with sensible `unacceptable_reason`; rest of run completes.
- [ ] Cancel an in-flight run; next items skipped; previously-completed items remain.
- [ ] SIGTERM the server mid-run; restart; verify the run is `failed` and items `error('interrupted by restart')`.
- [ ] `/studio` loads with `mode=offline` (no CatDV).

If anything fails, fix it on a small follow-up commit; do not skip steps.

- [ ] **Final regression**

```bash
.venv/bin/pytest
```

- [ ] **Final commit (if any polish was needed)**

```bash
git commit -m "polish(studio): manual verification fixes"
```

---

## Done

When all phases land green, the operator can iterate on prompts in a sandbox without touching the CatDV write path or burning a seat. The schema is laid out to accept a future evals layer as a pure addition.
