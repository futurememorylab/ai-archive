# Prompt Studio — PR1 (studio shell + run loop) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the first vertical slice of Prompt Studio — a `/studio` page where the user picks a prompt, focuses one clip from a curated folder, and runs that prompt against the clip; structured output is persisted and rendered in the right pane. PR2 (compare/diff) and PR3 (polish) ship in follow-ups under their own plans.

**Architecture:** New tables `studio_folder`, `studio_folder_clip`, `studio_run`, and a nullable `job.kind` column. A studio run creates a `kind='studio'` job; `services/annotator.py` branches on that kind to skip the CatDV-write step and persist structured output into `studio_run` instead. The Studio screen is server-rendered Jinja + Alpine + HTMX, layered on top of the existing player/cache/archive components.

**Tech Stack:** Python 3.13, FastAPI, aiosqlite (SQLite), Jinja2, Alpine.js, HTMX, pytest, ruff, basedpyright.

**Spec:** `docs/specs/2026-05-26-prompt-studio-design.md`

**Design source files** (already extracted to `/tmp/catdv_design/`):
- `project/studio.jsx` — primary screen
- `project/studio-data.js` — data shapes for testbench/versions/runs
- `project/styles.css` — visual tokens (port the relevant rules to `app.css`)

---

## File map

**Create:**

- `backend/migrations/0013_studio.sql` — new tables + `job.kind` column
- `backend/app/models/studio.py` — Pydantic models `StudioFolder`, `StudioFolderClip`, `StudioRun`
- `backend/app/repositories/studio_folders.py` — folder + folder_clip CRUD
- `backend/app/repositories/studio_runs.py` — run creation, lookup, indicator queries
- `backend/app/routes/studio.py` — REST API: `/api/studio/folders*`, `/api/studio/runs*`
- `backend/app/routes/pages/studio.py` — page route + HTMX partial routes for `/studio`
- `backend/app/templates/pages/studio.html` — main studio page
- `backend/app/templates/pages/_studio_header.html` — header (prompt picker, model picker, run button)
- `backend/app/templates/pages/_studio_folder_list.html` — folder tree partial
- `backend/app/templates/pages/_studio_folder.html` — single folder with its clips
- `backend/app/templates/pages/_studio_clip_card.html` — clip card with run-dots
- `backend/app/templates/pages/_studio_archive_picker.html` — modal body (search + results)
- `backend/app/templates/pages/_studio_player.html` — player embed for focused clip
- `backend/app/templates/pages/_studio_prompt_card.html` — cur card (editor + tabs + output)
- `backend/app/templates/pages/_studio_run_output.html` — output panel rendering
- `backend/app/templates/icons/_flask.svg` — Studio nav icon
- `backend/app/static/studio.js` — Alpine components for studio state
- `tests/unit/test_studio_folders_repo.py`
- `tests/unit/test_studio_runs_repo.py`
- `tests/unit/test_annotator_studio_branch.py`
- `tests/integration/test_studio_api.py`
- `tests/integration/test_studio_page.py`

**Modify:**

- `backend/app/context.py` — register `studio_folders_repo`, `studio_runs_repo`
- `backend/app/repositories/jobs.py` — accept optional `kind` on `create_job`; add `get_job_kind` helper
- `backend/app/services/annotator.py` — branch on `job.kind == 'studio'` in `_process_item`
- `backend/app/main.py` — register new routers (`studio` API + page)
- `backend/app/templates/pages/_rail.html` — add Studio nav button
- `backend/app/templates/pages/_prompt_menu.html` — add "Open in Studio" item
- `backend/app/templates/pages/layout.html` — include `studio.js`
- `backend/app/static/app.css` — add studio layout rules (grid, panels, cards)

---

## Task 1: Migration 0013 — studio tables + job.kind column

**Files:**
- Create: `backend/migrations/0013_studio.sql`
- Test: `tests/integration/test_studio_api.py` (migration applies cleanly; covered transitively)

- [ ] **Step 1: Write the migration SQL**

`backend/migrations/0013_studio.sql`:

```sql
-- 0013: Prompt Studio tables. studio_folder/studio_folder_clip hold the
-- iteration workspace (clips picked from the archive, organized into
-- flat folders). studio_run stores one row per studio execution
-- (kept forever; UI shows the latest per version+clip).
-- jobs.kind discriminates the worker path: NULL=annotation (writes to
-- CatDV), 'studio'=studio run (writes only to studio_run, skips CatDV).

CREATE TABLE studio_folder (
  id         INTEGER PRIMARY KEY,
  name       TEXT    NOT NULL UNIQUE,
  created_at TEXT    NOT NULL
);

CREATE TABLE studio_folder_clip (
  folder_id INTEGER NOT NULL REFERENCES studio_folder(id) ON DELETE CASCADE,
  clip_id   INTEGER NOT NULL,
  added_at  TEXT    NOT NULL,
  PRIMARY KEY (folder_id, clip_id)
);

CREATE TABLE studio_run (
  id                INTEGER PRIMARY KEY,
  prompt_version_id INTEGER NOT NULL REFERENCES prompt_versions(id),
  clip_id           INTEGER NOT NULL,
  job_id            INTEGER REFERENCES jobs(id),
  status            TEXT    NOT NULL,
  output_json       TEXT,
  duration_s        REAL,
  tokens_in         INTEGER,
  tokens_out        INTEGER,
  cost_usd          REAL,
  model             TEXT,
  error             TEXT,
  started_at        TEXT,
  finished_at       TEXT
);

CREATE INDEX studio_run_lookup
  ON studio_run(prompt_version_id, clip_id, finished_at DESC);

CREATE INDEX studio_run_by_clip
  ON studio_run(clip_id, status, prompt_version_id);

ALTER TABLE jobs ADD COLUMN kind TEXT;
```

Note: the existing `prompt_versions` table is named `prompt_versions` (plural) and `jobs` is plural — matches the schema in `0009_prompts_and_versions.sql`.

- [ ] **Step 2: Verify the migration applies**

Run: `.venv/bin/python -c "import asyncio; from backend.app.db import open_db; from backend.app.migrations_runner import apply_migrations; from pathlib import Path; \
async def main():
    async with open_db(Path('/tmp/migtest.db')) as conn:
        await apply_migrations(conn, Path('backend/migrations'))
        cur = await conn.execute(\"SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'studio_%'\")
        print(sorted(r[0] for r in await cur.fetchall()))
asyncio.run(main())"`

Expected: `['studio_folder', 'studio_folder_clip', 'studio_run']`. Then `rm /tmp/migtest.db`.

- [ ] **Step 3: Commit**

```bash
git add backend/migrations/0013_studio.sql
git commit -m "feat(studio): migration 0013 — studio_folder, studio_folder_clip, studio_run, jobs.kind"
```

---

## Task 2: Domain models for studio entities

**Files:**
- Create: `backend/app/models/studio.py`
- Test: `tests/unit/test_studio_models.py`

- [ ] **Step 1: Write failing tests**

`tests/unit/test_studio_models.py`:

```python
"""Studio domain models — round-trip + invariants."""

from backend.app.models.studio import StudioFolder, StudioFolderClip, StudioRun


def test_studio_folder_minimal():
    f = StudioFolder(id=1, name="edge_cases", created_at="2026-05-26T10:00:00+00:00")
    assert f.name == "edge_cases"


def test_studio_folder_clip_minimal():
    fc = StudioFolderClip(folder_id=1, clip_id=12041, added_at="2026-05-26T10:00:00+00:00")
    assert fc.clip_id == 12041


def test_studio_run_ok_with_output():
    r = StudioRun(
        id=1, prompt_version_id=10, clip_id=12041, job_id=99,
        status="ok",
        output_json={"scenes": [{"name": "garden", "in_secs": 0, "out_secs": 12.4}]},
        duration_s=7.4, tokens_in=14820, tokens_out=612, cost_usd=0.0218,
        model="gemini-2.5-pro",
        started_at="2026-05-26T10:00:00+00:00",
        finished_at="2026-05-26T10:00:07+00:00",
    )
    assert r.status == "ok"
    assert r.output_json["scenes"][0]["name"] == "garden"


def test_studio_run_pending_no_output():
    r = StudioRun(
        id=1, prompt_version_id=10, clip_id=12041, status="pending",
        started_at="2026-05-26T10:00:00+00:00",
    )
    assert r.output_json is None
    assert r.duration_s is None
```

- [ ] **Step 2: Run the test to confirm it fails**

Run: `.venv/bin/pytest tests/unit/test_studio_models.py -v`
Expected: FAIL with `ModuleNotFoundError: backend.app.models.studio`.

- [ ] **Step 3: Implement the models**

`backend/app/models/studio.py`:

```python
"""Domain models for Prompt Studio.

A StudioFolder is a flat-named bucket of clips picked from the archive.
A StudioFolderClip is one row of the membership table.
A StudioRun is one execution of a prompt version against a clip; one row
per execution. History kept forever; UI shows the latest per
(prompt_version_id, clip_id).
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

StudioRunStatus = Literal["pending", "running", "ok", "error"]


class StudioFolder(BaseModel):
    id: int | None = None
    name: str
    created_at: str

    model_config = ConfigDict(extra="forbid")


class StudioFolderClip(BaseModel):
    folder_id: int
    clip_id: int
    added_at: str

    model_config = ConfigDict(extra="forbid")


class StudioRun(BaseModel):
    id: int | None = None
    prompt_version_id: int
    clip_id: int
    job_id: int | None = None
    status: StudioRunStatus
    output_json: dict[str, Any] | None = None
    duration_s: float | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    cost_usd: float | None = None
    model: str | None = None
    error: str | None = None
    started_at: str | None = None
    finished_at: str | None = None

    model_config = ConfigDict(extra="forbid")
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv/bin/pytest tests/unit/test_studio_models.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/app/models/studio.py tests/unit/test_studio_models.py
git commit -m "feat(studio): domain models StudioFolder, StudioFolderClip, StudioRun"
```

---

## Task 3: StudioFoldersRepo

**Files:**
- Create: `backend/app/repositories/studio_folders.py`
- Test: `tests/unit/test_studio_folders_repo.py`

- [ ] **Step 1: Write failing tests**

`tests/unit/test_studio_folders_repo.py`:

```python
"""StudioFoldersRepo — create/list/rename/delete folders + clip membership."""

import aiosqlite
import pytest
from pathlib import Path

from backend.app.db import open_db
from backend.app.migrations_runner import apply_migrations
from backend.app.repositories.studio_folders import StudioFoldersRepo


@pytest.fixture
async def db(tmp_path: Path):
    db_path = tmp_path / "test.db"
    cm = open_db(db_path)
    conn = await cm.__aenter__()
    await apply_migrations(conn, Path("backend/migrations"))
    yield conn
    await cm.__aexit__(None, None, None)


@pytest.mark.asyncio
async def test_create_and_list_folder(db: aiosqlite.Connection):
    repo = StudioFoldersRepo()
    fid = await repo.create_folder(db, name="edge_cases")
    rows = await repo.list_folders_with_counts(db)
    assert len(rows) == 1
    assert rows[0]["id"] == fid
    assert rows[0]["name"] == "edge_cases"
    assert rows[0]["clip_count"] == 0


@pytest.mark.asyncio
async def test_unique_folder_name(db):
    repo = StudioFoldersRepo()
    await repo.create_folder(db, name="x")
    with pytest.raises(aiosqlite.IntegrityError):
        await repo.create_folder(db, name="x")


@pytest.mark.asyncio
async def test_add_and_list_clips(db):
    repo = StudioFoldersRepo()
    fid = await repo.create_folder(db, name="f1")
    added = await repo.add_clips(db, fid, clip_ids=[12041, 12042, 12041])  # dedupe
    assert added == 2
    clips = await repo.list_clips(db, fid)
    assert sorted(c["clip_id"] for c in clips) == [12041, 12042]


@pytest.mark.asyncio
async def test_remove_clip(db):
    repo = StudioFoldersRepo()
    fid = await repo.create_folder(db, name="f1")
    await repo.add_clips(db, fid, clip_ids=[12041, 12042])
    await repo.remove_clip(db, fid, clip_id=12041)
    clips = await repo.list_clips(db, fid)
    assert [c["clip_id"] for c in clips] == [12042]


@pytest.mark.asyncio
async def test_rename_folder(db):
    repo = StudioFoldersRepo()
    fid = await repo.create_folder(db, name="old")
    await repo.rename_folder(db, fid, name="new")
    rows = await repo.list_folders_with_counts(db)
    assert rows[0]["name"] == "new"


@pytest.mark.asyncio
async def test_delete_folder_cascades_clips(db):
    repo = StudioFoldersRepo()
    fid = await repo.create_folder(db, name="f1")
    await repo.add_clips(db, fid, clip_ids=[12041])
    await repo.delete_folder(db, fid)
    rows = await repo.list_folders_with_counts(db)
    assert rows == []
    cur = await db.execute("SELECT COUNT(*) FROM studio_folder_clip WHERE folder_id = ?", (fid,))
    assert (await cur.fetchone())[0] == 0


@pytest.mark.asyncio
async def test_clip_count_reflects_membership(db):
    repo = StudioFoldersRepo()
    fid = await repo.create_folder(db, name="f1")
    await repo.add_clips(db, fid, clip_ids=[1, 2, 3])
    rows = await repo.list_folders_with_counts(db)
    assert rows[0]["clip_count"] == 3
```

- [ ] **Step 2: Run tests to confirm they fail**

Run: `.venv/bin/pytest tests/unit/test_studio_folders_repo.py -v`
Expected: collection error or 7 failures (`StudioFoldersRepo` does not exist).

- [ ] **Step 3: Implement the repository**

`backend/app/repositories/studio_folders.py`:

```python
"""StudioFoldersRepo — flat folders of archive-picked clips for the studio.

No nested folders, no per-prompt scoping. Folder names are globally unique
(enforced by `studio_folder.name UNIQUE`). Removing a folder cascades to
its clip memberships via ON DELETE CASCADE.
"""

from datetime import UTC, datetime
from typing import Any

import aiosqlite


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class StudioFoldersRepo:
    async def create_folder(self, conn: aiosqlite.Connection, *, name: str) -> int:
        cur = await conn.execute(
            "INSERT INTO studio_folder(name, created_at) VALUES (?, ?)",
            (name, _now_iso()),
        )
        fid = cur.lastrowid
        assert fid is not None
        await conn.commit()
        return fid

    async def rename_folder(
        self, conn: aiosqlite.Connection, folder_id: int, *, name: str
    ) -> None:
        await conn.execute(
            "UPDATE studio_folder SET name = ? WHERE id = ?", (name, folder_id)
        )
        await conn.commit()

    async def delete_folder(self, conn: aiosqlite.Connection, folder_id: int) -> None:
        await conn.execute("DELETE FROM studio_folder WHERE id = ?", (folder_id,))
        await conn.commit()

    async def list_folders_with_counts(
        self, conn: aiosqlite.Connection
    ) -> list[dict[str, Any]]:
        cur = await conn.execute(
            """
            SELECT f.id, f.name, f.created_at,
                   COALESCE(COUNT(fc.clip_id), 0) AS clip_count
            FROM studio_folder f
            LEFT JOIN studio_folder_clip fc ON fc.folder_id = f.id
            GROUP BY f.id
            ORDER BY f.name
            """
        )
        return [
            {"id": r[0], "name": r[1], "created_at": r[2], "clip_count": r[3]}
            for r in await cur.fetchall()
        ]

    async def add_clips(
        self, conn: aiosqlite.Connection, folder_id: int, *, clip_ids: list[int]
    ) -> int:
        """Add clip_ids to folder. Returns count of newly added (dedupes)."""
        now = _now_iso()
        added = 0
        for cid in set(clip_ids):
            cur = await conn.execute(
                "INSERT OR IGNORE INTO studio_folder_clip(folder_id, clip_id, added_at) "
                "VALUES (?, ?, ?)",
                (folder_id, cid, now),
            )
            if cur.rowcount:
                added += 1
        await conn.commit()
        return added

    async def remove_clip(
        self, conn: aiosqlite.Connection, folder_id: int, *, clip_id: int
    ) -> None:
        await conn.execute(
            "DELETE FROM studio_folder_clip WHERE folder_id = ? AND clip_id = ?",
            (folder_id, clip_id),
        )
        await conn.commit()

    async def list_clips(
        self, conn: aiosqlite.Connection, folder_id: int
    ) -> list[dict[str, Any]]:
        cur = await conn.execute(
            "SELECT clip_id, added_at FROM studio_folder_clip "
            "WHERE folder_id = ? ORDER BY added_at DESC",
            (folder_id,),
        )
        return [{"clip_id": r[0], "added_at": r[1]} for r in await cur.fetchall()]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/unit/test_studio_folders_repo.py -v`
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/app/repositories/studio_folders.py tests/unit/test_studio_folders_repo.py
git commit -m "feat(studio): StudioFoldersRepo — folder CRUD + clip membership"
```

---

## Task 4: StudioRunsRepo

**Files:**
- Create: `backend/app/repositories/studio_runs.py`
- Test: `tests/unit/test_studio_runs_repo.py`

- [ ] **Step 1: Write failing tests**

`tests/unit/test_studio_runs_repo.py`:

```python
"""StudioRunsRepo — run creation, completion, latest lookup, version indicator."""

import json
from pathlib import Path

import aiosqlite
import pytest

from backend.app.db import open_db
from backend.app.migrations_runner import apply_migrations
from backend.app.repositories.studio_runs import StudioRunsRepo


@pytest.fixture
async def db(tmp_path: Path):
    cm = open_db(tmp_path / "t.db")
    conn = await cm.__aenter__()
    await apply_migrations(conn, Path("backend/migrations"))
    # seed a prompt + version so FK on studio_run.prompt_version_id is valid
    await conn.execute(
        "INSERT INTO prompts(id, name, archived, created_at, updated_at) "
        "VALUES (1, 'p', 0, '2026-05-26T00:00:00+00:00', '2026-05-26T00:00:00+00:00')"
    )
    await conn.execute(
        "INSERT INTO prompt_versions(id, prompt_id, version_num, state, body, target_map, "
        "output_schema, model, created_at, updated_at) "
        "VALUES (10, 1, 1, 'draft', 'do x', '{}', '{}', 'gemini-2.5-pro', "
        "'2026-05-26T00:00:00+00:00', '2026-05-26T00:00:00+00:00')"
    )
    await conn.execute(
        "INSERT INTO prompt_versions(id, prompt_id, version_num, state, body, target_map, "
        "output_schema, model, created_at, updated_at) "
        "VALUES (11, 1, 2, 'draft', 'do y', '{}', '{}', 'gemini-2.5-pro', "
        "'2026-05-26T00:00:00+00:00', '2026-05-26T00:00:00+00:00')"
    )
    await conn.commit()
    yield conn
    await cm.__aexit__(None, None, None)


@pytest.mark.asyncio
async def test_create_pending_run(db: aiosqlite.Connection):
    repo = StudioRunsRepo()
    rid = await repo.create_pending(db, prompt_version_id=10, clip_id=12041, model="gemini-2.5-pro")
    cur = await db.execute("SELECT status, model, output_json FROM studio_run WHERE id = ?", (rid,))
    row = await cur.fetchone()
    assert row[0] == "pending"
    assert row[1] == "gemini-2.5-pro"
    assert row[2] is None


@pytest.mark.asyncio
async def test_attach_job(db):
    repo = StudioRunsRepo()
    rid = await repo.create_pending(db, prompt_version_id=10, clip_id=12041, model="gemini-2.5-pro")
    await repo.attach_job(db, rid, job_id=99)
    cur = await db.execute("SELECT job_id FROM studio_run WHERE id = ?", (rid,))
    assert (await cur.fetchone())[0] == 99


@pytest.mark.asyncio
async def test_complete_ok_persists_output_and_stats(db):
    repo = StudioRunsRepo()
    rid = await repo.create_pending(db, prompt_version_id=10, clip_id=12041, model="gemini-2.5-pro")
    output = {"scenes": [{"name": "garden", "in_secs": 0, "out_secs": 12.4}]}
    await repo.complete_ok(
        db, rid,
        output_json=output,
        duration_s=7.4, tokens_in=14820, tokens_out=612, cost_usd=0.0218,
    )
    run = await repo.get(db, rid)
    assert run.status == "ok"
    assert run.output_json == output
    assert run.duration_s == 7.4
    assert run.finished_at is not None


@pytest.mark.asyncio
async def test_complete_error_records_message(db):
    repo = StudioRunsRepo()
    rid = await repo.create_pending(db, prompt_version_id=10, clip_id=12041, model="gemini-2.5-pro")
    await repo.complete_error(db, rid, error="rate-limited")
    run = await repo.get(db, rid)
    assert run.status == "error"
    assert run.error == "rate-limited"
    assert run.output_json is None


@pytest.mark.asyncio
async def test_latest_for_pair_returns_most_recent(db):
    repo = StudioRunsRepo()
    r1 = await repo.create_pending(db, prompt_version_id=10, clip_id=12041, model="m")
    await repo.complete_ok(db, r1, output_json={"k": 1}, duration_s=1.0, tokens_in=0, tokens_out=0, cost_usd=0)
    r2 = await repo.create_pending(db, prompt_version_id=10, clip_id=12041, model="m")
    await repo.complete_ok(db, r2, output_json={"k": 2}, duration_s=1.0, tokens_in=0, tokens_out=0, cost_usd=0)
    latest = await repo.latest_for_pair(db, prompt_version_id=10, clip_id=12041)
    assert latest is not None
    assert latest.output_json == {"k": 2}


@pytest.mark.asyncio
async def test_latest_for_pair_none_when_no_runs(db):
    repo = StudioRunsRepo()
    latest = await repo.latest_for_pair(db, prompt_version_id=10, clip_id=99999)
    assert latest is None


@pytest.mark.asyncio
async def test_versions_run_on_clip(db):
    repo = StudioRunsRepo()
    # v10 succeeded on clip; v11 succeeded on clip
    a = await repo.create_pending(db, prompt_version_id=10, clip_id=12041, model="m")
    await repo.complete_ok(db, a, output_json={}, duration_s=1.0, tokens_in=0, tokens_out=0, cost_usd=0)
    b = await repo.create_pending(db, prompt_version_id=11, clip_id=12041, model="m")
    await repo.complete_ok(db, b, output_json={}, duration_s=1.0, tokens_in=0, tokens_out=0, cost_usd=0)
    # v10 also has an errored run on a different clip — should not appear here
    c = await repo.create_pending(db, prompt_version_id=10, clip_id=99, model="m")
    await repo.complete_error(db, c, error="x")
    versions = await repo.versions_run_on_clip(db, clip_id=12041)
    assert sorted(versions) == [10, 11]
```

- [ ] **Step 2: Run tests to confirm they fail**

Run: `.venv/bin/pytest tests/unit/test_studio_runs_repo.py -v`
Expected: collection error (`StudioRunsRepo` does not exist).

- [ ] **Step 3: Implement the repository**

`backend/app/repositories/studio_runs.py`:

```python
"""StudioRunsRepo — persists studio run history and serves UI lookups.

One row per execution; never deleted. UI queries:
  * latest_for_pair(version, clip)        — right-pane output
  * versions_run_on_clip(clip)            — clip-card run-dot indicators
"""

import json
from datetime import UTC, datetime

import aiosqlite

from backend.app.models.studio import StudioRun


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


_RUN_COLS = (
    "id, prompt_version_id, clip_id, job_id, status, output_json, "
    "duration_s, tokens_in, tokens_out, cost_usd, model, error, "
    "started_at, finished_at"
)


def _row_to_run(row) -> StudioRun:
    return StudioRun(
        id=row[0],
        prompt_version_id=row[1],
        clip_id=row[2],
        job_id=row[3],
        status=row[4],
        output_json=json.loads(row[5]) if row[5] else None,
        duration_s=row[6],
        tokens_in=row[7],
        tokens_out=row[8],
        cost_usd=row[9],
        model=row[10],
        error=row[11],
        started_at=row[12],
        finished_at=row[13],
    )


class StudioRunsRepo:
    async def create_pending(
        self,
        conn: aiosqlite.Connection,
        *,
        prompt_version_id: int,
        clip_id: int,
        model: str,
    ) -> int:
        cur = await conn.execute(
            "INSERT INTO studio_run(prompt_version_id, clip_id, status, model, started_at) "
            "VALUES (?, ?, 'pending', ?, ?)",
            (prompt_version_id, clip_id, model, _now_iso()),
        )
        rid = cur.lastrowid
        assert rid is not None
        await conn.commit()
        return rid

    async def attach_job(
        self, conn: aiosqlite.Connection, run_id: int, *, job_id: int
    ) -> None:
        await conn.execute(
            "UPDATE studio_run SET job_id = ? WHERE id = ?", (job_id, run_id)
        )
        await conn.commit()

    async def mark_running(self, conn: aiosqlite.Connection, run_id: int) -> None:
        await conn.execute(
            "UPDATE studio_run SET status = 'running' WHERE id = ?", (run_id,)
        )
        await conn.commit()

    async def complete_ok(
        self,
        conn: aiosqlite.Connection,
        run_id: int,
        *,
        output_json: dict,
        duration_s: float,
        tokens_in: int,
        tokens_out: int,
        cost_usd: float,
    ) -> None:
        await conn.execute(
            "UPDATE studio_run SET status = 'ok', output_json = ?, duration_s = ?, "
            "tokens_in = ?, tokens_out = ?, cost_usd = ?, finished_at = ? "
            "WHERE id = ?",
            (
                json.dumps(output_json),
                duration_s,
                tokens_in,
                tokens_out,
                cost_usd,
                _now_iso(),
                run_id,
            ),
        )
        await conn.commit()

    async def complete_error(
        self, conn: aiosqlite.Connection, run_id: int, *, error: str
    ) -> None:
        await conn.execute(
            "UPDATE studio_run SET status = 'error', error = ?, finished_at = ? "
            "WHERE id = ?",
            (error, _now_iso(), run_id),
        )
        await conn.commit()

    async def get(self, conn: aiosqlite.Connection, run_id: int) -> StudioRun:
        cur = await conn.execute(
            f"SELECT {_RUN_COLS} FROM studio_run WHERE id = ?", (run_id,)
        )
        row = await cur.fetchone()
        if row is None:
            raise LookupError(f"studio_run {run_id} not found")
        return _row_to_run(row)

    async def latest_for_pair(
        self,
        conn: aiosqlite.Connection,
        *,
        prompt_version_id: int,
        clip_id: int,
    ) -> StudioRun | None:
        cur = await conn.execute(
            f"SELECT {_RUN_COLS} FROM studio_run "
            "WHERE prompt_version_id = ? AND clip_id = ? "
            "ORDER BY COALESCE(finished_at, started_at) DESC LIMIT 1",
            (prompt_version_id, clip_id),
        )
        row = await cur.fetchone()
        return _row_to_run(row) if row else None

    async def versions_run_on_clip(
        self, conn: aiosqlite.Connection, *, clip_id: int
    ) -> list[int]:
        """Returns distinct prompt_version_ids that have a successful run on this clip."""
        cur = await conn.execute(
            "SELECT DISTINCT prompt_version_id FROM studio_run "
            "WHERE clip_id = ? AND status = 'ok'",
            (clip_id,),
        )
        return [r[0] for r in await cur.fetchall()]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/unit/test_studio_runs_repo.py -v`
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/app/repositories/studio_runs.py tests/unit/test_studio_runs_repo.py
git commit -m "feat(studio): StudioRunsRepo — persist runs + latest/indicator queries"
```

---

## Task 5: Extend JobsRepo with `kind` support

**Files:**
- Modify: `backend/app/repositories/jobs.py`
- Test: `tests/unit/test_jobs_repo_kind.py` (new)

- [ ] **Step 1: Write failing tests**

`tests/unit/test_jobs_repo_kind.py`:

```python
"""JobsRepo — `kind` column round-trips and defaults to NULL for back-compat."""

from pathlib import Path

import pytest

from backend.app.db import open_db
from backend.app.migrations_runner import apply_migrations
from backend.app.repositories.jobs import JobsRepo


@pytest.fixture
async def db(tmp_path: Path):
    cm = open_db(tmp_path / "t.db")
    conn = await cm.__aenter__()
    await apply_migrations(conn, Path("backend/migrations"))
    await conn.execute(
        "INSERT INTO prompts(id, name, archived, created_at, updated_at) "
        "VALUES (1, 'p', 0, '2026-05-26T00:00:00+00:00', '2026-05-26T00:00:00+00:00')"
    )
    await conn.execute(
        "INSERT INTO prompt_versions(id, prompt_id, version_num, state, body, target_map, "
        "output_schema, model, created_at, updated_at) "
        "VALUES (10, 1, 1, 'draft', 'b', '{}', '{}', 'm', "
        "'2026-05-26T00:00:00+00:00', '2026-05-26T00:00:00+00:00')"
    )
    await conn.commit()
    yield conn
    await cm.__aexit__(None, None, None)


@pytest.mark.asyncio
async def test_create_job_default_kind_is_null(db):
    repo = JobsRepo()
    jid = await repo.create_job(db, prompt_version_id=10, clip_ids=[42])
    kind = await repo.get_job_kind(db, jid)
    assert kind is None


@pytest.mark.asyncio
async def test_create_job_with_studio_kind(db):
    repo = JobsRepo()
    jid = await repo.create_job(db, prompt_version_id=10, clip_ids=[42], kind="studio")
    kind = await repo.get_job_kind(db, jid)
    assert kind == "studio"
```

- [ ] **Step 2: Run tests to confirm they fail**

Run: `.venv/bin/pytest tests/unit/test_jobs_repo_kind.py -v`
Expected: FAIL — `JobsRepo.create_job() got an unexpected keyword argument 'kind'`.

- [ ] **Step 3: Modify `JobsRepo`**

Edit `backend/app/repositories/jobs.py`:

In `create_job`, add a `kind: str | None = None` keyword arg and persist it. Add a `get_job_kind` helper.

```python
    async def create_job(
        self,
        conn: aiosqlite.Connection,
        *,
        prompt_version_id: int,
        clip_ids: list[int],
        kind: str | None = None,
    ) -> int:
        cur = await conn.execute(
            """
            INSERT INTO jobs (prompt_version_id, status, created_at, total_clips, kind)
            VALUES (?, 'pending', ?, ?, ?)
            """,
            (prompt_version_id, _now_iso(), len(clip_ids), kind),
        )
        job_id = cur.lastrowid
        assert job_id is not None
        for clip_id in clip_ids:
            await conn.execute(
                "INSERT INTO job_items (job_id, catdv_clip_id, status) VALUES (?, ?, 'pending')",
                (job_id, clip_id),
            )
        await conn.commit()
        return job_id

    async def get_job_kind(self, conn: aiosqlite.Connection, job_id: int) -> str | None:
        cur = await conn.execute("SELECT kind FROM jobs WHERE id = ?", (job_id,))
        row = await cur.fetchone()
        if row is None:
            raise LookupError(f"job {job_id} not found")
        return row[0]
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest tests/unit/test_jobs_repo_kind.py tests/unit/test_studio_folders_repo.py tests/unit/test_studio_runs_repo.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add backend/app/repositories/jobs.py tests/unit/test_jobs_repo_kind.py
git commit -m "feat(studio): JobsRepo accepts kind on create_job; add get_job_kind"
```

---

## Task 6: Wire studio repos into AppContext

**Files:**
- Modify: `backend/app/context.py`

- [ ] **Step 1: Add imports + dataclass fields**

In `backend/app/context.py`, add to the import block (alphabetized with the other repo imports):

```python
from backend.app.repositories.studio_folders import StudioFoldersRepo
from backend.app.repositories.studio_runs import StudioRunsRepo
```

In the `AppContext` dataclass, add two new fields next to the other repo fields (after `prefetch_queue_repo`):

```python
    studio_folders_repo: StudioFoldersRepo = field(default_factory=StudioFoldersRepo)
    studio_runs_repo: StudioRunsRepo = field(default_factory=StudioRunsRepo)
```

- [ ] **Step 2: Verify boot still works**

Run: `.venv/bin/pytest tests/unit/ -k context -v`
Expected: existing context tests still pass.

If no dedicated context tests exist, run a smoke check:

```bash
.venv/bin/python -c "
import asyncio
from backend.app.context import AppContext
from backend.app.settings import Settings
async def main():
    ctx = await AppContext.build(Settings(), init_external=False)
    assert ctx.studio_folders_repo is not None
    assert ctx.studio_runs_repo is not None
    await ctx.aclose()
asyncio.run(main())
"
```

Expected: no output (clean exit).

- [ ] **Step 3: Commit**

```bash
git add backend/app/context.py
git commit -m "feat(studio): wire studio_folders_repo + studio_runs_repo into AppContext"
```

---

## Task 7: Annotator service — kind='studio' branch

**Files:**
- Modify: `backend/app/services/annotator.py`
- Test: `tests/unit/test_annotator_studio_branch.py` (new)

- [ ] **Step 1: Write failing test**

`tests/unit/test_annotator_studio_branch.py`:

```python
"""Annotator service — studio path persists to studio_run and skips CatDV-write.

We assert that for a job with kind='studio':
  * No annotation row is inserted (annotations_repo.insert not called).
  * The matching studio_run row transitions to status='ok' with output_json.
  * review_items are not inserted (target_map.expand not called).
"""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.app.db import open_db
from backend.app.migrations_runner import apply_migrations
from backend.app.repositories.annotations import AnnotationsRepo
from backend.app.repositories.jobs import JobsRepo
from backend.app.repositories.prompts import PromptsRepo
from backend.app.repositories.review_items import ReviewItemsRepo
from backend.app.repositories.studio_runs import StudioRunsRepo
from backend.app.services.annotator import run_job
from backend.app.services.events import EventBus


@pytest.fixture
async def db(tmp_path: Path):
    cm = open_db(tmp_path / "t.db")
    conn = await cm.__aenter__()
    await apply_migrations(conn, Path("backend/migrations"))
    yield conn
    await cm.__aexit__(None, None, None)


@pytest.mark.asyncio
async def test_studio_kind_persists_run_skips_catdv_write(db):
    # Seed a prompt + draft version
    prompts = PromptsRepo()
    pid, vid = await prompts.create_with_initial_version(
        db,
        name="p",
        description=None,
        body="do x",
        target_map={"scenes": {"kind": "markers"}},
        output_schema={"type": "object"},
        model="gemini-2.5-pro",
    )

    # Create a studio_run row first, then a kind='studio' job linked to it
    runs = StudioRunsRepo()
    run_id = await runs.create_pending(
        db, prompt_version_id=vid, clip_id=42, model="gemini-2.5-pro"
    )
    jobs = JobsRepo()
    job_id = await jobs.create_job(
        db, prompt_version_id=vid, clip_ids=[42], kind="studio"
    )
    await runs.attach_job(db, run_id, job_id=job_id)

    # Fakes for the externals.
    archive = MagicMock()
    archive.get_clip = AsyncMock(return_value=MagicMock(
        provider_data={"name": "clip-42"},
        duration_secs=10.0,
    ))
    proxy = MagicMock()
    proxy.path_for_clip_id = AsyncMock(return_value=Path("/tmp/clip-42.mp4"))
    ai_store = MagicMock()
    ai_store.ensure_uploaded = AsyncMock(return_value="upload-ref")
    ai_store.reference_for_gemini = AsyncMock(return_value={"uri": "gs://x"})
    gemini = MagicMock()
    gemini.annotate = MagicMock(return_value={
        "text": json.dumps({"scenes": [{"name": "s1", "in_secs": 0, "out_secs": 5}]}),
        "raw": {"usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 50}},
    })

    annotations = AnnotationsRepo()
    review_items = ReviewItemsRepo()
    annotations.insert = AsyncMock()  # type: ignore[method-assign]
    review_items.bulk_insert = AsyncMock()  # type: ignore[method-assign]

    bus = EventBus()

    await run_job(
        db=db, job_id=job_id,
        archive=archive, proxy_resolver=proxy, ai_store=ai_store, gemini=gemini,
        event_bus=bus,
        annotations_repo=annotations, review_items_repo=review_items,
        jobs_repo=jobs, prompts_repo=prompts,
        studio_runs_repo=runs,
    )

    # Assertion: CatDV-side writes were NOT called
    annotations.insert.assert_not_called()
    review_items.bulk_insert.assert_not_called()

    # studio_run completed ok with output
    run = await runs.get(db, run_id)
    assert run.status == "ok"
    assert run.output_json == {"scenes": [{"name": "s1", "in_secs": 0, "out_secs": 5}]}
```

- [ ] **Step 2: Run test to confirm it fails**

Run: `.venv/bin/pytest tests/unit/test_annotator_studio_branch.py -v`
Expected: FAIL — `run_job() got an unexpected keyword argument 'studio_runs_repo'`.

- [ ] **Step 3: Modify the annotator service**

Edit `backend/app/services/annotator.py`:

1. Add `studio_runs_repo` parameter to `run_job` and thread it through to `_process_item`.
2. In `_process_item`, fetch `job.kind` once; branch the write step.

Updated `run_job` signature and body:

```python
async def run_job(
    *,
    db: aiosqlite.Connection,
    job_id: int,
    archive,
    proxy_resolver,
    ai_store: AIInputStore,
    gemini,
    event_bus: EventBus,
    annotations_repo: AnnotationsRepo,
    review_items_repo: ReviewItemsRepo,
    jobs_repo: JobsRepo,
    prompts_repo: PromptsRepo,
    studio_runs_repo,  # StudioRunsRepo — kept untyped here to avoid a circular import
) -> None:
    job = await jobs_repo.get_job(db, job_id)
    kind = await jobs_repo.get_job_kind(db, job_id)
    version = await prompts_repo.get_version(db, job.prompt_version_id)
    await jobs_repo.update_status(db, job_id, "running")

    items = await jobs_repo.list_items(db, job_id)
    topic = f"job:{job_id}"

    for item in items:
        live = await jobs_repo.get_job(db, job_id)
        if live.status == "cancelled":
            log.info("job %s cancelled mid-run; stopping", job_id, extra={"job_id": job_id})
            break

        if item.status not in ("pending", "error"):
            continue

        try:
            await _process_item(
                db=db,
                item=item,
                version=version,
                kind=kind,
                archive=archive,
                proxy_resolver=proxy_resolver,
                ai_store=ai_store,
                gemini=gemini,
                annotations_repo=annotations_repo,
                review_items_repo=review_items_repo,
                jobs_repo=jobs_repo,
                studio_runs_repo=studio_runs_repo,
                event_bus=event_bus,
                topic=topic,
            )
        except Exception as exc:  # noqa: BLE001
            log.exception(
                "job %s clip %s failed",
                job_id,
                item.catdv_clip_id,
                extra={"job_id": job_id, "clip_id": item.catdv_clip_id},
            )
            await jobs_repo.update_item_status(db, item.id, "error", error=str(exc))
            await event_bus.publish(
                topic, {"item_id": item.id, "status": "error", "error": str(exc)}
            )

    refreshed = await jobs_repo.list_items(db, job_id)
    final_status = "completed"
    if any(it.status == "error" for it in refreshed):
        final_status = "failed"
    if (await jobs_repo.get_job(db, job_id)).status == "cancelled":
        final_status = "cancelled"
    await jobs_repo.update_status(db, job_id, final_status)
```

Updated `_process_item` to branch:

```python
async def _process_item(
    *,
    db, item, version, kind,
    archive, proxy_resolver, ai_store, gemini,
    annotations_repo, review_items_repo,
    jobs_repo, studio_runs_repo,
    event_bus, topic,
) -> None:
    import time

    await jobs_repo.update_item_status(db, item.id, "resolving")
    await event_bus.publish(topic, {"item_id": item.id, "status": "resolving"})
    local_path: Path = await proxy_resolver.path_for_clip_id(item.catdv_clip_id)

    await jobs_repo.update_item_status(db, item.id, "uploading")
    await event_bus.publish(topic, {"item_id": item.id, "status": "uploading"})
    mime = mimetypes.guess_type(str(local_path))[0] or "video/quicktime"
    clip_key = ("catdv", str(item.catdv_clip_id))
    upload = await ai_store.ensure_uploaded(clip_key, local_path, mime)
    file_ref = await ai_store.reference_for_gemini(upload)

    canonical = await archive.get_clip(str(item.catdv_clip_id))
    clip_snapshot: dict[str, Any] = dict(canonical.provider_data)
    duration_secs = float(canonical.duration_secs or 0.0)

    await jobs_repo.update_item_status(db, item.id, "prompting")
    await event_bus.publish(topic, {"item_id": item.id, "status": "prompting"})
    rendered_body = _render_prompt(version.body, duration_secs=duration_secs)
    t0 = time.monotonic()
    result = gemini.annotate(
        file_ref=file_ref,
        prompt=rendered_body,
        schema=version.output_schema,
        model=version.model,
    )
    elapsed_s = time.monotonic() - t0

    structured: dict[str, Any] | None
    try:
        structured = json.loads(result["text"]) if result.get("text") else None
    except json.JSONDecodeError:
        structured = None

    if kind == "studio":
        await _finalize_studio(
            db, item, version, structured, result, elapsed_s,
            studio_runs_repo, jobs_repo, event_bus, topic,
        )
    else:
        await _finalize_annotation(
            db, item, version, structured, result, rendered_body,
            clip_snapshot, duration_secs,
            annotations_repo, review_items_repo, jobs_repo,
            event_bus, topic,
        )


async def _finalize_studio(
    db, item, version, structured, result, elapsed_s,
    studio_runs_repo, jobs_repo, event_bus, topic,
) -> None:
    """Studio path: persist to studio_run, skip annotations + review_items."""
    # Locate the studio_run linked to this job + clip.
    cur = await db.execute(
        "SELECT id FROM studio_run WHERE job_id = ? AND clip_id = ? "
        "ORDER BY id DESC LIMIT 1",
        (item.job_id, item.catdv_clip_id),
    )
    row = await cur.fetchone()
    if row is None:
        # Should not happen — the studio route always creates the run row
        # before the job. Log and bail to a non-fatal error.
        await jobs_repo.update_item_status(db, item.id, "error", error="studio_run not found")
        return
    run_id = row[0]

    usage = (result.get("raw") or {}).get("usageMetadata") or {}
    tokens_in = int(usage.get("promptTokenCount", 0) or 0)
    tokens_out = int(usage.get("candidatesTokenCount", 0) or 0)
    cost_usd = 0.0  # cost calc lives elsewhere (or stays 0 if not computed)

    if structured is None:
        await studio_runs_repo.complete_error(db, run_id, error="model returned non-JSON or empty")
        await jobs_repo.update_item_status(db, item.id, "error", error="non-JSON output")
        await event_bus.publish(
            topic, {"item_id": item.id, "status": "error", "error": "non-JSON output"}
        )
        return

    await studio_runs_repo.complete_ok(
        db, run_id,
        output_json=structured,
        duration_s=elapsed_s,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cost_usd=cost_usd,
    )
    await jobs_repo.update_item_status(db, item.id, "review_ready")
    await event_bus.publish(
        topic, {"item_id": item.id, "status": "review_ready", "studio_run_id": run_id}
    )


async def _finalize_annotation(
    db, item, version, structured, result, rendered_body,
    clip_snapshot, duration_secs,
    annotations_repo, review_items_repo, jobs_repo,
    event_bus, topic,
) -> None:
    """Original annotation path: write to annotations + review_items."""
    annotation_id = await annotations_repo.insert(
        db,
        Annotation(
            catdv_clip_id=item.catdv_clip_id,
            catdv_clip_name=clip_snapshot.get("name", ""),
            prompt_version_id=version.id,
            job_id=item.job_id,
            model=version.model,
            prompt_used=rendered_body,
            raw_response=result.get("raw", {}),
            structured_output=structured,
            clip_snapshot=clip_snapshot,
        ),
    )
    await jobs_repo.attach_annotation(db, item.id, annotation_id)

    if structured:
        review = expand(
            structured,
            version.target_map,
            annotation_id=annotation_id,
            catdv_clip_id=item.catdv_clip_id,
            clip_duration_secs=duration_secs or None,
        )
        if review:
            await review_items_repo.bulk_insert(db, review)

    await jobs_repo.update_item_status(db, item.id, "review_ready")
    await event_bus.publish(
        topic, {"item_id": item.id, "status": "review_ready", "annotation_id": annotation_id}
    )
```

- [ ] **Step 4: Update the jobs route caller**

Edit `backend/app/routes/jobs.py` — `_run_in_bg` must pass the new `studio_runs_repo` arg:

```python
async def _run_in_bg(ctx, job_id: int) -> None:
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
        )
    finally:
        ctx._running_jobs.pop(job_id, None)
```

- [ ] **Step 5: Run tests**

Run: `.venv/bin/pytest tests/unit/test_annotator_studio_branch.py -v`
Expected: 1 passed.

Run the existing annotator tests to confirm no regression:
Run: `.venv/bin/pytest tests/ -k "annotator or job" -v`
Expected: all pre-existing tests pass.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/annotator.py backend/app/routes/jobs.py tests/unit/test_annotator_studio_branch.py
git commit -m "feat(studio): annotator branches on job.kind='studio' to persist studio_run + skip CatDV write"
```

---

## Task 8: REST routes — /api/studio/folders

**Files:**
- Create: `backend/app/routes/studio.py`
- Modify: `backend/app/main.py` (register router)
- Test: `tests/integration/test_studio_api.py` (new)

- [ ] **Step 1: Write failing tests**

`tests/integration/test_studio_api.py`:

```python
"""Integration tests for /api/studio routes."""

import pytest
from httpx import ASGITransport, AsyncClient

from backend.app.main import create_app
from backend.app.settings import Settings


@pytest.fixture
async def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    settings = Settings()
    app = create_app(settings=settings, init_external=False)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c


@pytest.mark.asyncio
async def test_create_list_rename_delete_folder(client):
    r = await client.post("/api/studio/folders", json={"name": "edge_cases"})
    assert r.status_code == 201
    fid = r.json()["id"]

    r = await client.get("/api/studio/folders")
    assert r.status_code == 200
    folders = r.json()
    assert len(folders) == 1
    assert folders[0]["name"] == "edge_cases"
    assert folders[0]["clip_count"] == 0

    r = await client.patch(f"/api/studio/folders/{fid}", json={"name": "rare"})
    assert r.status_code == 200
    r = await client.get("/api/studio/folders")
    assert r.json()[0]["name"] == "rare"

    r = await client.delete(f"/api/studio/folders/{fid}")
    assert r.status_code == 204
    r = await client.get("/api/studio/folders")
    assert r.json() == []


@pytest.mark.asyncio
async def test_duplicate_folder_name_rejected(client):
    await client.post("/api/studio/folders", json={"name": "x"})
    r = await client.post("/api/studio/folders", json={"name": "x"})
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_add_list_remove_clips(client):
    r = await client.post("/api/studio/folders", json={"name": "f"})
    fid = r.json()["id"]

    r = await client.post(f"/api/studio/folders/{fid}/clips", json={"clip_ids": [12041, 12042]})
    assert r.status_code == 200
    assert r.json()["added"] == 2

    r = await client.get(f"/api/studio/folders/{fid}/clips")
    assert r.status_code == 200
    clips = r.json()
    assert sorted(c["clip_id"] for c in clips) == [12041, 12042]

    r = await client.delete(f"/api/studio/folders/{fid}/clips/12041")
    assert r.status_code == 204

    r = await client.get(f"/api/studio/folders/{fid}/clips")
    assert [c["clip_id"] for c in r.json()] == [12042]
```

- [ ] **Step 2: Implement the routes**

`backend/app/routes/studio.py`:

```python
"""REST API for Prompt Studio — folders, folder_clips, runs.

All under /api/studio. See docs/specs/2026-05-26-prompt-studio-design.md.
"""

import asyncio

import aiosqlite
from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import Response
from pydantic import BaseModel

from backend.app.deps import get_ctx
from backend.app.services.annotator import run_job

router = APIRouter(prefix="/api/studio", tags=["studio"])


# ── request models ──────────────────────────────────────────────────────────


class FolderCreate(BaseModel):
    name: str


class FolderPatch(BaseModel):
    name: str


class AddClips(BaseModel):
    clip_ids: list[int]


class RunCreate(BaseModel):
    prompt_version_id: int
    clip_id: int
    model: str | None = None


# ── folders ─────────────────────────────────────────────────────────────────


@router.get("/folders")
async def list_folders(request: Request):
    ctx = get_ctx(request)
    return await ctx.studio_folders_repo.list_folders_with_counts(ctx.db)


@router.post("/folders", status_code=status.HTTP_201_CREATED)
async def create_folder(request: Request, body: FolderCreate):
    ctx = get_ctx(request)
    try:
        fid = await ctx.studio_folders_repo.create_folder(ctx.db, name=body.name)
    except aiosqlite.IntegrityError as exc:
        raise HTTPException(409, f"folder name {body.name!r} already exists") from exc
    return {"id": fid}


@router.patch("/folders/{folder_id}")
async def rename_folder(request: Request, folder_id: int, body: FolderPatch):
    ctx = get_ctx(request)
    try:
        await ctx.studio_folders_repo.rename_folder(ctx.db, folder_id, name=body.name)
    except aiosqlite.IntegrityError as exc:
        raise HTTPException(409, f"folder name {body.name!r} already exists") from exc
    return {"id": folder_id, "name": body.name}


@router.delete("/folders/{folder_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_folder(request: Request, folder_id: int):
    ctx = get_ctx(request)
    await ctx.studio_folders_repo.delete_folder(ctx.db, folder_id)
    return Response(status_code=204)


# ── folder clips ────────────────────────────────────────────────────────────


@router.get("/folders/{folder_id}/clips")
async def list_folder_clips(request: Request, folder_id: int):
    ctx = get_ctx(request)
    return await ctx.studio_folders_repo.list_clips(ctx.db, folder_id)


@router.post("/folders/{folder_id}/clips")
async def add_folder_clips(request: Request, folder_id: int, body: AddClips):
    ctx = get_ctx(request)
    added = await ctx.studio_folders_repo.add_clips(
        ctx.db, folder_id, clip_ids=body.clip_ids
    )
    return {"added": added}


@router.delete(
    "/folders/{folder_id}/clips/{clip_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def remove_folder_clip(request: Request, folder_id: int, clip_id: int):
    ctx = get_ctx(request)
    await ctx.studio_folders_repo.remove_clip(ctx.db, folder_id, clip_id=clip_id)
    return Response(status_code=204)


# ── runs ────────────────────────────────────────────────────────────────────


@router.post("/runs", status_code=status.HTTP_201_CREATED)
async def create_run(request: Request, body: RunCreate):
    ctx = get_ctx(request)
    # Resolve effective model: override > prompt_version.model
    try:
        version = await ctx.prompts_repo.get_version(ctx.db, body.prompt_version_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    model = body.model or version.model

    run_id = await ctx.studio_runs_repo.create_pending(
        ctx.db,
        prompt_version_id=body.prompt_version_id,
        clip_id=body.clip_id,
        model=model,
    )
    job_id = await ctx.jobs_repo.create_job(
        ctx.db,
        prompt_version_id=body.prompt_version_id,
        clip_ids=[body.clip_id],
        kind="studio",
    )
    await ctx.studio_runs_repo.attach_job(ctx.db, run_id, job_id=job_id)

    if ctx.archive and ctx.ai_store and ctx.gemini and ctx.proxy_resolver:
        task = asyncio.create_task(_run_in_bg(ctx, job_id))
        ctx._running_jobs[job_id] = task

    return {"run_id": run_id, "job_id": job_id}


async def _run_in_bg(ctx, job_id: int) -> None:
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
        )
    finally:
        ctx._running_jobs.pop(job_id, None)


@router.get("/runs/{run_id}")
async def get_run(request: Request, run_id: int):
    ctx = get_ctx(request)
    try:
        run = await ctx.studio_runs_repo.get(ctx.db, run_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    return run.model_dump()


@router.get("/runs")
async def latest_run(
    request: Request,
    prompt_version_id: int,
    clip_id: int,
    latest: int = 1,
):
    """Latest run for (version, clip). `latest=1` is the only supported mode in v1."""
    if latest != 1:
        raise HTTPException(400, "only latest=1 is supported in v1")
    ctx = get_ctx(request)
    run = await ctx.studio_runs_repo.latest_for_pair(
        ctx.db, prompt_version_id=prompt_version_id, clip_id=clip_id
    )
    return run.model_dump() if run else None
```

- [ ] **Step 3: Register the router in `main.py`**

Find the line where existing API routers are included (`app.include_router(prompts.router)` etc.) and add:

```python
from backend.app.routes import studio
# ...
app.include_router(studio.router)
```

- [ ] **Step 4: Run integration tests**

Run: `.venv/bin/pytest tests/integration/test_studio_api.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/app/routes/studio.py backend/app/main.py tests/integration/test_studio_api.py
git commit -m "feat(studio): /api/studio/folders + /clips + /runs endpoints"
```

---

## Task 9: Add `/api/studio/runs` flow test

**Files:**
- Modify: `tests/integration/test_studio_api.py` (append a new test)

- [ ] **Step 1: Add a run-creation test**

Append to `tests/integration/test_studio_api.py`:

```python
@pytest.mark.asyncio
async def test_create_run_persists_pending_studio_run_and_job(client):
    # Seed a prompt + version via the prompts API so we have a real prompt_version_id
    r = await client.post(
        "/api/prompts",
        json={
            "name": "p",
            "body": "do x",
            "target_map": {},
            "output_schema": {},
            "model": "gemini-2.5-pro",
        },
    )
    assert r.status_code == 201
    pid = r.json()["id"]
    # Fetch its initial version id
    r = await client.get(f"/api/prompts/{pid}")
    vid = r.json()["latest_version_id"]

    # Create a studio run (no externals wired in test env, so it will not
    # actually execute — but the row + job should exist with pending status).
    r = await client.post(
        "/api/studio/runs",
        json={"prompt_version_id": vid, "clip_id": 42},
    )
    assert r.status_code == 201
    body = r.json()
    assert "run_id" in body and "job_id" in body

    r = await client.get(f"/api/studio/runs/{body['run_id']}")
    assert r.status_code == 200
    run = r.json()
    assert run["status"] == "pending"
    assert run["model"] == "gemini-2.5-pro"

    # Latest lookup returns the same run
    r = await client.get(
        "/api/studio/runs", params={"prompt_version_id": vid, "clip_id": 42, "latest": 1}
    )
    assert r.status_code == 200
    assert r.json()["id"] == body["run_id"]
```

- [ ] **Step 2: Run the test**

Run: `.venv/bin/pytest tests/integration/test_studio_api.py::test_create_run_persists_pending_studio_run_and_job -v`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_studio_api.py
git commit -m "test(studio): integration coverage for POST /api/studio/runs"
```

---

## Task 10: Page route + studio.html scaffold

**Files:**
- Create: `backend/app/routes/pages/studio.py`
- Create: `backend/app/templates/pages/studio.html`
- Create: `backend/app/templates/icons/_flask.svg`
- Modify: `backend/app/main.py` (register page router)
- Modify: `backend/app/templates/pages/_rail.html` (add nav button)
- Test: `tests/integration/test_studio_page.py` (new)

- [ ] **Step 1: Write failing test**

`tests/integration/test_studio_page.py`:

```python
"""Studio page renders and includes the expected scaffolding."""

import pytest
from httpx import ASGITransport, AsyncClient

from backend.app.main import create_app
from backend.app.settings import Settings


@pytest.fixture
async def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    settings = Settings()
    app = create_app(settings=settings, init_external=False)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c


@pytest.mark.asyncio
async def test_studio_page_renders(client):
    r = await client.get("/studio")
    assert r.status_code == 200
    html = r.text
    # Page-level scaffolding
    assert "studio-page" in html
    assert "studio-hdr" in html
    assert "studio-body" in html


@pytest.mark.asyncio
async def test_studio_rail_button_present(client):
    r = await client.get("/")
    assert r.status_code == 200
    assert 'href="/studio"' in r.text


@pytest.mark.asyncio
async def test_studio_page_with_prompt_id_renders(client):
    # Even with an unknown prompt_id, the page must render (and show an empty
    # header — the prompt picker is the recovery affordance).
    r = await client.get("/studio?prompt_id=999")
    assert r.status_code == 200
```

- [ ] **Step 2: Create the icon**

`backend/app/templates/icons/_flask.svg`:

```html
<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
  <path d="M9 3h6" />
  <path d="M10 3v6L4 20a1 1 0 0 0 .9 1.5h14.2A1 1 0 0 0 20 20L14 9V3" />
  <path d="M7 14h10" />
</svg>
```

- [ ] **Step 3: Add the rail button**

Edit `backend/app/templates/pages/_rail.html`. Add this line after the existing Prompts link:

```html
<a class="rail-btn{% if _active == 'studio' %} active{% endif %}"
   href="/studio" title="Studio">{% include "icons/_flask.svg" %}</a>
```

- [ ] **Step 4: Create the page route**

`backend/app/routes/pages/studio.py`:

```python
"""Studio page + HTMX partial routes."""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from backend.app.deps import get_ctx
from backend.app.routes.pages.templates import templates

router = APIRouter()


@router.get("/studio", response_class=HTMLResponse)
async def studio_page(request: Request, prompt_id: int | None = None):
    ctx = get_ctx(request)
    prompts = await ctx.prompts_repo.list_active(ctx.db)
    folders = await ctx.studio_folders_repo.list_folders_with_counts(ctx.db)

    selected_prompt = None
    versions: list = []
    if prompt_id is not None:
        try:
            selected_prompt, versions = await ctx.prompts_repo.get_with_versions(
                ctx.db, prompt_id
            )
        except LookupError:
            selected_prompt = None
            versions = []
    elif prompts:
        # Default to the first active prompt if none specified
        selected_prompt, versions = await ctx.prompts_repo.get_with_versions(
            ctx.db, prompts[0].id
        )

    # Pick the active version: the only draft if exists, else the latest
    active_version = None
    if versions:
        active_version = next((v for v in versions if v.state == "draft"), versions[0])

    return templates.TemplateResponse(
        "pages/studio.html",
        {
            "request": request,
            "prompts": prompts,
            "selected_prompt": selected_prompt,
            "versions": versions,
            "active_version": active_version,
            "folders": folders,
        },
    )
```

- [ ] **Step 5: Create the studio.html scaffold**

`backend/app/templates/pages/studio.html`:

```html
{% extends "pages/layout.html" %}
{% block title %}Studio · CatDV Annotator{% endblock %}
{% block rail_active %}studio{% endblock %}

{% block body %}
<div class="studio-page" data-studio
     data-prompt-id="{{ selected_prompt.id if selected_prompt else '' }}"
     data-active-version-id="{{ active_version.id if active_version else '' }}">

  {% include "pages/_studio_header.html" %}

  <div class="studio-body no-player">
    <aside class="studio-videos">
      {% include "pages/_studio_folder_list.html" %}
    </aside>
    <section class="studio-right">
      <div class="studio-player-slot" data-studio-player-slot></div>
      <div class="studio-compare">
        {% include "pages/_studio_prompt_card.html" %}
      </div>
    </section>
  </div>
</div>
{% endblock %}
```

- [ ] **Step 6: Add empty placeholder partials so the page renders**

Create stub files (they will be filled in later tasks):

`backend/app/templates/pages/_studio_header.html`:

```html
<header class="studio-hdr"><div class="muted">Studio header (filled in Task 12)</div></header>
```

`backend/app/templates/pages/_studio_folder_list.html`:

```html
<div class="studio-folders muted">Folder list (filled in Task 13)</div>
```

`backend/app/templates/pages/_studio_prompt_card.html`:

```html
<div class="studio-prompt-card muted">Prompt card (filled in Task 15)</div>
```

- [ ] **Step 7: Register the page router via the aggregator**

Page routers are collected in `backend/app/routes/pages/__init__.py` as the
`page_routers` list. Edit that file:

```python
from backend.app.routes.pages.clips import router as clips_router
from backend.app.routes.pages.prompts import router as prompts_router
from backend.app.routes.pages.studio import router as studio_router

page_routers = [clips_router, prompts_router, studio_router]

__all__ = ["page_routers", "clips_router", "prompts_router", "studio_router"]
```

`main.py` already iterates `page_routers` and calls `include_router` for each — no change there.

- [ ] **Step 8: Run the tests**

Run: `.venv/bin/pytest tests/integration/test_studio_page.py -v`
Expected: 3 passed.

- [ ] **Step 9: Commit**

```bash
git add backend/app/routes/pages/studio.py \
        backend/app/routes/pages/__init__.py \
        backend/app/templates/pages/studio.html \
        backend/app/templates/pages/_studio_header.html \
        backend/app/templates/pages/_studio_folder_list.html \
        backend/app/templates/pages/_studio_prompt_card.html \
        backend/app/templates/icons/_flask.svg \
        backend/app/templates/pages/_rail.html \
        tests/integration/test_studio_page.py
git commit -m "feat(studio): /studio page scaffold + rail nav + flask icon"
```

---

## Task 11: Studio layout CSS

**Files:**
- Modify: `backend/app/static/app.css`

- [ ] **Step 1: Append the studio layout block**

Add to the end of `backend/app/static/app.css`:

```css
/* ── Studio ─────────────────────────────────────────────────────────────── */

.studio-page {
  display: grid;
  grid-template-rows: auto 1fr;
  height: 100%;
  min-height: 0;
}

.studio-hdr {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 16px;
  border-bottom: 1px solid var(--line);
  background: var(--panel);
}

.studio-hdr .exp-icn {
  width: 28px;
  height: 28px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 6px;
  background: var(--surface-2);
  color: var(--accent);
}

.studio-hdr .hdr-titles {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.studio-hdr .grow {
  flex: 1;
}

.studio-body {
  display: grid;
  grid-template-columns: 320px 1fr;
  min-height: 0;
  height: 100%;
}

.studio-body.no-player .studio-player-slot {
  display: none;
}

.studio-videos {
  border-right: 1px solid var(--line);
  display: flex;
  flex-direction: column;
  min-height: 0;
  background: var(--panel);
}

.studio-right {
  display: grid;
  grid-template-rows: auto 1fr;
  min-height: 0;
  min-width: 0;
}

.studio-body.no-player .studio-right {
  grid-template-rows: 1fr;
}

.studio-player-slot {
  border-bottom: 1px solid var(--line);
  min-height: 0;
}

.studio-compare {
  display: grid;
  grid-template-columns: 1fr;
  min-height: 0;
  min-width: 0;
  padding: 12px;
  gap: 12px;
}

.studio-prompt-card {
  display: flex;
  flex-direction: column;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--surface);
  min-height: 0;
  overflow: hidden;
}

/* Folder tree */
.studio-folders {
  display: flex;
  flex-direction: column;
  min-height: 0;
  height: 100%;
}

.studio-folders-hdr {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 12px;
  border-bottom: 1px solid var(--line);
  font-weight: 600;
  font-size: 13px;
}

.studio-folders-list {
  overflow: auto;
  flex: 1;
}

.studio-folder {
  border-bottom: 1px solid var(--line);
}

.studio-folder-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  cursor: pointer;
  user-select: none;
}

.studio-folder-row:hover {
  background: var(--surface-2);
}

.studio-folder-row .twist {
  width: 12px;
  display: inline-flex;
}

.studio-folder-row .name {
  flex: 1;
  font-size: 13px;
}

.studio-folder-row .count {
  font-family: var(--f-mono, monospace);
  font-size: 11px;
  color: var(--text-3);
}

.studio-folder-kids {
  padding: 4px 8px 8px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

/* Clip card in folder */
.studio-clip-card {
  position: relative;
  display: grid;
  grid-template-columns: 64px 1fr;
  gap: 8px;
  padding: 8px;
  border: 1px solid var(--line);
  border-radius: 6px;
  background: var(--surface);
  cursor: pointer;
}

.studio-clip-card.selected {
  border-color: var(--accent);
  background: var(--surface-2);
}

.studio-clip-card .thumb {
  width: 64px;
  height: 36px;
  border-radius: 4px;
  background: var(--surface-2) center/cover no-repeat;
  position: relative;
}

.studio-clip-card .thumb .dur {
  position: absolute;
  right: 2px;
  bottom: 2px;
  font-family: var(--f-mono, monospace);
  font-size: 9.5px;
  background: rgba(0, 0, 0, 0.6);
  color: #fff;
  padding: 1px 3px;
  border-radius: 2px;
}

.studio-clip-card .meta {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.studio-clip-card .meta .name {
  font-size: 12px;
  font-weight: 500;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.studio-clip-card .meta .tag {
  font-family: var(--f-mono, monospace);
  font-size: 10.5px;
  color: var(--text-3);
}

.studio-clip-card .dots {
  position: absolute;
  top: 6px;
  right: 6px;
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.studio-clip-card .rundot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--info);
}

.studio-clip-card .rundot.cur {
  background: var(--accent);
}

.studio-clip-card .remove-x {
  position: absolute;
  top: 4px;
  right: 4px;
  width: 18px;
  height: 18px;
  border-radius: 50%;
  background: rgba(0, 0, 0, 0.5);
  color: #fff;
  display: none;
  align-items: center;
  justify-content: center;
  font-size: 11px;
  line-height: 1;
}

.studio-clip-card:hover .remove-x {
  display: inline-flex;
}

/* Prompt card */
.studio-prompt-card .pc-hdr {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 12px;
  border-bottom: 1px solid var(--line);
}

.studio-prompt-card .pc-tabs {
  display: flex;
  gap: 4px;
  padding: 4px 12px;
  border-bottom: 1px solid var(--line);
}

.studio-prompt-card .pc-tab {
  padding: 6px 10px;
  font-size: 12px;
  border: 1px solid transparent;
  border-radius: 4px;
  background: transparent;
  cursor: pointer;
}

.studio-prompt-card .pc-tab.active {
  background: var(--surface-2);
  border-color: var(--line);
}

.studio-prompt-card .pc-body {
  flex: 1;
  overflow: auto;
  padding: 12px;
  min-height: 0;
}

.studio-prompt-card .pc-editor {
  width: 100%;
  height: 100%;
  min-height: 220px;
  resize: none;
  background: var(--surface);
  color: var(--text-1);
  border: 0;
  outline: 0;
  font-family: var(--f-mono, monospace);
  font-size: 12px;
  line-height: 1.5;
}

.studio-prompt-card .pc-foot {
  display: flex;
  gap: 14px;
  padding: 8px 12px;
  border-top: 1px solid var(--line);
  font-size: 11px;
  color: var(--text-3);
}
```

- [ ] **Step 2: Visual smoke check**

Start the dev server (if not already running):
```bash
.venv/bin/uvicorn backend.app.main:app --host 127.0.0.1 --port 8765 --reload
```

Visit `http://127.0.0.1:8765/studio` and confirm the three-region layout renders (header bar, left rail ~320px, right area). Stop the server with `kill -TERM`.

- [ ] **Step 3: Commit**

```bash
git add backend/app/static/app.css
git commit -m "feat(studio): page layout CSS — three-region grid + cards"
```

---

## Task 12: Studio header (prompt picker + model picker + run button)

**Files:**
- Modify: `backend/app/templates/pages/_studio_header.html`
- Create: `backend/app/static/studio.js`
- Modify: `backend/app/templates/pages/layout.html` (include `studio.js`)

- [ ] **Step 1: Replace the header stub**

`backend/app/templates/pages/_studio_header.html`:

```html
{# Studio top bar — prompt picker (left), model picker (middle), run button (right).

   State lives in the page-level Alpine component `studioPage` (see studio.js).
   The picker dropdowns are simple buttons that toggle `open` flags and
   re-mount themselves on prompt switch. Run button reads `focusedClipId` and
   `activeVersionId` from the Alpine root scope.

   `prompts`, `selected_prompt`, `versions`, `active_version` come from the
   page route.
#}
<header class="studio-hdr" x-data="studioHeader()">
  <span class="exp-icn">{% include "icons/_flask.svg" %}</span>

  <div class="hdr-titles">
    {% if selected_prompt %}
      <div class="hdr-tpicker" x-data="{ open: false }" @click.outside="open = false">
        <button class="hdr-title hdr-title-btn" :class="open && 'open'"
                @click="open = !open" title="switch prompt">
          <span class="hdr-title-text">{{ selected_prompt.name }}</span>
          <span class="caret">▾</span>
        </button>
        <div class="hdr-tmenu" x-show="open" x-cloak>
          <div class="hdr-tmenu-h mono-cell">prompts</div>
          {% for p in prompts %}
            <a class="hdr-tmenu-item {% if selected_prompt and p.id == selected_prompt.id %}is-current{% endif %}"
               href="/studio?prompt_id={{ p.id }}">
              <span class="hdr-tmenu-lbl">
                <span class="hdr-tmenu-name">{{ p.name }}</span>
                {% if p.description %}<span class="hdr-tmenu-desc">{{ p.description }}</span>{% endif %}
              </span>
              <span class="grow"></span>
              <span class="hdr-tmenu-v mono-cell">id:{{ p.id }}</span>
            </a>
          {% endfor %}
        </div>
      </div>
    {% else %}
      <div class="hdr-title muted">Pick a prompt</div>
    {% endif %}
  </div>

  {% if active_version %}
    <div class="model-picker" x-data="{ open: false, model: $root.activeModel }" @click.outside="open = false">
      <button class="tag info model-picker-btn" :class="open && 'open'"
              @click="open = !open" title="switch model">
        <span class="dot"></span>
        <span x-text="model"></span>
        <span class="caret">▾</span>
      </button>
      <div class="model-menu" x-show="open" x-cloak>
        {% for m in [
          'gemini-2.5-pro',
          'gemini-2.5-flash',
          'gemini-2.5-flash-lite',
          'gemini-2.0-pro',
        ] %}
          <button class="model-menu-item" :class="model === '{{ m }}' && 'is-current'"
                  @click="model = '{{ m }}'; $root.activeModel = model; open = false">
            <span class="model-menu-dot"></span>
            <span class="model-menu-lbl">{{ m }}</span>
          </button>
        {% endfor %}
      </div>
    </div>
  {% endif %}

  <span class="grow"></span>

  {% if active_version %}
    <button class="btn primary studio-run-btn"
            :disabled="!$root.focusedClipId || $root.running"
            @click="$root.runOnFocusedClip()"
            :title="$root.focusedClipId ? '' : 'Click a clip in a folder to focus it'">
      <template x-if="!$root.running">
        <span>▶ Run on this clip · v{{ active_version.version_num }}</span>
      </template>
      <template x-if="$root.running">
        <span>⟳ Running… <span x-text="$root.runningElapsedLabel"></span></span>
      </template>
    </button>
  {% else %}
    <button class="btn primary" disabled title="No version available for this prompt">▶ Run</button>
  {% endif %}
</header>
```

- [ ] **Step 2: Create the Alpine component**

`backend/app/static/studio.js`:

```javascript
/* Studio page — Alpine root state.

   The page template puts `x-data="studioPage(...)"` on .studio-page.
   Child components read parent scope via $root so the header, folder
   list, and prompt card all share the same focused-clip / running flag.

   Run lifecycle:
     1. POST /api/studio/runs {prompt_version_id, clip_id, model}
        → {run_id, job_id}
     2. Poll GET /api/studio/runs/{run_id} every 1s until status != pending|running
     3. On completion (ok or error), HTMX-swap the prompt card body via:
        hx-get="/studio/_run?prompt_version_id=…&clip_id=…"
        We trigger that swap by setting `pendingRunSwap = …` which the
        card watches with x-init/x-effect.
*/

document.addEventListener('alpine:init', () => {
  Alpine.data('studioPage', (initial) => ({
    promptId: initial.promptId,
    activeVersionId: initial.activeVersionId,
    activeVersionNum: initial.activeVersionNum,
    activeModel: initial.activeModel,
    focusedClipId: null,
    running: false,
    runId: null,
    runStartMs: 0,
    runningElapsedLabel: '00:00',
    pendingRunSwap: 0,  // incremented to nudge cards to re-fetch

    init() {
      // Tick elapsed-time label while running.
      setInterval(() => {
        if (this.running) {
          const s = Math.floor((performance.now() - this.runStartMs) / 1000);
          const m = String(Math.floor(s / 60)).padStart(2, '0');
          const r = String(s % 60).padStart(2, '0');
          this.runningElapsedLabel = `${m}:${r}`;
        }
      }, 500);
    },

    focusClip(clipId) {
      this.focusedClipId = clipId;
      this.pendingRunSwap++;
    },

    async runOnFocusedClip() {
      if (!this.activeVersionId || !this.focusedClipId || this.running) return;
      this.running = true;
      this.runStartMs = performance.now();
      this.runningElapsedLabel = '00:00';
      try {
        const res = await fetch('/api/studio/runs', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({
            prompt_version_id: this.activeVersionId,
            clip_id: this.focusedClipId,
            model: this.activeModel || null,
          }),
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const {run_id} = await res.json();
        this.runId = run_id;
        await this._poll(run_id);
      } catch (err) {
        console.error('studio run failed', err);
      } finally {
        this.running = false;
        this.pendingRunSwap++;
      }
    },

    async _poll(runId) {
      while (true) {
        await new Promise(r => setTimeout(r, 1000));
        const res = await fetch(`/api/studio/runs/${runId}`);
        if (!res.ok) return;
        const run = await res.json();
        if (run.status === 'ok' || run.status === 'error') return;
      }
    },
  }));

  Alpine.data('studioHeader', () => ({}));
});
```

- [ ] **Step 3: Include `studio.js` in `layout.html`**

Edit `backend/app/templates/pages/layout.html` — add to the script imports after `clipAnnotate.js`:

```html
<script defer src="/static/studio.js"></script>
```

- [ ] **Step 4: Pass initial state to Alpine in the page template**

Edit `backend/app/templates/pages/studio.html` — change the page root div to:

```html
<div class="studio-page"
     x-data="studioPage({
       promptId: {{ selected_prompt.id if selected_prompt else 'null' }},
       activeVersionId: {{ active_version.id if active_version else 'null' }},
       activeVersionNum: {{ active_version.version_num if active_version else 'null' }},
       activeModel: '{{ active_version.model if active_version else '' }}',
     })">
```

- [ ] **Step 5: Smoke check**

Start server, visit `/studio?prompt_id=<existing-prompt-id>`, confirm the header shows the prompt name (clickable), model picker chip with the active version's model, and a Run button disabled because no clip is focused. Click the prompt picker — it should list all active prompts.

- [ ] **Step 6: Commit**

```bash
git add backend/app/templates/pages/_studio_header.html \
        backend/app/templates/pages/studio.html \
        backend/app/templates/pages/layout.html \
        backend/app/static/studio.js
git commit -m "feat(studio): header — prompt picker, model picker, run button"
```

---

## Task 13: Folder list + create-folder UI

**Files:**
- Modify: `backend/app/templates/pages/_studio_folder_list.html`
- Create: `backend/app/templates/pages/_studio_folder.html`
- Modify: `backend/app/routes/pages/studio.py` (add partial endpoints)

- [ ] **Step 1: Add partial endpoints**

Append to `backend/app/routes/pages/studio.py`:

```python
@router.get("/studio/_folders", response_class=HTMLResponse)
async def _studio_folders(request: Request):
    ctx = get_ctx(request)
    folders = await ctx.studio_folders_repo.list_folders_with_counts(ctx.db)
    return templates.TemplateResponse(
        "pages/_studio_folder_list.html",
        {"request": request, "folders": folders, "expanded_folder_id": None, "clips": []},
    )


@router.get("/studio/_folder", response_class=HTMLResponse)
async def _studio_folder(request: Request, folder_id: int, active_version_id: int | None = None):
    """Expanded folder view — folder header + clips with run-dots."""
    ctx = get_ctx(request)
    clips_rows = await ctx.studio_folders_repo.list_clips(ctx.db, folder_id)
    # Build per-clip "has any run with active version" / "any other version" flags.
    enriched = []
    for c in clips_rows:
        versions = await ctx.studio_runs_repo.versions_run_on_clip(
            ctx.db, clip_id=c["clip_id"]
        )
        has_cur = active_version_id is not None and active_version_id in versions
        has_other = any(v != active_version_id for v in versions)
        # Pull minimal clip metadata via the archive if available; fall back to id.
        meta: dict = {"name": f"clip-{c['clip_id']}", "duration_secs": None}
        if ctx.archive:
            try:
                clip = await ctx.archive.get_clip(str(c["clip_id"]))
                meta = {
                    "name": clip.name,
                    "duration_secs": clip.duration_secs,
                    "year": (clip.provider_data or {}).get("pragafilm.rok.natoceni"),
                }
            except Exception:  # noqa: BLE001
                pass
        enriched.append({**c, **meta, "has_cur": has_cur, "has_other": has_other})
    return templates.TemplateResponse(
        "pages/_studio_folder.html",
        {"request": request, "folder_id": folder_id, "clips": enriched},
    )
```

- [ ] **Step 2: Folder list template**

Replace `backend/app/templates/pages/_studio_folder_list.html`:

```html
<div class="studio-folders" x-data="studioFolders()">
  <div class="studio-folders-hdr">
    <span>Folders</span>
    <span class="grow"></span>
    <button class="btn ghost mini" @click="newFolderOpen = !newFolderOpen">+ New folder</button>
  </div>

  <div x-show="newFolderOpen" x-cloak class="new-folder">
    <input type="text" placeholder="folder name…" x-model="newFolderName"
           @keyup.enter="createFolder()" />
    <button class="btn primary mini" @click="createFolder()">Create</button>
  </div>

  <div class="studio-folders-list">
    {% for f in folders %}
      <div class="studio-folder" :class="expandedId === {{ f.id }} && 'open'">
        <div class="studio-folder-row" @click="toggle({{ f.id }})">
          <span class="twist" x-text="expandedId === {{ f.id }} ? '▾' : '▸'"></span>
          <span class="name">{{ f.name }}</span>
          <span class="count">{{ f.clip_count }}</span>
        </div>
        <div class="studio-folder-kids" x-show="expandedId === {{ f.id }}" x-cloak
             hx-get="/studio/_folder?folder_id={{ f.id }}&active_version_id={{ active_version.id if active_version else '' }}"
             hx-trigger="load once from:.studio-folder-row"
             hx-swap="innerHTML">
          <div class="muted">loading…</div>
        </div>
      </div>
    {% endfor %}
    {% if not folders %}
      <div class="muted" style="padding:12px">No folders yet. Create one above.</div>
    {% endif %}
  </div>
</div>
```

- [ ] **Step 3: Single-folder partial**

`backend/app/templates/pages/_studio_folder.html`:

```html
{% for c in clips %}
  {% include "pages/_studio_clip_card.html" with context %}
{% endfor %}
{% if not clips %}
  <div class="muted" style="padding:8px 4px">Empty folder.</div>
{% endif %}
<button class="btn ghost mini studio-add-from-archive"
        hx-get="/studio/_archive_picker?folder_id={{ folder_id }}"
        hx-target="#modal-root" hx-swap="innerHTML">
  + Add from archive
</button>
```

- [ ] **Step 4: Add the Alpine component for folders**

Append to `backend/app/static/studio.js`:

```javascript
  Alpine.data('studioFolders', () => ({
    expandedId: null,
    newFolderOpen: false,
    newFolderName: '',

    toggle(id) {
      this.expandedId = this.expandedId === id ? null : id;
    },

    async createFolder() {
      const name = this.newFolderName.trim();
      if (!name) return;
      const res = await fetch('/api/studio/folders', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({name}),
      });
      if (res.ok) {
        location.reload();  // simplest: reload to refresh folder list
      } else if (res.status === 409) {
        alert(`Folder "${name}" already exists.`);
      }
    },
  }));
```

- [ ] **Step 5: Add `#modal-root` to layout**

Edit `backend/app/templates/pages/layout.html`. Right before `</body>`, add:

```html
<div id="modal-root"></div>
```

- [ ] **Step 6: Smoke check**

Visit `/studio`. The left rail should show "Folders" header, "+ New folder" button. Create a folder ("test1"). Reload — folder appears in the list with count 0. Click it — twist toggles, kids panel shows "Empty folder. + Add from archive".

- [ ] **Step 7: Commit**

```bash
git add backend/app/templates/pages/_studio_folder_list.html \
        backend/app/templates/pages/_studio_folder.html \
        backend/app/routes/pages/studio.py \
        backend/app/static/studio.js \
        backend/app/templates/pages/layout.html
git commit -m "feat(studio): folder list + create folder + HTMX-loaded clip lists"
```

---

## Task 14: Clip card with run-dots + focus behavior

**Files:**
- Create: `backend/app/templates/pages/_studio_clip_card.html`

- [ ] **Step 1: Create the clip card template**

`backend/app/templates/pages/_studio_clip_card.html`:

```html
{# Clip card — focus on click, remove on hover X, run-dots top-right.

   Variables in scope:
     c  — dict from _studio_folder endpoint: clip_id, name, duration_secs,
          year, has_cur, has_other
     folder_id  — outer folder id
#}
<div class="studio-clip-card"
     :class="$root.focusedClipId === {{ c.clip_id }} && 'selected'"
     @click="$root.focusClip({{ c.clip_id }})"
     data-clip-id="{{ c.clip_id }}">
  <div class="thumb"
       {% if c.clip_id %}style="background-image:url('/api/cache/clips/{{ c.clip_id }}/thumb')"{% endif %}>
    {% if c.duration_secs %}
      <span class="dur">{{ "%d:%02d" | format(c.duration_secs // 60, c.duration_secs % 60) }}</span>
    {% endif %}
  </div>
  <div class="meta">
    <div class="name" title="{{ c.name }}">{{ c.name }}</div>
    <div class="tag">id:{{ c.clip_id }}{% if c.year %} · {{ c.year }}{% endif %}</div>
  </div>
  <div class="dots">
    {% if c.has_cur %}<span class="rundot cur" title="ran with active version"></span>{% endif %}
    {% if c.has_other %}<span class="rundot" title="ran with other version(s)"></span>{% endif %}
  </div>
  <button class="remove-x" title="remove from folder"
          @click.stop="if (confirm('Remove from folder?')) { fetch('/api/studio/folders/{{ folder_id }}/clips/{{ c.clip_id }}', {method:'DELETE'}).then(() => $el.closest('.studio-clip-card').remove()); }">×</button>
</div>
```

Note on the thumb URL: the existing CatDV poster pipeline serves thumbnails at `/api/cache/clips/{id}/thumb` (verify the actual route in `backend/app/routes/cache.py` — if it differs, substitute the real one).

- [ ] **Step 2: Verify the thumb URL**

Run: `grep -n "thumb" backend/app/routes/cache.py backend/app/routes/media.py | head -10`

If the actual thumbnail endpoint differs (e.g. `/api/media/{id}/thumb`), update the template above accordingly. Existing thumbnail rendering lives in `_video_list.html` — match that URL pattern.

- [ ] **Step 3: Smoke check**

Add a clip to a folder via API:
```bash
curl -X POST http://127.0.0.1:8765/api/studio/folders/1/clips \
  -H 'Content-Type: application/json' \
  -d '{"clip_ids":[12041]}'
```

Reload `/studio`, expand the folder, confirm the clip card renders with the clip name, id, and (after at least one run is persisted) a run-dot. Hover and confirm the X appears. Click the card — `focusedClipId` updates (run button enables).

- [ ] **Step 4: Commit**

```bash
git add backend/app/templates/pages/_studio_clip_card.html
git commit -m "feat(studio): clip card — thumb, name, run-dots, focus + remove"
```

---

## Task 15: Prompt card (editor + tabs + output)

**Files:**
- Modify: `backend/app/templates/pages/_studio_prompt_card.html`
- Create: `backend/app/templates/pages/_studio_run_output.html`
- Modify: `backend/app/routes/pages/studio.py` (add `/studio/_run` partial)

- [ ] **Step 1: Add the run-output partial route**

Append to `backend/app/routes/pages/studio.py`:

```python
@router.get("/studio/_run", response_class=HTMLResponse)
async def _studio_run(
    request: Request,
    prompt_version_id: int,
    clip_id: int,
):
    ctx = get_ctx(request)
    run = await ctx.studio_runs_repo.latest_for_pair(
        ctx.db, prompt_version_id=prompt_version_id, clip_id=clip_id
    )
    version = await ctx.prompts_repo.get_version(ctx.db, prompt_version_id)
    return templates.TemplateResponse(
        "pages/_studio_run_output.html",
        {"request": request, "run": run, "version": version},
    )
```

- [ ] **Step 2: Output partial template**

`backend/app/templates/pages/_studio_run_output.html`:

```html
{# Renders the latest studio_run.output_json for (version, clip).
   Known fields rendered as widgets; everything else as generic rows.
#}
{% if not run %}
  <div class="run-empty muted">
    No run yet. Hit <b>Run</b> to execute v{{ version.version_num }} on the focused clip.
  </div>
{% elif run.status == 'error' %}
  <div class="run-error">
    <div class="run-error-h"><b>Error</b> — v{{ version.version_num }}</div>
    <div class="run-error-msg">{{ run.error or 'unknown error' }}</div>
  </div>
{% elif run.status in ('pending', 'running') %}
  <div class="run-empty muted">⟳ Running…</div>
{% else %}
  {% set o = run.output_json or {} %}
  <div class="run-output">
    {% if o.get('scenes') %}
      <div class="ro-section">
        <div class="ro-hdr">
          <span>scenes</span>
          <span class="ro-count">{{ o.scenes|length }}</span>
          <span class="grow"></span>
          <span class="mono-cell ro-target">→ CatDV markers</span>
        </div>
        <div class="ro-scenes">
          {% for s in o.scenes %}
            <div class="ro-scene">
              <span class="ro-tc mono-cell">{{ "%.2f" | format(s.in_secs) }} – {{ "%.2f" | format(s.out_secs) }}</span>
              <span class="ro-name">{{ s.name }}</span>
            </div>
          {% endfor %}
        </div>
      </div>
    {% endif %}
    {% for k, v in o.items() %}
      {% if k != 'scenes' %}
        <div class="ro-field">
          <div class="ro-fhdr"><span class="ro-fkey">{{ k }}</span></div>
          <div class="ro-fval">{{ v }}</div>
        </div>
      {% endif %}
    {% endfor %}
  </div>
  <div class="run-stats mono-cell muted" style="padding:6px 0;">
    {{ "%.1f"|format(run.duration_s or 0) }}s · {{ run.tokens_out or 0 }} tok · ${{ "%.4f"|format(run.cost_usd or 0) }} · {{ run.model }}
  </div>
{% endif %}
```

- [ ] **Step 3: Prompt card template (single-version mode for PR1)**

Replace `backend/app/templates/pages/_studio_prompt_card.html`:

```html
{# PR1: single prompt card (cur only). PR2 will add the compare card.
   Editor is shown only when the active version is a draft.
#}
<div class="studio-prompt-card" x-data="studioPromptCard()">
  <div class="pc-hdr">
    {% if active_version %}
      <span class="pc-vlbl">v{{ active_version.version_num }}</span>
      <span class="pc-status {{ active_version.state }}">{{ active_version.state }}</span>
      <span class="grow"></span>
      <span class="pc-meta mono-cell">{{ active_version.model }}</span>
    {% else %}
      <span class="muted">no version</span>
    {% endif %}
  </div>

  <div class="pc-tabs">
    <button class="pc-tab" :class="mode === 'prompt' && 'active'" @click="mode = 'prompt'">Prompt</button>
    <button class="pc-tab" :class="mode === 'output' && 'active'" @click="mode = 'output'">Output</button>
  </div>

  <div class="pc-body">
    {% if active_version %}
      <div x-show="mode === 'prompt'">
        {% if active_version.state == 'draft' %}
          <textarea class="pc-editor" x-ref="editor"
                    @input.debounce.700ms="save()"
                    spellcheck="false">{{ active_version.body }}</textarea>
        {% else %}
          <pre class="pc-readonly mono">{{ active_version.body }}</pre>
        {% endif %}
      </div>
      <div x-show="mode === 'output'"
           x-init="$nextTick(() => loadOutput())"
           x-effect="$root.pendingRunSwap && loadOutput()"
           hx-trigger="never">
        <div class="run-slot" x-ref="runSlot">
          <div class="muted">loading…</div>
        </div>
      </div>
    {% endif %}
  </div>

  <div class="pc-foot">
    <span x-show="dirty" class="mono-cell muted">draft · saving…</span>
    <span x-show="!dirty" class="mono-cell muted">saved</span>
  </div>
</div>
```

- [ ] **Step 4: Append Alpine card component**

Append to `backend/app/static/studio.js`:

```javascript
  Alpine.data('studioPromptCard', () => ({
    mode: 'prompt',
    dirty: false,
    _saveTimer: null,

    async save() {
      this.dirty = true;
      const versionId = this.$root.activeVersionId;
      const promptId = this.$root.promptId;
      if (!versionId || !promptId) return;
      const body = this.$refs.editor ? this.$refs.editor.value : null;
      if (body == null) return;
      // Fetch the existing version to round-trip target_map/output_schema/model,
      // since the prompts API requires the full PUT body.
      const v = await fetch(`/api/prompts/${promptId}/versions/${versionId}`).then(r => r.json());
      const res = await fetch(`/api/prompts/${promptId}/versions/${versionId}`, {
        method: 'PUT',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          body,
          target_map: v.target_map,
          output_schema: v.output_schema,
          model: v.model,
        }),
      });
      this.dirty = !res.ok;
    },

    async loadOutput() {
      const versionId = this.$root.activeVersionId;
      const clipId = this.$root.focusedClipId;
      if (!versionId) return;
      const slot = this.$refs.runSlot;
      if (!slot) return;
      if (!clipId) {
        slot.innerHTML = '<div class="muted">Click a clip in a folder to focus it.</div>';
        return;
      }
      const html = await fetch(
        `/studio/_run?prompt_version_id=${versionId}&clip_id=${clipId}`,
      ).then(r => r.text());
      slot.innerHTML = html;
    },
  }));
```

- [ ] **Step 5: Smoke check**

Visit `/studio?prompt_id=<existing>`. Confirm:
- Prompt card shows version label + state badge + model.
- Tabs Prompt | Output present; Prompt tab shows editable textarea when the active version is a draft, read-only `<pre>` otherwise.
- Click a clip in a folder → Output tab shows "No run yet…" (until you click Run).
- Edit the textarea → after 700ms debounce, "saving…" briefly shows, then "saved".

Verify the save round-trips: refresh the page, the edited text should persist.

- [ ] **Step 6: Commit**

```bash
git add backend/app/templates/pages/_studio_prompt_card.html \
        backend/app/templates/pages/_studio_run_output.html \
        backend/app/routes/pages/studio.py \
        backend/app/static/studio.js
git commit -m "feat(studio): prompt card — editor with debounce auto-save + output tab"
```

---

## Task 16: Archive picker modal

**Files:**
- Create: `backend/app/templates/pages/_studio_archive_picker.html`
- Modify: `backend/app/routes/pages/studio.py` (add picker partial)

- [ ] **Step 1: Add the picker partial route**

Append to `backend/app/routes/pages/studio.py`:

```python
@router.get("/studio/_archive_picker", response_class=HTMLResponse)
async def _studio_archive_picker(
    request: Request,
    folder_id: int,
    q: str = "",
):
    """Renders the archive picker modal body. Uses ArchiveProvider.list_clips
    when available; empty list in offline/test mode."""
    from backend.app.archive.model import ClipQuery

    ctx = get_ctx(request)
    results = []
    if ctx.archive:
        try:
            page = await ctx.archive.list_clips(
                ctx.settings.catdv_catalog_id,
                ClipQuery(text=q or None, offset=0, limit=50),
            )
            results = list(page.items or ())
        except Exception:  # noqa: BLE001
            results = []
    return templates.TemplateResponse(
        "pages/_studio_archive_picker.html",
        {"request": request, "folder_id": folder_id, "q": q, "results": results},
    )
```

`ClipQuery` lives in `backend.app.archive.model`. `ctx.settings.catdv_catalog_id` is the catalog id string (same env var used elsewhere).

- [ ] **Step 2: Modal template**

`backend/app/templates/pages/_studio_archive_picker.html`:

```html
<div class="modal" x-data="archivePicker({{ folder_id }})" @keydown.escape.window="close()">
  <div class="modal-backdrop" @click="close()"></div>
  <div class="modal-card">
    <div class="modal-hdr">
      <h2>Add clips to folder</h2>
      <span class="grow"></span>
      <button class="btn ghost" @click="close()">×</button>
    </div>
    <div class="modal-body">
      <input type="search" placeholder="search clips…" x-model="q"
             @input.debounce.300ms="search()"
             hx-get="/studio/_archive_picker?folder_id={{ folder_id }}"
             hx-target=".modal-results" hx-trigger="search delay:300ms changed">
      <div class="modal-results">
        {% if not results %}
          <div class="muted">No clips returned. Try a different query.</div>
        {% endif %}
        {% for c in results %}
          <label class="picker-row">
            <input type="checkbox" :checked="picked.has({{ c.id }})"
                   @change="toggle({{ c.id }})">
            <span class="name">{{ c.name }}</span>
            <span class="mono-cell muted">id:{{ c.id }}</span>
          </label>
        {% endfor %}
      </div>
    </div>
    <div class="modal-foot">
      <span class="muted" x-text="picked.size + ' selected'"></span>
      <span class="grow"></span>
      <button class="btn ghost" @click="close()">Cancel</button>
      <button class="btn primary" @click="addSelected()" :disabled="picked.size === 0">Add</button>
    </div>
  </div>
</div>
```

- [ ] **Step 3: Add modal CSS + Alpine**

Append to `backend/app/static/app.css`:

```css
.modal { position: fixed; inset: 0; z-index: 100; display: flex; align-items: center; justify-content: center; }
.modal-backdrop { position: absolute; inset: 0; background: rgba(0,0,0,0.5); }
.modal-card { position: relative; width: 640px; max-height: 80vh; background: var(--surface); border: 1px solid var(--line); border-radius: 8px; display: flex; flex-direction: column; overflow: hidden; }
.modal-hdr, .modal-foot { display: flex; gap: 8px; padding: 10px 14px; align-items: center; border-bottom: 1px solid var(--line); }
.modal-foot { border-top: 1px solid var(--line); border-bottom: 0; }
.modal-body { padding: 12px 14px; overflow: auto; }
.modal-results { display: flex; flex-direction: column; gap: 4px; margin-top: 8px; }
.picker-row { display: flex; gap: 8px; align-items: center; padding: 4px 6px; border-radius: 4px; }
.picker-row:hover { background: var(--surface-2); }
```

Append to `backend/app/static/studio.js`:

```javascript
  Alpine.data('archivePicker', (folderId) => ({
    folderId,
    q: '',
    picked: new Set(),

    toggle(id) {
      if (this.picked.has(id)) this.picked.delete(id);
      else this.picked.add(id);
    },

    async addSelected() {
      const ids = Array.from(this.picked);
      if (!ids.length) return;
      const res = await fetch(`/api/studio/folders/${this.folderId}/clips`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({clip_ids: ids}),
      });
      if (res.ok) {
        location.reload();
      }
    },

    close() {
      const root = document.getElementById('modal-root');
      if (root) root.innerHTML = '';
    },
  }));
```

- [ ] **Step 4: Smoke check**

Click "+ Add from archive" inside an expanded folder. Modal opens. Type into the search box — list updates. Tick a few clips, click Add — page reloads, the clips appear in the folder.

- [ ] **Step 5: Commit**

```bash
git add backend/app/templates/pages/_studio_archive_picker.html \
        backend/app/routes/pages/studio.py \
        backend/app/static/app.css \
        backend/app/static/studio.js
git commit -m "feat(studio): archive picker modal — search + multi-select add"
```

---

## Task 17: Player embed for focused clip

**Files:**
- Create: `backend/app/templates/pages/_studio_player.html`
- Modify: `backend/app/routes/pages/studio.py` (add player partial)
- Modify: `backend/app/static/studio.js` (load player on focus)

- [ ] **Step 1: Add player partial route**

Append to `backend/app/routes/pages/studio.py`:

```python
@router.get("/studio/_player", response_class=HTMLResponse)
async def _studio_player(request: Request, clip_id: int):
    """Reuses the existing clip player wrapper. The player.js component
    already knows how to mount given a clip id; this template only
    provides the container."""
    return templates.TemplateResponse(
        "pages/_studio_player.html",
        {"request": request, "clip_id": clip_id},
    )
```

- [ ] **Step 2: Player wrapper template**

`backend/app/templates/pages/_studio_player.html`:

```html
{# Minimal wrapper around the existing player. The actual <video> + transport
   is mounted by player.js based on the data attribute. We don't introduce
   any new player behavior here. #}
<div class="studio-player" data-clip-player data-clip-id="{{ clip_id }}">
  <video controls preload="metadata"
         src="/api/cache/clips/{{ clip_id }}/media"></video>
</div>
```

Note: the actual media URL may differ; confirm against `backend/app/routes/media.py`. If `player.js` provides a richer auto-mount (e.g. via Alpine `x-data="player"`), use that instead. The goal is to display the focused clip; we layer overlays in PR2.

- [ ] **Step 3: Trigger player load on focus**

Edit `studio.js` — extend the `focusClip` method:

```javascript
    focusClip(clipId) {
      this.focusedClipId = clipId;
      this.pendingRunSwap++;
      // Toggle no-player off and load the player partial
      const body = document.querySelector('.studio-body');
      if (body) body.classList.remove('no-player');
      const slot = document.querySelector('[data-studio-player-slot]');
      if (slot) {
        fetch(`/studio/_player?clip_id=${clipId}`)
          .then(r => r.text())
          .then(html => { slot.innerHTML = html; });
      }
    },
```

- [ ] **Step 4: Smoke check**

Click a clip card → the player slot above the prompt card now shows the clip's video, with native controls. Confirm the right pane reflows (player visible, prompt card below it).

- [ ] **Step 5: Commit**

```bash
git add backend/app/templates/pages/_studio_player.html \
        backend/app/routes/pages/studio.py \
        backend/app/static/studio.js
git commit -m "feat(studio): player embed for focused clip (reuses existing media route)"
```

---

## Task 18: "Open in Studio" link from /prompts menu

**Files:**
- Modify: `backend/app/templates/pages/_prompt_menu.html`

- [ ] **Step 1: Inspect current menu**

Run: `cat backend/app/templates/pages/_prompt_menu.html`

Identify the existing item list pattern (e.g. `<button class="tmpl-menu-item">`).

- [ ] **Step 2: Add the menu item**

Insert near the top of the menu (after any "Create new version" item, before the separator):

```html
<a class="tmpl-menu-item" href="/studio?prompt_id={{ prompt.id }}">
  {% include "icons/_flask.svg" %}
  Open in Studio
</a>
```

If the menu uses `<button>` elements styled as menu items and the existing pattern is JS-driven, switch this entry to a plain `<a>` for the navigation and preserve the same class list so styling matches.

- [ ] **Step 3: Smoke check**

Visit `/prompts/<id>`, open the kebab menu, click "Open in Studio" → land on `/studio?prompt_id=<id>` with that prompt active in the header.

- [ ] **Step 4: Commit**

```bash
git add backend/app/templates/pages/_prompt_menu.html
git commit -m "feat(studio): 'Open in Studio' menu item on prompt detail"
```

---

## Task 19: End-to-end happy-path integration test

**Files:**
- Modify: `tests/integration/test_studio_api.py` (append e2e test)

- [ ] **Step 1: Write the e2e test**

This test does not exercise the worker (externals aren't wired in test env), but it does drive the full HTTP surface:

Append to `tests/integration/test_studio_api.py`:

```python
@pytest.mark.asyncio
async def test_studio_e2e_happy_path(client):
    # 1. Create a prompt.
    r = await client.post(
        "/api/prompts",
        json={
            "name": "studio-e2e",
            "body": "do x",
            "target_map": {},
            "output_schema": {},
            "model": "gemini-2.5-pro",
        },
    )
    assert r.status_code == 201
    pid = r.json()["id"]
    vid = (await client.get(f"/api/prompts/{pid}")).json()["latest_version_id"]

    # 2. Create a folder.
    r = await client.post("/api/studio/folders", json={"name": "e2e-folder"})
    assert r.status_code == 201
    fid = r.json()["id"]

    # 3. Add a clip to the folder.
    r = await client.post(f"/api/studio/folders/{fid}/clips", json={"clip_ids": [42]})
    assert r.status_code == 200
    assert r.json()["added"] == 1

    # 4. Folder list shows the count.
    r = await client.get("/api/studio/folders")
    f = next(x for x in r.json() if x["id"] == fid)
    assert f["clip_count"] == 1

    # 5. Create a run.
    r = await client.post(
        "/api/studio/runs",
        json={"prompt_version_id": vid, "clip_id": 42, "model": "gemini-2.5-flash"},
    )
    assert r.status_code == 201
    run_id = r.json()["run_id"]

    # 6. Run is pending with the override model recorded.
    r = await client.get(f"/api/studio/runs/{run_id}")
    assert r.status_code == 200
    run = r.json()
    assert run["status"] == "pending"
    assert run["model"] == "gemini-2.5-flash"

    # 7. Latest lookup returns it.
    r = await client.get(
        "/api/studio/runs",
        params={"prompt_version_id": vid, "clip_id": 42, "latest": 1},
    )
    assert r.json()["id"] == run_id

    # 8. Studio page renders with this prompt + folder.
    r = await client.get(f"/studio?prompt_id={pid}")
    assert r.status_code == 200
    assert "e2e-folder" in r.text
```

- [ ] **Step 2: Run**

Run: `.venv/bin/pytest tests/integration/test_studio_api.py -v`
Expected: all tests pass.

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_studio_api.py
git commit -m "test(studio): end-to-end happy-path through HTTP surface"
```

---

## Task 20: Lint, type-check, full test sweep

**Files:**
- None — verification only.

- [ ] **Step 1: Ruff**

Run: `.venv/bin/ruff check backend/ tests/`
Expected: clean. Fix any new findings inline.

- [ ] **Step 2: basedpyright**

Run: `.venv/bin/basedpyright backend/app/routes/studio.py backend/app/routes/pages/studio.py backend/app/repositories/studio_folders.py backend/app/repositories/studio_runs.py backend/app/services/annotator.py backend/app/models/studio.py`
Expected: 0 errors. Fix any inline.

- [ ] **Step 3: Full pytest sweep**

Run: `.venv/bin/pytest -x`
Expected: all tests pass.

- [ ] **Step 4: Manual smoke (only if a real CatDV seat is available)**

Bring up the server, visit `/studio`, run a real prompt against a real clip end-to-end. Confirm:
- Run button shows `Running…` → `Done`.
- Output panel populates with structured result.
- Clip card now shows the accent run-dot for the active version.
- `studio_run` row in SQLite has `status='ok'`, `output_json` populated.

Use the `server-stop` skill to shut down gracefully when done — never `kill -9`.

- [ ] **Step 5: Commit (only if any fixes were needed in steps 1-2)**

```bash
git add -p   # selectively
git commit -m "chore(studio): lint + type fixes"
```

---

## Task 21: ADR for PR1

**Files:**
- Create: `docs/adr/0033-prompt-studio-pr1-shell-and-run-loop.md`
- Modify: `docs/decisions.md`

- [ ] **Step 1: Write the ADR**

`docs/adr/0033-prompt-studio-pr1-shell-and-run-loop.md`:

```markdown
# 0033. Prompt Studio PR1 — Shell + Single-Clip Run Loop

**Date:** 2026-05-26
**Status:** Accepted

## Context

We need an iteration UI for prompts — pick a clip, run the prompt, see structured output, tweak, re-run. A prior attempt (PR #9) shipped the entire studio in one PR and was reverted. The new design (spec `2026-05-26-prompt-studio-design.md`) explicitly slices the work into three vertical PRs. This ADR records the implementation decisions for PR1.

## Alternatives considered

1. **Single PR for the whole studio** — rejected: that's what PR #9 did.
2. **Separate jobs subsystem for studio runs** — rejected: duplicates worker orchestration, two cancellation paths, two error stories.
3. **Synchronous in-request runs** — rejected: model latency makes that an awkward UX (no progress, no cancellation, long-held HTTP connections).

## Decision

- Reuse the existing `jobs` pipeline with a nullable `jobs.kind` column. `kind='studio'` triggers an annotator-side branch that persists to `studio_run` and skips the CatDV-write step.
- Three new tables (`studio_folder`, `studio_folder_clip`, `studio_run`). Folders are flat (one-level), globally scoped (not per-prompt), and clip membership is a many-to-many key pair (`folder_id`, `clip_id`).
- Studio operates on *all* versions (any version is runnable); only the *draft* body is editable. The /prompts page keeps lifecycle management; Studio is the iteration loop.
- Single focused clip at a time. No multi-select / batch runs in v1.
- Model picker overrides the version's stored model per-run; `studio_run.model` records what actually executed.

## Consequences

- Cancel/retry/SSE come free with the jobs pipeline.
- `studio_run` history is persisted forever but only the latest per (version, clip) is surfaced in UI. Future PRs can add timeline/history views.
- The annotator's `_process_item` branches on `kind`; both paths share resolve/upload/prompt steps. A test pins both branches against the same input to detect divergence.
- Folder naming is globally unique — no namespacing needed for v1 but constrains future per-prompt folders.
```

- [ ] **Step 2: Update decisions index**

Edit `docs/decisions.md`, append a row to the index table:

```
| 0033 | Prompt Studio PR1 — Shell + Single-Clip Run Loop | Accepted | 2026-05-26 |
```

(If the existing table format differs, match it — column count/order.)

- [ ] **Step 3: Commit**

```bash
git add docs/adr/0033-prompt-studio-pr1-shell-and-run-loop.md docs/decisions.md
git commit -m "docs(adr): 0033 — Prompt Studio PR1 shell + run loop"
```

---

## Done

PR1 lands the studio shell + single-clip run loop. Next:

- **PR2:** Version compare card with `+ Compare`, version picker per card, `PromptDiff` (line-LCS) + `OutputDiff`, two-row marker overlay on the player timeline. Get its own plan.
- **PR3:** Visual polish pass, run-state transitions, empty/error-state polish. Get its own plan.

When PR1 is ready to merge, use the `finishing-a-development-branch` skill.
