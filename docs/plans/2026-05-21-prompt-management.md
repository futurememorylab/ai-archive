# Prompt Management Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the single-row `templates` table with a versioned `prompts` + `prompt_versions` model, rewire annotator/jobs/review to use `prompt_version_id`, and ship a server-rendered Prompts management UI (left-nav item + list/detail page with version states, kebab actions, dirty Save).

**Architecture:** One DB migration that creates the new tables, backfills from `templates`, rebuilds `annotations` + `jobs` to swap `template_id` → `prompt_version_id`, then drops `templates`. New `PromptsRepo` enforces the production-immutability invariant via partial unique index and explicit state checks. Annotator/jobs/review/seed are refactored in the same change — no compat shim. UI is FastAPI/Jinja partials + HTMX, mirroring `routes/cache.py`'s pattern (REST JSON under `/api/`, HTML actions under page paths). Alpine.js for trivially small client state (dirty tracking, kebab/model dropdowns).

**Tech Stack:** FastAPI · Pydantic v2 · aiosqlite · Jinja2 · HTMX · Alpine.js · pytest-asyncio. No new dependencies.

**Reference spec:** `docs/specs/2026-05-21-prompt-management-design.md` (commit `9d81349`).

**Reference design bundle (read-only, do not commit):** `/tmp/design/catdv-annotator/project/` — `screens.jsx` (TemplatesScreen, lines 199–370) is the source of truth for layout + classes; `styles.css` is the source for visual rules.

---

## File Structure

**Create:**
- `backend/migrations/0009_prompts_and_versions.sql` — schema + backfill
- `backend/app/models/prompt.py` — `Prompt`, `PromptVersion`, `PromptVersionState`, `TargetMap`, `TargetEntry`
- `backend/app/repositories/prompts.py` — `PromptsRepo`
- `backend/app/routes/prompts.py` — REST API `/api/prompts/*`
- `backend/app/templates/pages/prompts.html` — full page (2-col body)
- `backend/app/templates/pages/_prompts_list.html` — left-rail list partial
- `backend/app/templates/pages/_prompt_detail.html` — right-pane container (header inlined so Alpine x-data scope wraps the whole detail)
- `backend/app/templates/pages/_prompt_menu.html` — kebab popover
- `backend/app/templates/pages/_prompt_new.html` — new-prompt creation form
- `backend/app/templates/pages/_prompt_version_picker.html` — version dropdown
- `backend/app/templates/icons/_prompts.svg` — rail icon
- `backend/app/static/promptEditor.js` — Alpine component
- Tests: `tests/integration/test_migration_0009.py`, `tests/unit/test_prompt_models.py`, `tests/integration/test_prompts_repo.py`, `tests/integration/test_routes_prompts.py`, `tests/integration/test_routes_pages_prompts.py`

**Modify:**
- `backend/app/models/job.py` — `template_id` → `prompt_version_id`
- `backend/app/models/annotation.py` — `template_id` → `prompt_version_id`
- `backend/app/repositories/jobs.py` — column rename
- `backend/app/services/annotator.py` — load version via PromptsRepo
- `backend/app/services/target_map.py` — import `TargetMap`/`TargetEntry` from `models.prompt`
- `backend/app/services/write_queue.py` — same import change
- `backend/app/routes/review.py` — load version's `target_map` via PromptsRepo
- `backend/app/routes/jobs.py` — pass `prompts_repo` instead of `templates_repo`
- `backend/app/routes/pages.py` — add `/prompts*` handlers
- `backend/app/seed.py` — `seed_default_template` → `seed_default_prompt`
- `backend/app/context.py` — drop `templates_repo`, add `prompts_repo`
- `backend/app/main.py` — replace `templates_router` with `prompts_router`
- `backend/app/templates/pages/_rail.html` — add Prompts nav item
- `backend/app/static/app.css` — add Prompts page rules (port from design `styles.css`)
- Existing tests that import `from backend.app.models.template` or use `TemplatesRepo` — see Task 7 for the list.

**Delete:**
- `backend/app/models/template.py`
- `backend/app/repositories/templates.py`
- `backend/app/routes/templates.py`
- `tests/unit/test_template_models.py` (replaced by `test_prompt_models.py`)
- `tests/integration/test_templates_repo.py` (replaced by `test_prompts_repo.py`)
- `tests/integration/test_routes_templates.py` (replaced by `test_routes_prompts.py`)

---

## Task 1: Migration 0009 — schema + backfill + annotations/jobs rebuild

**Files:**
- Create: `backend/migrations/0009_prompts_and_versions.sql`
- Test: `tests/integration/test_migration_0009.py`

- [ ] **Step 1: Write the failing migration test**

Create `tests/integration/test_migration_0009.py`:

```python
"""Migration 0009 — prompts + prompt_versions, rewire annotations + jobs.

Boots a DB at the pre-0009 schema (migrations 0001-0008 only), seeds two
templates rows with referencing annotations + jobs, then applies 0009 and
asserts the new shape end-to-end.
"""
import json
from pathlib import Path

import aiosqlite
import pytest

from backend.app.db import open_db
from backend.app.migrations_runner import apply_migrations

MIGRATIONS = Path(__file__).resolve().parents[2] / "backend" / "migrations"


async def _apply_through_0008(db: aiosqlite.Connection) -> None:
    for path in sorted(MIGRATIONS.glob("*.sql")):
        if path.name >= "0009_":
            continue
        await db.executescript(path.read_text())
        await db.execute(
            "INSERT OR IGNORE INTO schema_migrations(name) VALUES (?)",
            (path.name,),
        )
    await db.commit()


@pytest.mark.asyncio
async def test_migration_0009_creates_tables_and_backfills(tmp_path: Path):
    db_path = tmp_path / "test.db"
    async with open_db(db_path) as db:
        # Pre-create the migrations meta table so direct executescript above
        # has somewhere to record names.
        await db.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations "
            "(name TEXT PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT (datetime('now')))"
        )
        await _apply_through_0008(db)

        # Seed two templates rows with all required fields.
        await db.execute(
            "INSERT INTO templates(name, description, prompt, output_schema, target_map, "
            "model, created_at, updated_at, archived) "
            "VALUES ('p1', 'd1', 'body1', ?, ?, 'gemini-2.5-pro', "
            "'2026-05-01T00:00:00+00:00', '2026-05-01T00:00:00+00:00', 0)",
            (json.dumps({"type": "object"}), json.dumps({"scenes": {"kind": "markers"}})),
        )
        await db.execute(
            "INSERT INTO templates(name, description, prompt, output_schema, target_map, "
            "model, created_at, updated_at, archived) "
            "VALUES ('p2', 'd2', 'body2', ?, ?, 'gemini-2.5-flash', "
            "'2026-05-02T00:00:00+00:00', '2026-05-02T00:00:00+00:00', 0)",
            (json.dumps({"type": "object"}), json.dumps({"scenes": {"kind": "markers"}})),
        )
        # Annotation referencing template id 1.
        await db.execute(
            "INSERT INTO annotations(catdv_clip_id, catdv_clip_name, template_id, "
            "model, prompt_used, raw_response, structured_output, clip_snapshot, created_at) "
            "VALUES (12041, 'c', 1, 'gemini-2.5-pro', 'body1', '{}', '{}', '{}', "
            "'2026-05-10T00:00:00+00:00')"
        )
        # Job referencing template id 2.
        await db.execute(
            "INSERT INTO jobs(template_id, status, created_at, total_clips) "
            "VALUES (2, 'pending', '2026-05-10T00:00:00+00:00', 0)"
        )
        await db.commit()

        # Now apply 0009.
        sql = (MIGRATIONS / "0009_prompts_and_versions.sql").read_text()
        await db.executescript(sql)
        await db.commit()

        # Assert prompts table.
        cur = await db.execute("SELECT id, name, description, archived FROM prompts ORDER BY id")
        rows = await cur.fetchall()
        assert rows == [(1, "p1", "d1", 0), (2, "p2", "d2", 0)]

        # Assert prompt_versions table — each prompt has v1@production.
        cur = await db.execute(
            "SELECT prompt_id, version_num, state, body, model FROM prompt_versions ORDER BY prompt_id"
        )
        rows = await cur.fetchall()
        assert rows == [
            (1, 1, "production", "body1", "gemini-2.5-pro"),
            (2, 1, "production", "body2", "gemini-2.5-flash"),
        ]

        # Partial unique index is in place.
        cur = await db.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_one_prod_per_prompt'"
        )
        assert (await cur.fetchone()) is not None

        # Annotation now points at prompt_versions.id.
        cur = await db.execute(
            "SELECT a.prompt_version_id, pv.prompt_id FROM annotations a "
            "JOIN prompt_versions pv ON pv.id = a.prompt_version_id"
        )
        row = await cur.fetchone()
        assert row is not None
        version_id, prompt_id = row
        assert prompt_id == 1

        # Job now points at prompt_versions.id.
        cur = await db.execute(
            "SELECT j.prompt_version_id, pv.prompt_id FROM jobs j "
            "JOIN prompt_versions pv ON pv.id = j.prompt_version_id"
        )
        row = await cur.fetchone()
        assert row is not None
        _, prompt_id = row
        assert prompt_id == 2

        # templates table is gone.
        cur = await db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='templates'"
        )
        assert (await cur.fetchone()) is None

        # annotations_fts still works after the rebuild.
        cur = await db.execute(
            "INSERT INTO annotations(catdv_clip_id, catdv_clip_name, prompt_version_id, "
            "model, prompt_used, raw_response, structured_output, clip_snapshot, created_at) "
            f"VALUES (12042, 'searchable', {version_id}, 'm', 'p', '{{}}', '{{}}', '{{}}', "
            "'2026-05-11T00:00:00+00:00')"
        )
        await db.commit()
        cur = await db.execute("SELECT rowid FROM annotations_fts WHERE annotations_fts MATCH 'searchable'")
        assert await cur.fetchone() is not None


@pytest.mark.asyncio
async def test_migration_0009_partial_unique_index_rejects_two_production(tmp_path: Path):
    db_path = tmp_path / "test.db"
    async with open_db(db_path) as db:
        await db.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations "
            "(name TEXT PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT (datetime('now')))"
        )
        await _apply_through_0008(db)
        sql = (MIGRATIONS / "0009_prompts_and_versions.sql").read_text()
        await db.executescript(sql)
        await db.commit()

        await db.execute(
            "INSERT INTO prompts(name, description, archived, created_at, updated_at) "
            "VALUES ('p', '', 0, '2026-05-01T00:00:00+00:00', '2026-05-01T00:00:00+00:00')"
        )
        await db.execute(
            "INSERT INTO prompt_versions(prompt_id, version_num, state, body, target_map, "
            "output_schema, model, created_at, updated_at) "
            "VALUES (1, 1, 'production', 'b', '{}', '{}', 'm', "
            "'2026-05-01T00:00:00+00:00', '2026-05-01T00:00:00+00:00')"
        )
        await db.commit()

        with pytest.raises(aiosqlite.IntegrityError):
            await db.execute(
                "INSERT INTO prompt_versions(prompt_id, version_num, state, body, target_map, "
                "output_schema, model, created_at, updated_at) "
                "VALUES (1, 2, 'production', 'b', '{}', '{}', 'm', "
                "'2026-05-01T00:00:00+00:00', '2026-05-01T00:00:00+00:00')"
            )
```

- [ ] **Step 2: Run test to verify it fails (file does not exist)**

Run: `.venv/bin/python -m pytest tests/integration/test_migration_0009.py -v`

Expected: FAIL — `0009_prompts_and_versions.sql` does not exist.

- [ ] **Step 3: Write the migration**

Create `backend/migrations/0009_prompts_and_versions.sql`:

```sql
-- 0009: Replace templates with prompts + prompt_versions, rewire annotations + jobs.
-- Migration is irreversible. Each existing templates row -> 1 prompt + 1 v1@production.

CREATE TABLE prompts (
  id            INTEGER PRIMARY KEY,
  name          TEXT NOT NULL UNIQUE,
  description   TEXT,
  archived      INTEGER NOT NULL DEFAULT 0,
  created_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL
);

CREATE TABLE prompt_versions (
  id              INTEGER PRIMARY KEY,
  prompt_id       INTEGER NOT NULL REFERENCES prompts(id) ON DELETE CASCADE,
  version_num     INTEGER NOT NULL,
  state           TEXT NOT NULL CHECK (state IN ('draft','production','archived')),
  body            TEXT NOT NULL,
  target_map      TEXT NOT NULL,
  output_schema   TEXT NOT NULL,
  model           TEXT NOT NULL,
  created_at      TEXT NOT NULL,
  updated_at      TEXT NOT NULL,
  UNIQUE (prompt_id, version_num)
);

CREATE UNIQUE INDEX idx_one_prod_per_prompt
  ON prompt_versions(prompt_id) WHERE state = 'production';

CREATE INDEX idx_prompt_versions_prompt ON prompt_versions(prompt_id);

-- Backfill: each templates row -> prompts + v1@production.
INSERT INTO prompts (id, name, description, archived, created_at, updated_at)
  SELECT id, name, description, archived, created_at, updated_at FROM templates;

INSERT INTO prompt_versions
  (prompt_id, version_num, state, body, target_map, output_schema, model, created_at, updated_at)
  SELECT id, 1, 'production', prompt, target_map, output_schema, model, created_at, updated_at
  FROM templates;

-- Rebuild annotations: template_id -> prompt_version_id.
-- (SQLite < 3.35 cannot drop columns; build new table, copy, swap.)
CREATE TABLE annotations_new (
  id                 INTEGER PRIMARY KEY,
  catdv_clip_id      INTEGER NOT NULL,
  catdv_clip_name    TEXT NOT NULL,
  prompt_version_id  INTEGER NOT NULL REFERENCES prompt_versions(id),
  job_id             INTEGER REFERENCES jobs(id),
  model              TEXT NOT NULL,
  prompt_used        TEXT NOT NULL,
  raw_response       TEXT NOT NULL,
  structured_output  TEXT NOT NULL,
  clip_snapshot      TEXT NOT NULL,
  created_at         TEXT NOT NULL
);
INSERT INTO annotations_new
  SELECT a.id, a.catdv_clip_id, a.catdv_clip_name,
         pv.id, a.job_id, a.model, a.prompt_used, a.raw_response,
         a.structured_output, a.clip_snapshot, a.created_at
  FROM annotations a
  JOIN prompt_versions pv ON pv.prompt_id = a.template_id AND pv.version_num = 1;

-- Drop FTS + triggers tied to the old annotations table.
DROP TRIGGER IF EXISTS annotations_ai;
DROP TRIGGER IF EXISTS annotations_ad;
DROP TABLE IF EXISTS annotations_fts;
DROP TABLE annotations;
ALTER TABLE annotations_new RENAME TO annotations;

CREATE INDEX idx_annotations_clip ON annotations(catdv_clip_id);
CREATE INDEX idx_annotations_prompt_version ON annotations(prompt_version_id);

CREATE VIRTUAL TABLE annotations_fts USING fts5(
  clip_name, prompt_used, structured_output, raw_response,
  content='annotations', content_rowid='id',
  tokenize = "unicode61 remove_diacritics 2"
);

CREATE TRIGGER annotations_ai AFTER INSERT ON annotations BEGIN
  INSERT INTO annotations_fts(rowid, clip_name, prompt_used, structured_output, raw_response)
  VALUES (new.id, new.catdv_clip_name, new.prompt_used, new.structured_output, new.raw_response);
END;

CREATE TRIGGER annotations_ad AFTER DELETE ON annotations BEGIN
  INSERT INTO annotations_fts(annotations_fts, rowid, clip_name, prompt_used, structured_output, raw_response)
  VALUES ('delete', old.id, old.catdv_clip_name, old.prompt_used, old.structured_output, old.raw_response);
END;

-- Rebuild jobs: template_id -> prompt_version_id.
CREATE TABLE jobs_new (
  id              INTEGER PRIMARY KEY,
  prompt_version_id INTEGER NOT NULL REFERENCES prompt_versions(id),
  status          TEXT NOT NULL,
  created_at      TEXT NOT NULL,
  started_at      TEXT,
  finished_at     TEXT,
  total_clips     INTEGER NOT NULL,
  notes           TEXT
);
INSERT INTO jobs_new (id, prompt_version_id, status, created_at, started_at, finished_at, total_clips, notes)
  SELECT j.id, pv.id, j.status, j.created_at, j.started_at, j.finished_at, j.total_clips, j.notes
  FROM jobs j
  JOIN prompt_versions pv ON pv.prompt_id = j.template_id AND pv.version_num = 1;
DROP TABLE jobs;
ALTER TABLE jobs_new RENAME TO jobs;

DROP TABLE templates;
```

- [ ] **Step 4: Run migration tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/integration/test_migration_0009.py -v`

Expected: both tests PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/migrations/0009_prompts_and_versions.sql tests/integration/test_migration_0009.py
git commit -m "feat(db): migration 0009 — prompts + prompt_versions, drop templates

Replaces single-row templates table with versioned prompts + prompt_versions.
Backfills each existing templates row as v1@production. Rebuilds annotations
and jobs to point at prompt_version_id. Partial unique index enforces at
most one production version per prompt."
```

---

## Task 2: Pydantic models — `Prompt`, `PromptVersion`, move `TargetMap`

**Files:**
- Create: `backend/app/models/prompt.py`
- Test: `tests/unit/test_prompt_models.py`
- Delete (in a later task — leave for now to keep tests passing): `backend/app/models/template.py`

- [ ] **Step 1: Write the failing model tests**

Create `tests/unit/test_prompt_models.py`:

```python
"""Pydantic models for Prompt + PromptVersion."""
import pytest

from backend.app.models.prompt import (
    Prompt,
    PromptVersion,
    PromptVersionState,
    TargetMap,
    TargetEntry,
)


def test_prompt_minimal():
    p = Prompt(name="Scenes", description="d", archived=False,
               created_at="2026-05-01T00:00:00+00:00",
               updated_at="2026-05-01T00:00:00+00:00")
    assert p.name == "Scenes"
    assert p.archived is False


def test_prompt_version_minimal():
    v = PromptVersion(
        prompt_id=1,
        version_num=1,
        state="draft",
        body="Hello",
        target_map={"scenes": {"kind": "markers"}},
        output_schema={"type": "object"},
        model="gemini-2.5-pro",
        created_at="2026-05-01T00:00:00+00:00",
        updated_at="2026-05-01T00:00:00+00:00",
    )
    assert v.state == "draft"
    assert v.target_map.fields["scenes"].kind == "markers"


def test_prompt_version_state_invalid_rejected():
    with pytest.raises(ValueError):
        PromptVersion(
            prompt_id=1, version_num=1, state="bogus", body="x",
            target_map={}, output_schema={}, model="m",
            created_at="t", updated_at="t",
        )


def test_target_map_field_requires_identifier():
    with pytest.raises(ValueError):
        TargetMap.model_validate({"x": {"kind": "field"}})


def test_target_map_note_requires_target():
    with pytest.raises(ValueError):
        TargetMap.model_validate({"x": {"kind": "note"}})


def test_target_entry_note_defaults_to_append():
    tm = TargetMap.model_validate({"s": {"kind": "note", "target": "notes"}})
    assert tm.fields["s"].mode == "append"


def test_prompt_version_state_literal():
    # Sanity: the type alias is what we expect.
    assert PromptVersionState.__args__ == ("draft", "production", "archived")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_prompt_models.py -v`

Expected: FAIL — `backend.app.models.prompt` does not exist.

- [ ] **Step 3: Write the models**

Create `backend/app/models/prompt.py`:

```python
"""Domain models for the prompts management feature.

A Prompt is the long-lived identity (name + description). A PromptVersion
is a snapshot of editable content (body + target_map + output_schema + model)
plus a state (draft / production / archived) that gates mutability.
"""
from typing import Any, Literal, get_args

from pydantic import BaseModel, ConfigDict, RootModel, model_validator


PromptVersionState = Literal["draft", "production", "archived"]
PROMPT_VERSION_STATES: tuple[str, ...] = get_args(PromptVersionState)


class TargetEntry(BaseModel):
    kind: Literal["markers", "field", "note"]
    identifier: str | None = None
    target: str | None = None
    mode: Literal["append", "replace"] = "append"

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def _check_required(self) -> "TargetEntry":
        if self.kind == "field" and not self.identifier:
            raise ValueError("kind=field requires 'identifier'")
        if self.kind == "note" and not self.target:
            raise ValueError("kind=note requires 'target'")
        return self


class TargetMap(RootModel[dict[str, TargetEntry]]):
    @property
    def fields(self) -> dict[str, TargetEntry]:
        return self.root


class Prompt(BaseModel):
    id: int | None = None
    name: str
    description: str | None = None
    archived: bool = False
    created_at: str
    updated_at: str

    model_config = ConfigDict(extra="allow")


class PromptVersion(BaseModel):
    id: int | None = None
    prompt_id: int
    version_num: int
    state: PromptVersionState
    body: str
    target_map: TargetMap
    output_schema: dict[str, Any]
    model: str
    created_at: str
    updated_at: str

    model_config = ConfigDict(extra="allow")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/test_prompt_models.py -v`

Expected: 6 PASS. (Old `tests/unit/test_template_models.py` still passes — both modules coexist for now.)

- [ ] **Step 5: Commit**

```bash
git add backend/app/models/prompt.py tests/unit/test_prompt_models.py
git commit -m "feat(models): Prompt + PromptVersion + TargetMap

Introduces the versioned-prompt domain model. TargetMap/TargetEntry move
here from models/template.py (semantics unchanged); old module stays
temporarily until annotator/jobs are rewired."
```

---

## Task 3: `PromptsRepo` part 1 — prompt-level CRUD + invariants (TDD)

**Files:**
- Create: `backend/app/repositories/prompts.py`
- Test: `tests/integration/test_prompts_repo.py`

- [ ] **Step 1: Write the failing repo tests (prompt-level)**

Create `tests/integration/test_prompts_repo.py`:

```python
"""PromptsRepo — prompt-level + version-level CRUD with invariants."""
import pytest

from backend.app.models.prompt import Prompt, PromptVersion
from backend.app.repositories.prompts import (
    PromptsRepo,
    VersionImmutableError,
)


def _vbody() -> dict:
    return {
        "body": "Identify scenes.",
        "target_map": {"scenes": {"kind": "markers"}},
        "output_schema": {"type": "object"},
        "model": "gemini-2.5-pro",
    }


@pytest.mark.asyncio
async def test_create_with_initial_version_yields_v1_draft(db):
    repo = PromptsRepo()
    prompt_id, version_id = await repo.create_with_initial_version(
        db, name="P1", description="d", **_vbody()
    )
    prompt, versions = await repo.get_with_versions(db, prompt_id)
    assert prompt.name == "P1"
    assert prompt.archived is False
    assert len(versions) == 1
    assert versions[0].id == version_id
    assert versions[0].version_num == 1
    assert versions[0].state == "draft"


@pytest.mark.asyncio
async def test_list_active_excludes_archived(db):
    repo = PromptsRepo()
    p1, _ = await repo.create_with_initial_version(db, name="A", description=None, **_vbody())
    p2, _ = await repo.create_with_initial_version(db, name="B", description=None, **_vbody())
    await repo.archive(db, p1)
    active = await repo.list_active(db)
    assert [p.name for p in active] == ["B"]
    archived = await repo.list_archived(db)
    assert [p.name for p in archived] == ["A"]


@pytest.mark.asyncio
async def test_archive_then_restore_idempotent(db):
    repo = PromptsRepo()
    pid, _ = await repo.create_with_initial_version(db, name="P", description=None, **_vbody())
    await repo.archive(db, pid)
    await repo.archive(db, pid)  # idempotent
    p, _ = await repo.get_with_versions(db, pid)
    assert p.archived is True
    await repo.restore(db, pid)
    await repo.restore(db, pid)  # idempotent
    p, _ = await repo.get_with_versions(db, pid)
    assert p.archived is False


@pytest.mark.asyncio
async def test_archive_preserves_version_states(db):
    repo = PromptsRepo()
    pid, vid = await repo.create_with_initial_version(db, name="P", description=None, **_vbody())
    await repo.promote_version(db, pid, vid)
    await repo.archive(db, pid)
    _, versions = await repo.get_with_versions(db, pid)
    assert versions[0].state == "production"


@pytest.mark.asyncio
async def test_update_metadata_name_and_description(db):
    repo = PromptsRepo()
    pid, _ = await repo.create_with_initial_version(db, name="Old", description="d1", **_vbody())
    await repo.update_metadata(db, pid, name="New", description="d2")
    p, _ = await repo.get_with_versions(db, pid)
    assert p.name == "New"
    assert p.description == "d2"


@pytest.mark.asyncio
async def test_update_metadata_unique_name_collision_raises(db):
    repo = PromptsRepo()
    await repo.create_with_initial_version(db, name="A", description=None, **_vbody())
    pid, _ = await repo.create_with_initial_version(db, name="B", description=None, **_vbody())
    import aiosqlite
    with pytest.raises(aiosqlite.IntegrityError):
        await repo.update_metadata(db, pid, name="A", description=None)


@pytest.mark.asyncio
async def test_get_version_returns_loaded_pydantic(db):
    repo = PromptsRepo()
    pid, vid = await repo.create_with_initial_version(db, name="P", description=None, **_vbody())
    v = await repo.get_version(db, vid)
    assert v.id == vid
    assert v.prompt_id == pid
    assert v.body == "Identify scenes."
    assert v.target_map.fields["scenes"].kind == "markers"


@pytest.mark.asyncio
async def test_get_version_unknown_raises_lookup(db):
    repo = PromptsRepo()
    with pytest.raises(LookupError):
        await repo.get_version(db, 9999)
```

(Tests for version operations come in Task 4.)

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/integration/test_prompts_repo.py -v`

Expected: FAIL — `backend.app.repositories.prompts` does not exist.

- [ ] **Step 3: Implement the repo (prompt-level only)**

Create `backend/app/repositories/prompts.py`:

```python
"""PromptsRepo — CRUD + state-machine for prompts and their versions.

Invariants (enforced here + by partial unique index `idx_one_prod_per_prompt`):
  * At most one production version per prompt.
  * Editing body/target_map/output_schema/model only when state='draft'.
  * Promoting a draft demotes the previous production to 'archived'
    atomically.
"""
import json
from datetime import datetime, timezone
from typing import Any

import aiosqlite

from backend.app.models.prompt import Prompt, PromptVersion


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class VersionImmutableError(RuntimeError):
    """Raised when caller tries to edit a non-draft version."""

    def __init__(self, version_id: int, state: str):
        super().__init__(f"version {version_id} is in state {state!r} and cannot be edited")
        self.version_id = version_id
        self.state = state


def _target_map_to_json(target_map: Any) -> str:
    """Accept dict OR a TargetMap model and produce a JSON string."""
    if hasattr(target_map, "model_dump_json"):
        return target_map.model_dump_json()
    return json.dumps(target_map)


def _row_to_prompt(row) -> Prompt:
    return Prompt(
        id=row[0], name=row[1], description=row[2],
        archived=bool(row[3]), created_at=row[4], updated_at=row[5],
    )


def _row_to_version(row) -> PromptVersion:
    return PromptVersion(
        id=row[0], prompt_id=row[1], version_num=row[2], state=row[3],
        body=row[4], target_map=json.loads(row[5]),
        output_schema=json.loads(row[6]), model=row[7],
        created_at=row[8], updated_at=row[9],
    )


_PROMPT_COLS = "id, name, description, archived, created_at, updated_at"
_VERSION_COLS = (
    "id, prompt_id, version_num, state, body, target_map, "
    "output_schema, model, created_at, updated_at"
)


class PromptsRepo:
    # ── prompt-level ────────────────────────────────────────────────────────

    async def create_with_initial_version(
        self,
        conn: aiosqlite.Connection,
        *,
        name: str,
        description: str | None,
        body: str,
        target_map: Any,
        output_schema: Any,
        model: str,
        initial_state: str = "draft",
    ) -> tuple[int, int]:
        """Create prompt + v1. Returns (prompt_id, version_id)."""
        now = _now_iso()
        cur = await conn.execute(
            "INSERT INTO prompts(name, description, archived, created_at, updated_at) "
            "VALUES (?, ?, 0, ?, ?)",
            (name, description, now, now),
        )
        prompt_id = cur.lastrowid
        assert prompt_id is not None
        cur = await conn.execute(
            "INSERT INTO prompt_versions(prompt_id, version_num, state, body, target_map, "
            "output_schema, model, created_at, updated_at) "
            "VALUES (?, 1, ?, ?, ?, ?, ?, ?, ?)",
            (
                prompt_id, initial_state, body,
                _target_map_to_json(target_map), json.dumps(output_schema),
                model, now, now,
            ),
        )
        version_id = cur.lastrowid
        assert version_id is not None
        await conn.commit()
        return prompt_id, version_id

    async def get_with_versions(
        self, conn: aiosqlite.Connection, prompt_id: int
    ) -> tuple[Prompt, list[PromptVersion]]:
        cur = await conn.execute(
            f"SELECT {_PROMPT_COLS} FROM prompts WHERE id = ?", (prompt_id,)
        )
        prow = await cur.fetchone()
        if prow is None:
            raise LookupError(f"prompt {prompt_id} not found")
        cur = await conn.execute(
            f"SELECT {_VERSION_COLS} FROM prompt_versions "
            "WHERE prompt_id = ? ORDER BY version_num DESC",
            (prompt_id,),
        )
        versions = [_row_to_version(r) for r in await cur.fetchall()]
        return _row_to_prompt(prow), versions

    async def list_active(self, conn: aiosqlite.Connection) -> list[Prompt]:
        cur = await conn.execute(
            f"SELECT {_PROMPT_COLS} FROM prompts WHERE archived = 0 ORDER BY name"
        )
        return [_row_to_prompt(r) for r in await cur.fetchall()]

    async def list_archived(self, conn: aiosqlite.Connection) -> list[Prompt]:
        cur = await conn.execute(
            f"SELECT {_PROMPT_COLS} FROM prompts WHERE archived = 1 ORDER BY name"
        )
        return [_row_to_prompt(r) for r in await cur.fetchall()]

    async def update_metadata(
        self,
        conn: aiosqlite.Connection,
        prompt_id: int,
        *,
        name: str | None = None,
        description: str | None = None,
    ) -> None:
        sets, args = [], []
        if name is not None:
            sets.append("name = ?")
            args.append(name)
        if description is not None:
            sets.append("description = ?")
            args.append(description)
        if not sets:
            return
        sets.append("updated_at = ?")
        args.append(_now_iso())
        args.append(prompt_id)
        await conn.execute(f"UPDATE prompts SET {', '.join(sets)} WHERE id = ?", args)
        await conn.commit()

    async def archive(self, conn: aiosqlite.Connection, prompt_id: int) -> None:
        await conn.execute(
            "UPDATE prompts SET archived = 1, updated_at = ? WHERE id = ?",
            (_now_iso(), prompt_id),
        )
        await conn.commit()

    async def restore(self, conn: aiosqlite.Connection, prompt_id: int) -> None:
        await conn.execute(
            "UPDATE prompts SET archived = 0, updated_at = ? WHERE id = ?",
            (_now_iso(), prompt_id),
        )
        await conn.commit()

    # ── version-level (Task 4 fills in create_version, update_version, promote, duplicate) ──

    async def get_version(
        self, conn: aiosqlite.Connection, version_id: int
    ) -> PromptVersion:
        cur = await conn.execute(
            f"SELECT {_VERSION_COLS} FROM prompt_versions WHERE id = ?",
            (version_id,),
        )
        row = await cur.fetchone()
        if row is None:
            raise LookupError(f"prompt_version {version_id} not found")
        return _row_to_version(row)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/integration/test_prompts_repo.py -v`

Expected: 8 PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/repositories/prompts.py tests/integration/test_prompts_repo.py
git commit -m "feat(repo): PromptsRepo — prompt-level CRUD + get_version

Introduces the PromptsRepo with prompt-level operations (create, list,
get, update_metadata, archive, restore) and the version lookup primitive.
Version mutation operations (create_version, update_version, promote,
duplicate) follow in the next task."
```

---

## Task 4: `PromptsRepo` part 2 — version operations + atomic promote (TDD)

**Files:**
- Modify: `backend/app/repositories/prompts.py` — extend the class
- Modify: `tests/integration/test_prompts_repo.py` — add version-op tests

- [ ] **Step 1: Append failing tests to `tests/integration/test_prompts_repo.py`**

Append:

```python
# ── version operations ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_version_default_clones_current_production(db):
    repo = PromptsRepo()
    pid, v1 = await repo.create_with_initial_version(db, name="P", description=None, **_vbody())
    await repo.promote_version(db, pid, v1)
    new_vid = await repo.create_version(db, pid)
    new_v = await repo.get_version(db, new_vid)
    assert new_v.version_num == 2
    assert new_v.state == "draft"
    assert new_v.body == "Identify scenes."  # cloned


@pytest.mark.asyncio
async def test_create_version_fallback_to_latest_when_no_production(db):
    repo = PromptsRepo()
    pid, v1 = await repo.create_with_initial_version(db, name="P", description=None, **_vbody())
    # v1 is draft; no production exists.
    new_vid = await repo.create_version(db, pid)
    new_v = await repo.get_version(db, new_vid)
    assert new_v.version_num == 2
    assert new_v.state == "draft"


@pytest.mark.asyncio
async def test_create_version_explicit_from_version_id(db):
    repo = PromptsRepo()
    pid, v1 = await repo.create_with_initial_version(db, name="P", description=None, **_vbody())
    body2 = {**_vbody(), "body": "v2 body"}
    await repo.update_version(db, v1, **body2)  # still draft, mutate
    await repo.promote_version(db, pid, v1)  # v1 production
    v2_id = await repo.create_version(db, pid)
    await repo.update_version(db, v2_id, body="v2-edited", target_map=body2["target_map"],
                              output_schema=body2["output_schema"], model=body2["model"])
    v3_id = await repo.create_version(db, pid, from_version_id=v1)
    assert (await repo.get_version(db, v3_id)).body == "v2 body"


@pytest.mark.asyncio
async def test_update_version_on_draft_persists(db):
    repo = PromptsRepo()
    _, vid = await repo.create_with_initial_version(db, name="P", description=None, **_vbody())
    await repo.update_version(db, vid, body="new body",
                              target_map={"s": {"kind": "markers"}},
                              output_schema={"type": "object"}, model="gemini-2.5-flash")
    v = await repo.get_version(db, vid)
    assert v.body == "new body"
    assert v.model == "gemini-2.5-flash"


@pytest.mark.asyncio
async def test_update_version_on_production_raises(db):
    repo = PromptsRepo()
    pid, vid = await repo.create_with_initial_version(db, name="P", description=None, **_vbody())
    await repo.promote_version(db, pid, vid)
    with pytest.raises(VersionImmutableError) as excinfo:
        await repo.update_version(db, vid, body="x", target_map={},
                                  output_schema={}, model="m")
    assert excinfo.value.state == "production"


@pytest.mark.asyncio
async def test_update_version_on_archived_raises(db):
    repo = PromptsRepo()
    pid, v1 = await repo.create_with_initial_version(db, name="P", description=None, **_vbody())
    await repo.promote_version(db, pid, v1)
    v2 = await repo.create_version(db, pid)
    await repo.promote_version(db, pid, v2)  # v1 now archived
    v1_state = (await repo.get_version(db, v1)).state
    assert v1_state == "archived"
    with pytest.raises(VersionImmutableError):
        await repo.update_version(db, v1, body="x", target_map={},
                                  output_schema={}, model="m")


@pytest.mark.asyncio
async def test_promote_demotes_previous_production(db):
    repo = PromptsRepo()
    pid, v1 = await repo.create_with_initial_version(db, name="P", description=None, **_vbody())
    await repo.promote_version(db, pid, v1)
    v2 = await repo.create_version(db, pid)
    await repo.promote_version(db, pid, v2)
    assert (await repo.get_version(db, v1)).state == "archived"
    assert (await repo.get_version(db, v2)).state == "production"


@pytest.mark.asyncio
async def test_promote_only_one_production_per_prompt(db):
    """Sanity: the partial unique index actually fires under repo.promote."""
    repo = PromptsRepo()
    pid, v1 = await repo.create_with_initial_version(db, name="P", description=None, **_vbody())
    await repo.promote_version(db, pid, v1)
    v2 = await repo.create_version(db, pid)
    await repo.promote_version(db, pid, v2)
    # After promotes there is exactly one row with state='production'.
    cur = await db.execute(
        "SELECT COUNT(*) FROM prompt_versions WHERE prompt_id = ? AND state = 'production'",
        (pid,),
    )
    assert (await cur.fetchone())[0] == 1


@pytest.mark.asyncio
async def test_version_num_monotonic(db):
    repo = PromptsRepo()
    pid, _ = await repo.create_with_initial_version(db, name="P", description=None, **_vbody())
    v2 = await repo.create_version(db, pid)
    v3 = await repo.create_version(db, pid)
    assert (await repo.get_version(db, v2)).version_num == 2
    assert (await repo.get_version(db, v3)).version_num == 3


@pytest.mark.asyncio
async def test_duplicate_copies_current_production_into_new_prompt_draft(db):
    repo = PromptsRepo()
    pid, v1 = await repo.create_with_initial_version(db, name="P", description="d", **_vbody())
    await repo.promote_version(db, pid, v1)
    new_pid, new_vid = await repo.duplicate(db, pid)
    new_prompt, versions = await repo.get_with_versions(db, new_pid)
    assert new_prompt.name == "Copy of P"
    assert new_prompt.description == "d"
    assert len(versions) == 1
    assert versions[0].id == new_vid
    assert versions[0].state == "draft"
    assert versions[0].body == "Identify scenes."


@pytest.mark.asyncio
async def test_duplicate_walks_past_existing_copy_names(db):
    repo = PromptsRepo()
    pid, _ = await repo.create_with_initial_version(db, name="P", description=None, **_vbody())
    await repo.duplicate(db, pid)   # creates "Copy of P"
    pid2, _ = await repo.duplicate(db, pid)
    p, _ = await repo.get_with_versions(db, pid2)
    assert p.name == "Copy of P (2)"


@pytest.mark.asyncio
async def test_duplicate_skips_archived_name_collisions(db):
    repo = PromptsRepo()
    pid, _ = await repo.create_with_initial_version(db, name="P", description=None, **_vbody())
    copy1, _ = await repo.duplicate(db, pid)
    await repo.archive(db, copy1)  # archived but UNIQUE still applies
    pid3, _ = await repo.duplicate(db, pid)
    p, _ = await repo.get_with_versions(db, pid3)
    assert p.name == "Copy of P (2)"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/integration/test_prompts_repo.py -v`

Expected: 12 FAIL (the new ones — `create_version`, `update_version`, `promote_version`, `duplicate` don't exist yet).

- [ ] **Step 3: Extend `PromptsRepo` with version operations**

Edit `backend/app/repositories/prompts.py`. Add the following methods to `PromptsRepo` (after `get_version`):

```python
    async def _current_production_id(
        self, conn: aiosqlite.Connection, prompt_id: int
    ) -> int | None:
        cur = await conn.execute(
            "SELECT id FROM prompt_versions "
            "WHERE prompt_id = ? AND state = 'production' LIMIT 1",
            (prompt_id,),
        )
        row = await cur.fetchone()
        return row[0] if row else None

    async def _latest_version_id(
        self, conn: aiosqlite.Connection, prompt_id: int
    ) -> int | None:
        cur = await conn.execute(
            "SELECT id FROM prompt_versions WHERE prompt_id = ? "
            "ORDER BY version_num DESC LIMIT 1",
            (prompt_id,),
        )
        row = await cur.fetchone()
        return row[0] if row else None

    async def _max_version_num(
        self, conn: aiosqlite.Connection, prompt_id: int
    ) -> int:
        cur = await conn.execute(
            "SELECT COALESCE(MAX(version_num), 0) FROM prompt_versions WHERE prompt_id = ?",
            (prompt_id,),
        )
        row = await cur.fetchone()
        return int(row[0]) if row else 0

    async def create_version(
        self,
        conn: aiosqlite.Connection,
        prompt_id: int,
        *,
        from_version_id: int | None = None,
    ) -> int:
        """Clone a source version into a new draft. Returns new version_id.

        Source selection: explicit from_version_id > current production > latest.
        """
        if from_version_id is None:
            from_version_id = (
                await self._current_production_id(conn, prompt_id)
                or await self._latest_version_id(conn, prompt_id)
            )
        if from_version_id is None:
            raise LookupError(f"prompt {prompt_id} has no versions to clone from")
        src = await self.get_version(conn, from_version_id)
        next_num = (await self._max_version_num(conn, prompt_id)) + 1
        now = _now_iso()
        cur = await conn.execute(
            "INSERT INTO prompt_versions(prompt_id, version_num, state, body, "
            "target_map, output_schema, model, created_at, updated_at) "
            "VALUES (?, ?, 'draft', ?, ?, ?, ?, ?, ?)",
            (
                prompt_id, next_num, src.body,
                _target_map_to_json(src.target_map),
                json.dumps(src.output_schema), src.model, now, now,
            ),
        )
        new_id = cur.lastrowid
        assert new_id is not None
        await conn.commit()
        return new_id

    async def update_version(
        self,
        conn: aiosqlite.Connection,
        version_id: int,
        *,
        body: str,
        target_map: Any,
        output_schema: Any,
        model: str,
    ) -> None:
        v = await self.get_version(conn, version_id)
        if v.state != "draft":
            raise VersionImmutableError(version_id, v.state)
        await conn.execute(
            "UPDATE prompt_versions SET body = ?, target_map = ?, output_schema = ?, "
            "model = ?, updated_at = ? WHERE id = ?",
            (
                body, _target_map_to_json(target_map), json.dumps(output_schema),
                model, _now_iso(), version_id,
            ),
        )
        await conn.commit()

    async def promote_version(
        self, conn: aiosqlite.Connection, prompt_id: int, version_id: int
    ) -> None:
        """Atomically demote current production -> 'archived', set target -> 'production'."""
        now = _now_iso()
        # The partial unique index forbids two production rows existing at the
        # same instant, so we MUST archive the old one before promoting the
        # new one. Single transaction.
        try:
            await conn.execute("BEGIN")
            await conn.execute(
                "UPDATE prompt_versions SET state = 'archived', updated_at = ? "
                "WHERE prompt_id = ? AND state = 'production' AND id != ?",
                (now, prompt_id, version_id),
            )
            await conn.execute(
                "UPDATE prompt_versions SET state = 'production', updated_at = ? "
                "WHERE id = ? AND prompt_id = ?",
                (now, version_id, prompt_id),
            )
            await conn.execute("COMMIT")
        except Exception:
            await conn.execute("ROLLBACK")
            raise

    async def duplicate(
        self, conn: aiosqlite.Connection, prompt_id: int
    ) -> tuple[int, int]:
        """Create a new prompt 'Copy of <name>' with v1 cloned from source's
        current production (fallback: latest). Walks past existing names.
        Returns (new_prompt_id, new_version_id).
        """
        src_prompt, _ = await self.get_with_versions(conn, prompt_id)
        src_version_id = (
            await self._current_production_id(conn, prompt_id)
            or await self._latest_version_id(conn, prompt_id)
        )
        assert src_version_id is not None  # invariant: every prompt has >=1 version
        src_version = await self.get_version(conn, src_version_id)
        new_name = await self._next_copy_name(conn, src_prompt.name)
        return await self.create_with_initial_version(
            conn,
            name=new_name,
            description=src_prompt.description,
            body=src_version.body,
            target_map=src_version.target_map,
            output_schema=src_version.output_schema,
            model=src_version.model,
            initial_state="draft",
        )

    async def _next_copy_name(self, conn: aiosqlite.Connection, src_name: str) -> str:
        base = f"Copy of {src_name}"
        candidate = base
        n = 2
        while True:
            cur = await conn.execute("SELECT 1 FROM prompts WHERE name = ?", (candidate,))
            if (await cur.fetchone()) is None:
                return candidate
            candidate = f"{base} ({n})"
            n += 1
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/integration/test_prompts_repo.py -v`

Expected: all 20 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/repositories/prompts.py tests/integration/test_prompts_repo.py
git commit -m "feat(repo): PromptsRepo — version create/edit/promote/duplicate

Production is immutable (update_version raises VersionImmutableError on
non-draft). Promote atomically demotes the previous production to
archived. Duplicate clones the current production into a new prompt
named 'Copy of X', walking past existing collisions."
```

---

## Task 5: Rewire `Job` + `Annotation` models + repos

**Files:**
- Modify: `backend/app/models/job.py`
- Modify: `backend/app/models/annotation.py`
- Modify: `backend/app/repositories/jobs.py`

**No test changes here** — the rename is purely mechanical. The existing test suite breaks at this point; it gets fixed in Tasks 6–7.

- [ ] **Step 1: Edit `backend/app/models/job.py`**

Replace `template_id: int` with `prompt_version_id: int` in class `Job`:

```python
class Job(BaseModel):
    id: int | None = None
    prompt_version_id: int
    status: JobStatus = "pending"
    total_clips: int
    notes: str | None = None
```

- [ ] **Step 2: Edit `backend/app/models/annotation.py`**

Replace `template_id: int` with `prompt_version_id: int` in class `Annotation`:

```python
class Annotation(BaseModel):
    id: int | None = None
    catdv_clip_id: int
    catdv_clip_name: str
    prompt_version_id: int
    job_id: int | None = None
    model: str
    prompt_used: str
    raw_response: dict[str, Any]
    structured_output: dict[str, Any] | None
    clip_snapshot: dict[str, Any]
```

- [ ] **Step 3: Edit `backend/app/repositories/jobs.py`**

In every SQL string and method signature, replace `template_id` with `prompt_version_id`. Updated `create_job` signature:

```python
    async def create_job(
        self, conn: aiosqlite.Connection, *, prompt_version_id: int, clip_ids: list[int]
    ) -> int:
        cur = await conn.execute(
            """
            INSERT INTO jobs (prompt_version_id, status, created_at, total_clips)
            VALUES (?, 'pending', ?, ?)
            """,
            (prompt_version_id, _now_iso(), len(clip_ids)),
        )
```

And `get_job` + `list_jobs`:

```python
    async def get_job(self, conn: aiosqlite.Connection, job_id: int) -> Job:
        cur = await conn.execute(
            "SELECT id, prompt_version_id, status, total_clips, notes FROM jobs WHERE id = ?",
            (job_id,),
        )
        row = await cur.fetchone()
        if row is None:
            raise LookupError(f"job {job_id} not found")
        return Job(id=row[0], prompt_version_id=row[1], status=row[2],
                   total_clips=row[3], notes=row[4])

    async def list_jobs(self, conn: aiosqlite.Connection, *, limit: int = 50) -> list[Job]:
        cur = await conn.execute(
            "SELECT id, prompt_version_id, status, total_clips, notes "
            "FROM jobs ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        return [
            Job(id=r[0], prompt_version_id=r[1], status=r[2],
                total_clips=r[3], notes=r[4])
            for r in await cur.fetchall()
        ]
```

Also do the same equivalent rename in `backend/app/repositories/annotations.py` — find every `template_id` and replace with `prompt_version_id` (SQL string + any column lookup by name).

- [ ] **Step 4: Commit (tests will be broken — that's expected)**

```bash
git add backend/app/models/job.py backend/app/models/annotation.py \
        backend/app/repositories/jobs.py backend/app/repositories/annotations.py
git commit -m "refactor(models): jobs+annotations use prompt_version_id

Renames template_id -> prompt_version_id in Job, Annotation, JobsRepo,
AnnotationsRepo. Wider test suite is broken at this commit and gets
fixed in the next two tasks."
```

---

## Task 6: Rewire annotator service + write_queue + target_map imports

**Files:**
- Modify: `backend/app/services/annotator.py`
- Modify: `backend/app/services/target_map.py`
- Modify: `backend/app/services/write_queue.py`
- Test: `tests/integration/test_annotator_worker.py` (update fixtures)

- [ ] **Step 1: Edit `backend/app/services/target_map.py`**

Change the import line:

```python
# was: from backend.app.models.template import TargetEntry, TargetMap
from backend.app.models.prompt import TargetEntry, TargetMap
```

- [ ] **Step 2: Edit `backend/app/services/write_queue.py`**

Change the import line:

```python
# was: from backend.app.models.template import TargetMap
from backend.app.models.prompt import TargetMap
```

- [ ] **Step 3: Rewrite `backend/app/services/annotator.py`**

Full file:

```python
import json
import logging
import mimetypes
from pathlib import Path
from typing import Any

import aiosqlite

from backend.app.archive.ai_store import AIInputStore
from backend.app.models.annotation import Annotation
from backend.app.repositories.annotations import AnnotationsRepo
from backend.app.repositories.jobs import JobsRepo
from backend.app.repositories.prompts import PromptsRepo
from backend.app.repositories.review_items import ReviewItemsRepo
from backend.app.services.events import EventBus
from backend.app.services.target_map import expand

log = logging.getLogger(__name__)


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
) -> None:
    """Run a job to completion (or cancellation). Serial per job."""
    job = await jobs_repo.get_job(db, job_id)
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
                archive=archive,
                proxy_resolver=proxy_resolver,
                ai_store=ai_store,
                gemini=gemini,
                annotations_repo=annotations_repo,
                review_items_repo=review_items_repo,
                jobs_repo=jobs_repo,
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

    await jobs_repo.update_item_status(db, item.id, "prompting")
    await event_bus.publish(topic, {"item_id": item.id, "status": "prompting"})
    result = gemini.annotate(
        file_ref=file_ref,
        prompt=version.body,
        schema=version.output_schema,
        model=version.model,
    )

    structured: dict[str, Any] | None
    try:
        structured = json.loads(result["text"]) if result.get("text") else None
    except json.JSONDecodeError:
        structured = None

    annotation_id = await annotations_repo.insert(
        db,
        Annotation(
            catdv_clip_id=item.catdv_clip_id,
            catdv_clip_name=clip_snapshot.get("name", ""),
            prompt_version_id=version.id,
            job_id=item.job_id,
            model=version.model,
            prompt_used=version.body,
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
        )
        if review:
            await review_items_repo.bulk_insert(db, review)

    await jobs_repo.update_item_status(db, item.id, "review_ready")
    await event_bus.publish(
        topic, {"item_id": item.id, "status": "review_ready", "annotation_id": annotation_id}
    )
```

- [ ] **Step 4: Fix the annotator integration test**

Open `tests/integration/test_annotator_worker.py`. Replace every reference to `TemplatesRepo`/`Template`/`template_id` with `PromptsRepo`/`PromptVersion`/`prompt_version_id`. The pattern:

Where the test creates a template:
```python
# was
templates = TemplatesRepo()
tid = await templates.create(db, Template(name=..., prompt=..., ...))
job_id = await jobs.create_job(db, template_id=tid, clip_ids=[...])
```

becomes:
```python
prompts = PromptsRepo()
_, vid = await prompts.create_with_initial_version(
    db, name="t", description=None,
    body="p", target_map={"scenes": {"kind": "markers"}},
    output_schema={"type": "object"}, model="m",
)
job_id = await jobs.create_job(db, prompt_version_id=vid, clip_ids=[...])
```

And in any `run_job(...)` call, replace the kwarg `templates_repo=templates` with `prompts_repo=prompts`.

- [ ] **Step 5: Run the annotator test to verify it passes**

Run: `.venv/bin/python -m pytest tests/integration/test_annotator_worker.py -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/annotator.py backend/app/services/target_map.py \
        backend/app/services/write_queue.py tests/integration/test_annotator_worker.py
git commit -m "refactor(annotator): load version via PromptsRepo

Annotator now resolves job.prompt_version_id through PromptsRepo and
uses the version's body/target_map/output_schema/model directly.
TargetMap is re-imported from models.prompt."
```

---

## Task 7: Drop templates.* + wire context/seed/main + fix remaining tests

**Files:**
- Modify: `backend/app/seed.py`
- Modify: `backend/app/context.py`
- Modify: `backend/app/main.py`
- Modify: `backend/app/routes/jobs.py`
- Modify: `backend/app/routes/review.py`
- Delete: `backend/app/models/template.py`
- Delete: `backend/app/repositories/templates.py`
- Delete: `backend/app/routes/templates.py`
- Delete: `tests/unit/test_template_models.py`
- Delete: `tests/integration/test_templates_repo.py`
- Delete: `tests/integration/test_routes_templates.py`
- Modify: `tests/integration/test_review_items_repo.py` (the helper at top uses TemplatesRepo)
- Modify: `tests/integration/test_offline_cycle_e2e.py` (the helper at top uses TemplatesRepo)
- Modify: `tests/unit/test_target_map.py` (imports from models.template)
- Modify: `tests/integration/test_routes_jobs.py` (constructs jobs with template_id)

- [ ] **Step 1: Rewrite `backend/app/seed.py`**

```python
import json
from pathlib import Path

import aiosqlite

from backend.app.repositories.prompts import PromptsRepo


async def seed_default_prompt(conn: aiosqlite.Connection, *, seed_path: Path) -> None:
    """Insert the default prompt + v1@production if no prompt by that name exists."""
    raw = seed_path.read_text()  # noqa: ASYNC240  # sync read at startup is acceptable in lifespan
    data = json.loads(raw)
    cur = await conn.execute("SELECT 1 FROM prompts WHERE name = ?", (data["name"],))
    if await cur.fetchone():
        return
    repo = PromptsRepo()
    await repo.create_with_initial_version(
        conn,
        name=data["name"],
        description=data.get("description"),
        body=data["prompt"],
        target_map=data["target_map"],
        output_schema=data["output_schema"],
        model=data["model"],
        initial_state="production",
    )
```

- [ ] **Step 2: Edit `backend/app/context.py`**

Change the import:

```python
# was: from backend.app.repositories.templates import TemplatesRepo
from backend.app.repositories.prompts import PromptsRepo
```

In `AppContext` dataclass, replace the line:

```python
    # was: templates_repo: TemplatesRepo = field(default_factory=TemplatesRepo)
    prompts_repo: PromptsRepo = field(default_factory=PromptsRepo)
```

- [ ] **Step 3: Edit `backend/app/main.py`**

In two places:

```python
# was: from backend.app.seed import seed_default_template
from backend.app.seed import seed_default_prompt
```

```python
# was inside lifespan:
#   await seed_default_template(ctx.db, seed_path=seed_path)
await seed_default_prompt(ctx.db, seed_path=seed_path)
```

And:

```python
# was: from backend.app.routes.templates import router as templates_router
# was: app.include_router(templates_router)
from backend.app.routes.prompts import router as prompts_router
app.include_router(prompts_router)
```

(The prompts router file is created in Task 8. To keep this commit's import resolvable, add a one-line stub now: see Step 4.)

- [ ] **Step 4: Add a placeholder `backend/app/routes/prompts.py`**

```python
"""Prompts REST API — extended in a later task."""
from fastapi import APIRouter

router = APIRouter(prefix="/api/prompts", tags=["prompts"])
```

- [ ] **Step 5: Edit `backend/app/routes/jobs.py`**

Find the line that passes `templates_repo` to the annotator and replace with `prompts_repo`:

```python
# In the run_job(...) call inside this file:
#   templates_repo=ctx.templates_repo,
prompts_repo=ctx.prompts_repo,
```

If `routes/jobs.py` has a `POST /api/jobs` route accepting `template_id`, rename the request field to `prompt_version_id` (same rename in any Pydantic body model and DB call into `jobs_repo.create_job`).

- [ ] **Step 6: Edit `backend/app/routes/review.py`**

Replace the `apply_clip` handler's template lookup. Current:

```python
template = await ctx.templates_repo.get(ctx.db, annotation.template_id)
...
target_map=template.target_map,
```

New:

```python
version = await ctx.prompts_repo.get_version(ctx.db, annotation.prompt_version_id)
...
target_map=version.target_map,
```

- [ ] **Step 7: Delete the templates files**

```bash
git rm backend/app/models/template.py \
       backend/app/repositories/templates.py \
       backend/app/routes/templates.py \
       tests/unit/test_template_models.py \
       tests/integration/test_templates_repo.py \
       tests/integration/test_routes_templates.py
```

- [ ] **Step 8: Fix remaining test imports**

In `tests/unit/test_target_map.py`: change `from backend.app.models.template import TargetMap` → `from backend.app.models.prompt import TargetMap`.

In `tests/integration/test_review_items_repo.py`: rewrite the `_seed_annotation` helper:

```python
from backend.app.repositories.prompts import PromptsRepo
from backend.app.models.annotation import Annotation, ReviewItem
from backend.app.repositories.annotations import AnnotationsRepo
from backend.app.repositories.review_items import ReviewItemsRepo


async def _seed_annotation(db):
    prompts = PromptsRepo()
    _, vid = await prompts.create_with_initial_version(
        db, name="t", description=None,
        body="p", target_map={"x": {"kind": "markers"}},
        output_schema={}, model="m",
    )
    annotations = AnnotationsRepo()
    return await annotations.insert(
        db,
        Annotation(
            catdv_clip_id=1,
            catdv_clip_name="c",
            prompt_version_id=vid,
            model="m",
            prompt_used="p",
            raw_response={},
            structured_output={},
            clip_snapshot={},
        ),
    )
```

In `tests/integration/test_offline_cycle_e2e.py`: same shape — `_seed_template_and_annotations` becomes `_seed_prompt_and_annotations`, uses `PromptsRepo.create_with_initial_version`, and the annotation insert sets `prompt_version_id=vid` instead of `template_id=tid`.

In `tests/integration/test_routes_jobs.py`: any POST body or `create_job` invocation using `template_id` becomes `prompt_version_id`; any setup that creates a template via `TemplatesRepo` uses `PromptsRepo.create_with_initial_version` to get a version id.

- [ ] **Step 9: Run the full test suite**

Run: `.venv/bin/python -m pytest -q`

Expected: All tests pass. (Many tests are unaffected; the modified ones should pass against the rewired services.)

If a test still fails with `ImportError: cannot import name 'TemplatesRepo'`, grep for the residual reference and fix:

```bash
/usr/bin/grep -rn 'TemplatesRepo\|from backend.app.models.template\|templates_repo\|template_id' \
    backend/ tests/ --include='*.py' | /usr/bin/grep -v __pycache__
```

After the fix run `.venv/bin/python -m pytest -q` again.

- [ ] **Step 10: Commit**

```bash
git add -A
git commit -m "refactor: drop templates module, wire PromptsRepo

Deletes models/template.py, repositories/templates.py, routes/templates.py
and their tests. Seed becomes seed_default_prompt. AppContext exposes
prompts_repo. Review apply path now resolves the version through
PromptsRepo. All remaining tests pass."
```

---

## Task 8: REST API routes — `/api/prompts/*` (TDD)

**Files:**
- Modify: `backend/app/routes/prompts.py` (replace the stub from Task 7)
- Test: `tests/integration/test_routes_prompts.py`

- [ ] **Step 1: Write the failing route tests**

Create `tests/integration/test_routes_prompts.py`:

```python
"""REST routes for /api/prompts."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.context import AppContext
from backend.app.main import app
from backend.app.settings import Settings


@pytest.fixture
async def client(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.delenv("CATDV_PASSWORD", raising=False)
    settings = Settings()
    ctx = await AppContext.build(settings, init_external=False)
    app.state.ctx = ctx
    with TestClient(app) as c:
        yield c
    await ctx.aclose()


def _new_body(**over):
    base = {
        "name": "P1",
        "description": "d",
        "body": "Identify scenes.",
        "target_map": {"scenes": {"kind": "markers"}},
        "output_schema": {"type": "object"},
        "model": "gemini-2.5-pro",
    }
    base.update(over)
    return base


def test_create_and_get(client):
    r = client.post("/api/prompts", json=_new_body())
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    r = client.get(f"/api/prompts/{pid}")
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "P1"
    assert len(body["versions"]) == 1
    assert body["versions"][0]["state"] == "draft"
    assert body["current_production_version_id"] is None
    assert body["latest_version_id"] == body["versions"][0]["id"]


def test_list_active_excludes_archived(client):
    a = client.post("/api/prompts", json=_new_body(name="A")).json()["id"]
    client.post("/api/prompts", json=_new_body(name="B"))
    r = client.post(f"/api/prompts/{a}:archive")
    assert r.status_code == 200
    rows = client.get("/api/prompts").json()
    assert [p["name"] for p in rows] == ["B"]
    rows = client.get("/api/prompts?archived=1").json()
    assert [p["name"] for p in rows] == ["A"]


def test_patch_name_collision_returns_409(client):
    client.post("/api/prompts", json=_new_body(name="A"))
    bid = client.post("/api/prompts", json=_new_body(name="B")).json()["id"]
    r = client.patch(f"/api/prompts/{bid}", json={"name": "A"})
    assert r.status_code == 409


def test_promote_then_edit_returns_409(client):
    pid = client.post("/api/prompts", json=_new_body()).json()["id"]
    vid = client.get(f"/api/prompts/{pid}").json()["versions"][0]["id"]
    r = client.post(f"/api/prompts/{pid}/versions/{vid}:promote")
    assert r.status_code == 200
    r = client.put(
        f"/api/prompts/{pid}/versions/{vid}",
        json={"body": "x", "target_map": {}, "output_schema": {}, "model": "m"},
    )
    assert r.status_code == 409
    assert r.json()["error_code"] == "version_immutable"


def test_create_version_clones_production_into_new_draft(client):
    pid = client.post("/api/prompts", json=_new_body()).json()["id"]
    vid = client.get(f"/api/prompts/{pid}").json()["versions"][0]["id"]
    client.post(f"/api/prompts/{pid}/versions/{vid}:promote")
    r = client.post(f"/api/prompts/{pid}/versions", json={})
    assert r.status_code == 201
    new_vid = r.json()["id"]
    detail = client.get(f"/api/prompts/{pid}").json()
    new_version = next(v for v in detail["versions"] if v["id"] == new_vid)
    assert new_version["state"] == "draft"
    assert new_version["version_num"] == 2
    assert new_version["body"] == "Identify scenes."


def test_promote_auto_archives_previous_production(client):
    pid = client.post("/api/prompts", json=_new_body()).json()["id"]
    v1 = client.get(f"/api/prompts/{pid}").json()["versions"][0]["id"]
    client.post(f"/api/prompts/{pid}/versions/{v1}:promote")
    v2 = client.post(f"/api/prompts/{pid}/versions", json={}).json()["id"]
    client.post(f"/api/prompts/{pid}/versions/{v2}:promote")
    detail = client.get(f"/api/prompts/{pid}").json()
    states = {v["id"]: v["state"] for v in detail["versions"]}
    assert states[v1] == "archived"
    assert states[v2] == "production"


def test_duplicate(client):
    pid = client.post("/api/prompts", json=_new_body(name="P")).json()["id"]
    r = client.post(f"/api/prompts/{pid}:duplicate")
    assert r.status_code == 201
    new_pid = r.json()["id"]
    detail = client.get(f"/api/prompts/{new_pid}").json()
    assert detail["name"] == "Copy of P"
    assert detail["versions"][0]["state"] == "draft"


def test_export_returns_full_shape(client):
    pid = client.post("/api/prompts", json=_new_body(name="X")).json()["id"]
    vid = client.get(f"/api/prompts/{pid}").json()["versions"][0]["id"]
    r = client.get(f"/api/prompts/{pid}/versions/{vid}/export")
    assert r.status_code == 200
    body = r.json()
    assert body["prompt"]["name"] == "X"
    assert body["version"]["body"] == "Identify scenes."
    assert body["version"]["target_map"] == {"scenes": {"kind": "markers"}}


def test_404_unknown_ids(client):
    assert client.get("/api/prompts/999").status_code == 404
    assert client.put(
        "/api/prompts/1/versions/999",
        json={"body": "x", "target_map": {}, "output_schema": {}, "model": "m"},
    ).status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/integration/test_routes_prompts.py -v`

Expected: most FAIL — the stub router has no handlers.

- [ ] **Step 3: Implement the routes**

Replace `backend/app/routes/prompts.py` (full file):

```python
"""REST API for prompt management.

Verb-style sub-paths (`:archive`, `:promote`, `:duplicate`, `:restore`) keep
state mutations visually distinct from RESTful CRUD; FastAPI maps them as
literal path strings.
"""
import json
from typing import Any, Literal

import aiosqlite
from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from backend.app.models.prompt import Prompt, PromptVersion
from backend.app.repositories.prompts import VersionImmutableError

router = APIRouter(prefix="/api/prompts", tags=["prompts"])


# ── request models ──────────────────────────────────────────────────────────


class PromptCreate(BaseModel):
    name: str
    description: str | None = None
    body: str
    target_map: dict
    output_schema: dict
    model: str


class PromptPatch(BaseModel):
    name: str | None = None
    description: str | None = None


class VersionCreate(BaseModel):
    from_version_id: int | None = None


class VersionEdit(BaseModel):
    body: str
    target_map: dict
    output_schema: dict
    model: str


# ── response shaping ────────────────────────────────────────────────────────


def _prompt_envelope(prompt: Prompt, versions: list[PromptVersion]) -> dict[str, Any]:
    """Render full detail: prompt + all versions + convenience pointers."""
    current_prod = next((v.id for v in versions if v.state == "production"), None)
    latest = versions[0].id if versions else None  # versions are desc by version_num
    return {
        **prompt.model_dump(),
        "current_production_version_id": current_prod,
        "latest_version_id": latest,
        "versions": [_version_envelope(v) for v in versions],
    }


def _version_envelope(v: PromptVersion) -> dict[str, Any]:
    out = v.model_dump()
    # TargetMap is a RootModel; model_dump returns the dict shape directly.
    out["target_map"] = v.target_map.model_dump() if hasattr(v.target_map, "model_dump") else v.target_map
    return out


# ── prompt-level routes ─────────────────────────────────────────────────────


@router.get("")
async def list_prompts(request: Request, archived: int = 0):
    ctx = request.app.state.ctx
    if archived:
        rows = await ctx.prompts_repo.list_archived(ctx.db)
    else:
        rows = await ctx.prompts_repo.list_active(ctx.db)
    return [p.model_dump() for p in rows]


@router.get("/{prompt_id}")
async def get_prompt(request: Request, prompt_id: int):
    ctx = request.app.state.ctx
    try:
        prompt, versions = await ctx.prompts_repo.get_with_versions(ctx.db, prompt_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    return _prompt_envelope(prompt, versions)


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_prompt(request: Request, body: PromptCreate):
    ctx = request.app.state.ctx
    try:
        pid, _ = await ctx.prompts_repo.create_with_initial_version(
            ctx.db,
            name=body.name, description=body.description,
            body=body.body, target_map=body.target_map,
            output_schema=body.output_schema, model=body.model,
        )
    except aiosqlite.IntegrityError as exc:
        raise HTTPException(409, f"name collision: {exc}")
    return {"id": pid}


@router.patch("/{prompt_id}")
async def patch_prompt(request: Request, prompt_id: int, body: PromptPatch):
    ctx = request.app.state.ctx
    try:
        await ctx.prompts_repo.update_metadata(
            ctx.db, prompt_id, name=body.name, description=body.description
        )
    except aiosqlite.IntegrityError as exc:
        raise HTTPException(409, f"name collision: {exc}")
    return {"id": prompt_id}


@router.post("/{prompt_id}:archive")
async def archive_prompt(request: Request, prompt_id: int):
    ctx = request.app.state.ctx
    await ctx.prompts_repo.archive(ctx.db, prompt_id)
    return {"id": prompt_id, "archived": True}


@router.post("/{prompt_id}:restore")
async def restore_prompt(request: Request, prompt_id: int):
    ctx = request.app.state.ctx
    await ctx.prompts_repo.restore(ctx.db, prompt_id)
    return {"id": prompt_id, "archived": False}


@router.post("/{prompt_id}:duplicate", status_code=status.HTTP_201_CREATED)
async def duplicate_prompt(request: Request, prompt_id: int):
    ctx = request.app.state.ctx
    try:
        new_pid, _ = await ctx.prompts_repo.duplicate(ctx.db, prompt_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    return {"id": new_pid}


# ── version-level routes ────────────────────────────────────────────────────


@router.get("/{prompt_id}/versions/{version_id}")
async def get_version(request: Request, prompt_id: int, version_id: int):
    ctx = request.app.state.ctx
    try:
        v = await ctx.prompts_repo.get_version(ctx.db, version_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    if v.prompt_id != prompt_id:
        raise HTTPException(404, "version does not belong to prompt")
    return _version_envelope(v)


@router.post("/{prompt_id}/versions", status_code=status.HTTP_201_CREATED)
async def create_version(request: Request, prompt_id: int, body: VersionCreate):
    ctx = request.app.state.ctx
    try:
        new_vid = await ctx.prompts_repo.create_version(
            ctx.db, prompt_id, from_version_id=body.from_version_id
        )
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    return {"id": new_vid}


@router.put("/{prompt_id}/versions/{version_id}")
async def update_version(
    request: Request, prompt_id: int, version_id: int, body: VersionEdit
):
    ctx = request.app.state.ctx
    try:
        v = await ctx.prompts_repo.get_version(ctx.db, version_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    if v.prompt_id != prompt_id:
        raise HTTPException(404, "version does not belong to prompt")
    try:
        await ctx.prompts_repo.update_version(
            ctx.db, version_id,
            body=body.body, target_map=body.target_map,
            output_schema=body.output_schema, model=body.model,
        )
    except VersionImmutableError as exc:
        return JSONResponse(
            {"error_code": "version_immutable", "message": str(exc)},
            status_code=409,
        )
    return {"id": version_id}


@router.post("/{prompt_id}/versions/{version_id}:promote")
async def promote_version(request: Request, prompt_id: int, version_id: int):
    ctx = request.app.state.ctx
    try:
        v = await ctx.prompts_repo.get_version(ctx.db, version_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    if v.prompt_id != prompt_id:
        raise HTTPException(404, "version does not belong to prompt")
    await ctx.prompts_repo.promote_version(ctx.db, prompt_id, version_id)
    return {"id": version_id, "state": "production"}


@router.get("/{prompt_id}/versions/{version_id}/export")
async def export_version(request: Request, prompt_id: int, version_id: int):
    ctx = request.app.state.ctx
    try:
        prompt, _ = await ctx.prompts_repo.get_with_versions(ctx.db, prompt_id)
        v = await ctx.prompts_repo.get_version(ctx.db, version_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    if v.prompt_id != prompt_id:
        raise HTTPException(404, "version does not belong to prompt")
    return {
        "prompt": {"name": prompt.name, "description": prompt.description},
        "version": {
            "version_num": v.version_num,
            "state": v.state,
            "body": v.body,
            "target_map": v.target_map.model_dump()
                          if hasattr(v.target_map, "model_dump") else v.target_map,
            "output_schema": v.output_schema,
            "model": v.model,
        },
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/integration/test_routes_prompts.py -v`

Expected: all 9 tests PASS.

- [ ] **Step 5: Run the full suite to confirm no regressions**

Run: `.venv/bin/python -m pytest -q`

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add backend/app/routes/prompts.py tests/integration/test_routes_prompts.py
git commit -m "feat(api): /api/prompts REST routes + version operations

GET/POST/PATCH /api/prompts, GET /{id}, POST :archive/:restore/:duplicate,
POST /versions, PUT/GET /versions/{vid}, POST :promote, GET /export.
Returns 409 {error_code: version_immutable} on edit of non-draft."
```

---

## Task 9: Page route + base template + rail nav item

**Files:**
- Modify: `backend/app/routes/pages.py` — add `/prompts*` handlers
- Create: `backend/app/templates/pages/prompts.html`
- Create: `backend/app/templates/icons/_prompts.svg`
- Modify: `backend/app/templates/pages/_rail.html`
- Test: `tests/integration/test_routes_pages_prompts.py`

- [ ] **Step 1: Write the failing page-route test**

Create `tests/integration/test_routes_pages_prompts.py`:

```python
"""SSR smoke tests for /prompts."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.context import AppContext
from backend.app.main import app
from backend.app.settings import Settings


@pytest.fixture
async def client(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.delenv("CATDV_PASSWORD", raising=False)
    settings = Settings()
    ctx = await AppContext.build(settings, init_external=False)
    app.state.ctx = ctx
    with TestClient(app) as c:
        yield c
    await ctx.aclose()


def test_prompts_page_renders_empty(client):
    r = client.get("/prompts")
    assert r.status_code == 200
    assert "Prompts" in r.text
    # Empty state when no seed loaded — the SSR test bypasses lifespan seeding.
    assert "No prompts yet" in r.text or "page-body" in r.text


def test_prompts_page_lists_seeded_prompt(client):
    # Manually seed one prompt via the API to avoid relying on the seed loader.
    body = {
        "name": "Test Prompt", "description": "test",
        "body": "p", "target_map": {"x": {"kind": "markers"}},
        "output_schema": {"type": "object"}, "model": "m",
    }
    pid = client.post("/api/prompts", json=body).json()["id"]
    r = client.get("/prompts")
    assert r.status_code == 200
    assert "Test Prompt" in r.text
    r = client.get(f"/prompts/{pid}")
    assert r.status_code == 200
    assert "Test Prompt" in r.text


def test_rail_includes_prompts_link(client):
    r = client.get("/prompts")
    assert r.status_code == 200
    assert 'href="/prompts"' in r.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/integration/test_routes_pages_prompts.py -v`

Expected: FAIL — `/prompts` returns 404.

- [ ] **Step 3: Add the page route**

Append to `backend/app/routes/pages.py` (after `clip_detail_page`):

```python
@router.get("/prompts", response_class=HTMLResponse)
async def prompts_page(request: Request, archived: int = 0):
    ctx = request.app.state.ctx
    repo = ctx.prompts_repo
    prompts = await (repo.list_archived(ctx.db) if archived else repo.list_active(ctx.db))
    selected = None
    selected_version = None
    versions: list = []
    if prompts:
        first_id = prompts[0].id
        selected, versions = await repo.get_with_versions(ctx.db, first_id)
        selected_version = _pick_default_version(versions)
    return templates.TemplateResponse(
        request, "pages/prompts.html",
        {
            "prompts": [p.model_dump() for p in prompts],
            "selected": selected.model_dump() if selected else None,
            "selected_version": _version_view(selected_version) if selected_version else None,
            "versions": [_version_view(v) for v in versions],
            "archived_view": bool(archived),
            "rail_active": "prompts",
        },
    )


@router.get("/prompts/archived", response_class=HTMLResponse)
async def prompts_archived_page(request: Request):
    return await prompts_page(request, archived=1)


@router.get("/prompts/{prompt_id}", response_class=HTMLResponse)
async def prompt_detail_page(request: Request, prompt_id: int, version_id: int | None = None):
    ctx = request.app.state.ctx
    repo = ctx.prompts_repo
    try:
        selected, versions = await repo.get_with_versions(ctx.db, prompt_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    prompts = await repo.list_active(ctx.db)
    selected_version = (
        await repo.get_version(ctx.db, version_id) if version_id is not None
        else _pick_default_version(versions)
    )
    return templates.TemplateResponse(
        request, "pages/prompts.html",
        {
            "prompts": [p.model_dump() for p in prompts],
            "selected": selected.model_dump(),
            "selected_version": _version_view(selected_version),
            "versions": [_version_view(v) for v in versions],
            "archived_view": False,
            "rail_active": "prompts",
        },
    )


def _pick_default_version(versions: list) -> object | None:
    """Default-displayed version: current production, fallback to latest."""
    for v in versions:
        if v.state == "production":
            return v
    return versions[0] if versions else None


def _version_view(v) -> dict:
    """Renderable dict — JSON fields stringified pretty for the textareas."""
    import json as _json
    return {
        "id": v.id,
        "prompt_id": v.prompt_id,
        "version_num": v.version_num,
        "state": v.state,
        "body": v.body,
        "target_map_text": _json.dumps(
            v.target_map.model_dump() if hasattr(v.target_map, "model_dump") else v.target_map,
            indent=2, ensure_ascii=False,
        ),
        "output_schema_text": _json.dumps(v.output_schema, indent=2, ensure_ascii=False),
        "model": v.model,
        "created_at": v.created_at,
        "updated_at": v.updated_at,
    }
```

- [ ] **Step 4: Create the rail icon**

Create `backend/app/templates/icons/_prompts.svg`:

```svg
<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor"
     stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">
  <path d="M4 4h13l3 3v13H4z"/>
  <path d="M8 8h8M8 12h8M8 16h5"/>
</svg>
```

- [ ] **Step 5: Edit `backend/app/templates/pages/_rail.html`**

Add a fourth rail button between Preview and Cache:

```html
<a class="rail-btn{% if _active == 'prompts' %} active{% endif %}"
   href="/prompts" title="Prompts">{% include "icons/_prompts.svg" %}</a>
```

(Place it right after the `id="rail-preview"` anchor and before the cache anchor.)

- [ ] **Step 6: Create the page skeleton**

Create `backend/app/templates/pages/prompts.html`:

```html
{% extends "pages/layout.html" %}
{% block title %}Prompts · CatDV Annotator{% endblock %}
{% block rail_active %}prompts{% endblock %}

{% block content %}
<div class="page prompts-page">
  <div class="page-hdr">
    <h1>Prompts</h1>
    <span class="meta">{{ prompts|length }} {% if archived_view %}archived{% else %}active{% endif %}</span>
    <div class="grow"></div>
    {% if archived_view %}
      <a class="btn ghost" href="/prompts">Active prompts</a>
    {% else %}
      <a class="btn ghost" href="/prompts/archived">Archived</a>
      <button class="btn primary" id="prompt-new-btn">+ New prompt</button>
    {% endif %}
  </div>
  <div class="page-body prompts-body">
    <aside class="prompts-list scroll">
      {% include "pages/_prompts_list.html" %}
    </aside>
    <section class="prompt-detail scroll" id="prompt-detail">
      {% include "pages/_prompt_detail.html" %}
    </section>
  </div>
</div>
<script src="/static/promptEditor.js" defer></script>
{% endblock %}
```

- [ ] **Step 7: Create stub partials so the includes resolve**

Create `backend/app/templates/pages/_prompts_list.html`:

```html
{% if prompts %}
  {% for p in prompts %}
    <a class="tmpl-row{% if selected and selected.id == p.id %} selected{% endif %}"
       href="/prompts/{{ p.id }}">
      <div>
        <div class="name">{{ p.name }}</div>
        <div class="desc">{{ p.description or "" }}</div>
      </div>
    </a>
  {% endfor %}
{% else %}
  <div class="empty-state">No prompts yet</div>
{% endif %}
```

Create `backend/app/templates/pages/_prompt_detail.html`:

```html
{% if selected and selected_version %}
  <div class="prompt-detail-inner">
    <div class="detail-header">{{ selected.name }} — v{{ selected_version.version_num }} ({{ selected_version.state }})</div>
    <div class="detail-body">(detail content fills in in later tasks)</div>
  </div>
{% else %}
  <div class="empty-state">Select a prompt or create one.</div>
{% endif %}
```

- [ ] **Step 8: Run page tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/integration/test_routes_pages_prompts.py -v`

Expected: all 3 PASS.

- [ ] **Step 9: Commit**

```bash
git add backend/app/routes/pages.py \
        backend/app/templates/pages/prompts.html \
        backend/app/templates/pages/_prompts_list.html \
        backend/app/templates/pages/_prompt_detail.html \
        backend/app/templates/icons/_prompts.svg \
        backend/app/templates/pages/_rail.html \
        tests/integration/test_routes_pages_prompts.py
git commit -m "feat(ui): /prompts page skeleton + rail nav

Server-rendered list+detail layout with rail icon. Detail pane is a
stub — fills in over the next two tasks. Verified by SSR smoke tests."
```

---

## Task 10: Detail header — title row, version/state tags, model picker, kebab

**Files:**
- Modify: `backend/app/templates/pages/_prompt_detail.html`
- Create: `backend/app/templates/pages/_prompt_detail_header.html`
- Create: `backend/app/templates/pages/_prompt_menu.html`
- Create: `backend/app/templates/pages/_prompt_version_picker.html`
- Modify: `backend/app/static/app.css` — port `.tmpl-row`, `.tag.good`, `.tag.accent`, `.tag.mono-cell`, `.model-picker`, `.model-menu`, `.tmpl-menu`, `.tmpl-menu-item`, `.tmpl-menu-sep` from design `styles.css`

This task is mostly CSS + template structure with no automated test (the existing SSR smoke test from Task 9 still asserts the page renders). Verify manually at the end.

- [ ] **Step 1: Build the header partial**

Create `backend/app/templates/pages/_prompt_detail_header.html`:

```html
{# Args (via include with context): selected (prompt), selected_version, versions #}
{% set v = selected_version %}
<div class="prompt-detail-hdr row" style="justify-content: space-between; align-items: flex-start; gap: 12px;">
  <div style="min-width: 0;">
    <div class="row" style="gap: 8px; align-items: center; margin-bottom: 2px; flex-wrap: wrap;">
      <div style="font-family: var(--f-display); font-size: 18px; font-weight: 600;">{{ selected.name }}</div>
      {% include "pages/_prompt_version_picker.html" %}
      {% if v.state == "production" %}
        <span class="tag good"><span class="dot"></span>production</span>
      {% elif v.state == "draft" %}
        <span class="tag accent"><span class="dot"></span>draft</span>
      {% else %}
        <span class="tag"><span class="dot"></span>archived</span>
      {% endif %}
    </div>
    <div class="muted" style="font-size: 12px;">{{ selected.description or "" }}</div>
  </div>
  <div class="row" style="gap: 8px; flex: none;"
       x-data="promptEditor({
                  prompt_id: {{ selected.id }},
                  version_id: {{ v.id }},
                  state: '{{ v.state }}',
                  body: {{ v.body|tojson }},
                  target_map_text: {{ v.target_map_text|tojson }},
                  output_schema_text: {{ v.output_schema_text|tojson }},
                  model: '{{ v.model }}'
              })">
    <div class="model-picker" x-ref="modelPicker">
      <button type="button" class="tag info model-picker-btn"
              :class="{ open: modelOpen }"
              @click="modelOpen = !modelOpen"
              :disabled="!canEdit"
              title="switch model">
        <span class="dot"></span><span x-text="draft.model"></span>
        <span style="margin-left: 4px;">▾</span>
      </button>
      <div class="model-menu" x-show="modelOpen" x-cloak @click.outside="modelOpen=false">
        <template x-for="m in MODELS" :key="m">
          <button type="button" class="model-menu-item"
                  :class="{ 'is-current': m === draft.model }"
                  @click="draft.model = m; modelOpen = false">
            <span class="model-menu-dot"></span>
            <span class="model-menu-lbl" x-text="m"></span>
          </button>
        </template>
      </div>
    </div>
    <button type="button" class="btn primary"
            x-show="dirty"
            @click="save()">✓ Save changes</button>
    <div style="position: relative;">
      <button type="button" class="btn ghost" aria-label="More actions"
              @click="menuOpen = !menuOpen"
              style="padding: 6px 8px;">⋯</button>
      <div x-show="menuOpen" x-cloak @click.outside="menuOpen=false">
        {% include "pages/_prompt_menu.html" %}
      </div>
    </div>
  </div>
</div>
```

- [ ] **Step 2: Build the version picker partial**

Create `backend/app/templates/pages/_prompt_version_picker.html`:

```html
<div class="version-picker" x-data="{ open: false }">
  <button type="button" class="tag mono-cell"
          @click="open = !open"
          style="cursor: pointer;">v{{ selected_version.version_num }} ▾</button>
  <div class="version-menu" x-show="open" x-cloak @click.outside="open=false">
    {% for vv in versions %}
      <a class="version-menu-item{% if vv.id == selected_version.id %} is-current{% endif %}"
         href="/prompts/{{ selected.id }}?version_id={{ vv.id }}">
        <span class="mono-cell">v{{ vv.version_num }}</span>
        <span class="muted" style="margin-left: 8px;">{{ vv.state }}</span>
      </a>
    {% endfor %}
  </div>
</div>
```

- [ ] **Step 3: Build the kebab menu partial**

Create `backend/app/templates/pages/_prompt_menu.html`:

```html
<div class="tmpl-menu">
  <form method="post" :action="'/prompts/' + prompt_id + '/_new_version'">
    <button type="submit" class="tmpl-menu-item">+ Create new version</button>
  </form>
  <form method="post" :action="'/prompts/' + prompt_id + '/versions/' + version_id + '/_promote'"
        x-show="state === 'draft'">
    <button type="submit" class="tmpl-menu-item">▶ Promote to production</button>
  </form>
  <div class="tmpl-menu-sep"></div>
  <form method="post" :action="'/prompts/' + prompt_id + '/_duplicate'">
    <button type="submit" class="tmpl-menu-item">⎘ Duplicate</button>
  </form>
  <a class="tmpl-menu-item"
     :href="'/api/prompts/' + prompt_id + '/versions/' + version_id + '/export'"
     download>⬇ Export JSON</a>
  <div class="tmpl-menu-sep"></div>
  <form method="post" :action="'/prompts/' + prompt_id + '/_archive'">
    <button type="submit" class="tmpl-menu-item danger">▣ Archive</button>
  </form>
</div>
```

- [ ] **Step 4: Update `_prompt_detail.html` to include the header**

Replace the stub:

```html
{% if selected and selected_version %}
  {% include "pages/_prompt_detail_header.html" %}
  <div class="prompt-detail-body">
    <div class="muted" style="font-size: 12px;">Editors fill in in the next task.</div>
  </div>
{% else %}
  <div class="empty-state">Select a prompt or create one.</div>
{% endif %}
```

- [ ] **Step 5: Port CSS rules**

Open `/tmp/design/catdv-annotator/project/styles.css` and copy the relevant rules into `backend/app/static/app.css` (append at the bottom). Required selectors (search the design file for each):

- `.tmpl-row`, `.tmpl-row .name`, `.tmpl-row .desc`, `.tmpl-row.selected`
- `.tag`, `.tag.good`, `.tag.accent`, `.tag.info`, `.tag.mono-cell`, `.tag .dot`
- `.model-picker`, `.model-picker-btn`, `.model-menu`, `.model-menu-item`, `.model-menu-item.is-current`, `.model-menu-dot`, `.model-menu-lbl`
- `.tmpl-menu`, `.tmpl-menu-item`, `.tmpl-menu-item.danger`, `.tmpl-menu-sep`
- `.json-editor`, `.code-inline`, the `.k`, `.s`, `.b`, `.n` syntax classes
- `.panel-h`

Then add page-scoped rules (not in the design file — they bridge the `.prompts-page` shell to the existing layout):

```css
/* Prompts page shell */
.prompts-page .prompts-body {
  display: grid;
  grid-template-columns: 320px 1fr;
  height: 100%;
}
.prompts-page .prompts-list { border-right: 1px solid var(--line); }
.prompts-page .prompt-detail { padding: 16px 20px; display: flex; flex-direction: column; gap: 14px; }
.prompts-page .empty-state { padding: 24px; color: var(--text-3); font-size: 13px; }
.prompts-page .version-picker { position: relative; display: inline-flex; }
.prompts-page .version-menu {
  position: absolute; top: 100%; left: 0; margin-top: 4px; z-index: 30;
  background: var(--panel); border: 1px solid var(--line); border-radius: 6px;
  min-width: 160px; padding: 4px 0;
}
.prompts-page .version-menu-item {
  display: block; padding: 6px 10px; color: var(--text-1); text-decoration: none;
  font-size: 12px;
}
.prompts-page .version-menu-item.is-current { background: var(--panel-2); }
.prompts-page .version-menu-item:hover { background: var(--panel-2); }
[x-cloak] { display: none !important; }
```

- [ ] **Step 6: Manual sanity — start the server and open `/prompts`**

Check that no dev server is running, then start one (project convention):

```bash
/usr/sbin/lsof -nP -iTCP:8765 -sTCP:LISTEN
# If empty:
./run.sh &
```

Open `http://localhost:8765/prompts` in a browser. Verify:
1. Left rail has the Prompts icon, active.
2. Page header reads "Prompts".
3. List rail shows the seeded "Scene markers + Czech summary + era" with description.
4. Detail header shows the prompt name, `v1` tag, `production` pill, model picker disabled (production is immutable).
5. Kebab `⋯` opens a popover with all five items.
6. Browser console is clean.

Shut down gracefully:

```bash
/bin/kill -TERM $(/usr/sbin/lsof -nP -iTCP:8765 -sTCP:LISTEN | awk 'NR==2{print $2}')
```

Confirm the log shows `Application shutdown complete.` (required to release the CatDV seat).

- [ ] **Step 7: Commit**

```bash
git add backend/app/templates/pages/_prompt_detail.html \
        backend/app/templates/pages/_prompt_detail_header.html \
        backend/app/templates/pages/_prompt_menu.html \
        backend/app/templates/pages/_prompt_version_picker.html \
        backend/app/static/app.css
git commit -m "feat(ui): prompt detail header — tags, model picker, kebab

Ports the design's title-row treatment (version + state pills, model
picker dropdown, dirty Save, kebab menu) onto Alpine. Production-state
versions disable the model picker. Editor body comes next."
```

---

## Task 11: Detail body — prompt textarea + target_map + output_schema editors + Save flow

**Files:**
- Modify: `backend/app/templates/pages/_prompt_detail.html`
- Create: `backend/app/static/promptEditor.js`
- Modify: `backend/app/routes/pages.py` — add HTMX action endpoints (`_save_version`, `_new_version`, `_promote`, `_duplicate`, `_archive`)

The Alpine component drives dirty tracking, model picker open/close, kebab open/close, and Save (POSTs JSON to `/api/prompts/{id}/versions/{vid}`). On error (JSON parse, validation, 409 immutable), shows an inline error banner above the offending field.

- [ ] **Step 1: Replace `_prompt_detail.html` with the full editor body**

```html
{% if selected and selected_version %}
<div class="prompt-detail-inner"
     x-data="promptEditor({
                prompt_id: {{ selected.id }},
                version_id: {{ selected_version.id }},
                state: '{{ selected_version.state }}',
                body: {{ selected_version.body|tojson }},
                target_map_text: {{ selected_version.target_map_text|tojson }},
                output_schema_text: {{ selected_version.output_schema_text|tojson }},
                model: '{{ selected_version.model }}'
            })">

  {# Title row + tags + model picker + Save + kebab.
     The header partial is rendered _inside_ the x-data scope so the
     Alpine bindings on Save / model-picker / kebab resolve. #}
  {% set inline_header = True %}
  <div class="row" style="justify-content: space-between; align-items: flex-start; gap: 12px;">
    <div style="min-width: 0;">
      <div class="row" style="gap: 8px; align-items: center; margin-bottom: 2px; flex-wrap: wrap;">
        <div style="font-family: var(--f-display); font-size: 18px; font-weight: 600;">{{ selected.name }}</div>
        {% include "pages/_prompt_version_picker.html" %}
        {% if selected_version.state == "production" %}
          <span class="tag good"><span class="dot"></span>production</span>
        {% elif selected_version.state == "draft" %}
          <span class="tag accent"><span class="dot"></span>draft</span>
        {% else %}
          <span class="tag"><span class="dot"></span>archived</span>
        {% endif %}
      </div>
      <div class="muted" style="font-size: 12px;">{{ selected.description or "" }}</div>
    </div>
    <div class="row" style="gap: 8px; flex: none;">
      <div class="model-picker">
        <button type="button" class="tag info model-picker-btn"
                :class="{ open: modelOpen }"
                @click="modelOpen = !modelOpen"
                :disabled="!canEdit">
          <span class="dot"></span><span x-text="draft.model"></span>
          <span style="margin-left: 4px;">▾</span>
        </button>
        <div class="model-menu" x-show="modelOpen" x-cloak @click.outside="modelOpen=false">
          <template x-for="m in MODELS" :key="m">
            <button type="button" class="model-menu-item"
                    :class="{ 'is-current': m === draft.model }"
                    @click="draft.model = m; modelOpen = false">
              <span class="model-menu-dot"></span>
              <span class="model-menu-lbl" x-text="m"></span>
            </button>
          </template>
        </div>
      </div>
      <button type="button" class="btn primary" x-show="dirty" @click="save()">✓ Save changes</button>
      <div style="position: relative;">
        <button type="button" class="btn ghost" aria-label="More actions"
                @click="menuOpen = !menuOpen"
                style="padding: 6px 8px;">⋯</button>
        <div x-show="menuOpen" x-cloak @click.outside="menuOpen=false">
          {% include "pages/_prompt_menu.html" %}
        </div>
      </div>
    </div>
  </div>

  {# Error banner (shown after a failed Save). #}
  <div class="error-banner" x-show="error" x-cloak x-text="error"></div>

  {# Prompt body editor. #}
  <div>
    <div class="panel-h" style="padding: 0; background: transparent; border: 0; height: 24px;">Prompt</div>
    <textarea class="txt scroll"
              style="width: 100%; min-height: 130px; resize: vertical;
                     line-height: 1.5; font-family: var(--f-mono);"
              :readonly="!canEdit"
              x-model="draft.body"></textarea>
    <div class="muted" style="font-size: 11px; margin-top: 4px;">
      Plain text. No HTML, no Markdown — sent as-is to Gemini.
    </div>
  </div>

  {# target_map editor. #}
  <div>
    <div class="panel-h" style="padding: 0; background: transparent; border: 0; height: 24px;">target_map</div>
    <textarea class="txt scroll json-editor"
              style="width: 100%; min-height: 120px; resize: vertical;
                     line-height: 1.5; font-family: var(--f-mono);"
              :readonly="!canEdit"
              x-model="draft.target_map_text"></textarea>
    <div class="muted" style="font-size: 11px; margin-top: 4px;">
      Maps schema field → CatDV target.
      <span class="code-inline">markers</span>,
      <span class="code-inline">note</span>,
      <span class="code-inline">field</span>.
    </div>
  </div>

  {# output_schema editor. #}
  <div>
    <div class="panel-h" style="padding: 0; background: transparent; border: 0; height: 24px;">output_schema</div>
    <textarea class="txt scroll json-editor"
              style="width: 100%; min-height: 160px; resize: vertical;
                     line-height: 1.5; font-family: var(--f-mono);"
              :readonly="!canEdit"
              x-model="draft.output_schema_text"></textarea>
    <div class="muted" style="font-size: 11px; margin-top: 4px;">
      JSON Schema for the Gemini response. Versioned alongside the body.
    </div>
  </div>
</div>
{% else %}
  <div class="empty-state">Select a prompt or create one.</div>
{% endif %}
```

- [ ] **Step 2: Write the Alpine component**

Create `backend/app/static/promptEditor.js`:

```js
// promptEditor — single Alpine.data factory for the prompt detail pane.
// Tracks dirtiness, manages model + kebab popovers, posts edits via fetch().
document.addEventListener("alpine:init", () => {
  Alpine.data("promptEditor", (initial) => ({
    prompt_id: initial.prompt_id,
    version_id: initial.version_id,
    state: initial.state,
    initial: {
      body: initial.body,
      target_map_text: initial.target_map_text,
      output_schema_text: initial.output_schema_text,
      model: initial.model,
    },
    draft: {
      body: initial.body,
      target_map_text: initial.target_map_text,
      output_schema_text: initial.output_schema_text,
      model: initial.model,
    },
    menuOpen: false,
    modelOpen: false,
    error: "",
    saving: false,

    MODELS: [
      "gemini-2.5-pro",
      "gemini-2.5-flash",
      "gemini-2.5-flash-lite",
      "gemini-2.0-pro",
    ],

    get canEdit() { return this.state === "draft"; },
    get dirty() {
      if (!this.canEdit) return false;
      const d = this.draft, i = this.initial;
      return d.body !== i.body
        || d.target_map_text !== i.target_map_text
        || d.output_schema_text !== i.output_schema_text
        || d.model !== i.model;
    },

    parseOrFail(label, text) {
      try { return JSON.parse(text); }
      catch (e) { throw new Error(`${label}: invalid JSON — ${e.message}`); }
    },

    async save() {
      if (!this.dirty || this.saving) return;
      this.error = "";
      this.saving = true;
      let target_map, output_schema;
      try {
        target_map = this.parseOrFail("target_map", this.draft.target_map_text);
        output_schema = this.parseOrFail("output_schema", this.draft.output_schema_text);
      } catch (e) {
        this.error = e.message;
        this.saving = false;
        return;
      }
      try {
        const resp = await fetch(
          `/api/prompts/${this.prompt_id}/versions/${this.version_id}`,
          {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              body: this.draft.body,
              target_map: target_map,
              output_schema: output_schema,
              model: this.draft.model,
            }),
          }
        );
        if (!resp.ok) {
          const data = await resp.json().catch(() => ({ message: resp.statusText }));
          this.error = data.message || `save failed (${resp.status})`;
          return;
        }
        // Success — re-baseline and reload the page so version metadata
        // (updated_at, list rail) stays in sync.
        this.initial = { ...this.draft };
        window.location.reload();
      } finally {
        this.saving = false;
      }
    },
  }));
});
```

- [ ] **Step 3: Remove the now-unused `_prompt_detail_header.html`**

```bash
git rm backend/app/templates/pages/_prompt_detail_header.html
```

(The header is now inlined into `_prompt_detail.html` so the Alpine `x-data` scope wraps it correctly.)

- [ ] **Step 4: Add error-banner CSS**

Append to `backend/app/static/app.css`:

```css
.error-banner {
  background: var(--bad-bg, #3a1a1a);
  color: var(--bad, #ff8080);
  border: 1px solid var(--bad, #ff8080);
  border-radius: 6px;
  padding: 8px 10px;
  font-size: 12px;
  font-family: var(--f-mono);
}
```

- [ ] **Step 5: Run the page test suite (still passes — Save flow isn't exercised by SSR tests)**

Run: `.venv/bin/python -m pytest tests/integration/test_routes_pages_prompts.py -v`

Expected: 3 PASS.

- [ ] **Step 6: Manual smoke — edit + Save a draft**

Start the server (check first), open `/prompts`, click the seeded prompt (which is production — Save button hidden, fields read-only). Open the kebab → "Create new version" — but that action isn't wired yet (Task 12). Skip; we'll come back. Verify visually:

1. Three editor panels render: prompt body, target_map, output_schema.
2. Textareas show JSON pretty-printed.
3. Textareas are `readonly` while state is production (no caret on click).
4. Browser console clean.

Shut down with `kill -TERM`.

- [ ] **Step 7: Commit**

```bash
git add backend/app/templates/pages/_prompt_detail.html \
        backend/app/static/promptEditor.js \
        backend/app/static/app.css
git rm backend/app/templates/pages/_prompt_detail_header.html
git commit -m "feat(ui): prompt detail editor body + Save flow

Body, target_map, and output_schema editors driven by an Alpine
component. Save PUTs to /api/prompts/{id}/versions/{vid}, validates
JSON client-side, and surfaces 409 (version_immutable) and 422
(invalid JSON) inline. Production versions render read-only."
```

---

## Task 12: Kebab actions — new version, promote, duplicate, archive

**Files:**
- Modify: `backend/app/routes/pages.py` — add five action endpoints
- Modify: `backend/app/templates/pages/_prompt_menu.html` — wire forms to the new endpoints

The kebab forms post to `/prompts/{id}/_<action>` (or `/prompts/{id}/versions/{vid}/_<action>`); each endpoint mutates via `PromptsRepo`, then 303-redirects to the canonical landing place. Forms use plain HTML POST + redirect — no HTMX, no JSON. Matches the project's pattern for non-table-cell mutations.

- [ ] **Step 1: Add action endpoints to `backend/app/routes/pages.py`**

Append (after `prompt_detail_page`):

```python
from fastapi.responses import RedirectResponse  # add to existing imports if not present


@router.post("/prompts/{prompt_id}/_new_version")
async def action_new_version(request: Request, prompt_id: int):
    ctx = request.app.state.ctx
    new_vid = await ctx.prompts_repo.create_version(ctx.db, prompt_id)
    return RedirectResponse(
        f"/prompts/{prompt_id}?version_id={new_vid}", status_code=303
    )


@router.post("/prompts/{prompt_id}/versions/{version_id}/_promote")
async def action_promote_version(request: Request, prompt_id: int, version_id: int):
    ctx = request.app.state.ctx
    await ctx.prompts_repo.promote_version(ctx.db, prompt_id, version_id)
    return RedirectResponse(
        f"/prompts/{prompt_id}?version_id={version_id}", status_code=303
    )


@router.post("/prompts/{prompt_id}/_duplicate")
async def action_duplicate_prompt(request: Request, prompt_id: int):
    ctx = request.app.state.ctx
    new_pid, _ = await ctx.prompts_repo.duplicate(ctx.db, prompt_id)
    return RedirectResponse(f"/prompts/{new_pid}", status_code=303)


@router.post("/prompts/{prompt_id}/_archive")
async def action_archive_prompt(request: Request, prompt_id: int):
    ctx = request.app.state.ctx
    await ctx.prompts_repo.archive(ctx.db, prompt_id)
    return RedirectResponse("/prompts", status_code=303)


@router.post("/prompts/{prompt_id}/_restore")
async def action_restore_prompt(request: Request, prompt_id: int):
    ctx = request.app.state.ctx
    await ctx.prompts_repo.restore(ctx.db, prompt_id)
    return RedirectResponse(f"/prompts/{prompt_id}", status_code=303)
```

- [ ] **Step 2: Update `_prompt_menu.html` to use proper string-interpolated URLs**

The previous version used Alpine `:action` bindings which won't interpolate at render time. Replace with Jinja-rendered URLs:

```html
{# Rendered inside x-data='promptEditor(...)' so we have prompt_id/version_id at hand.
   But for form actions we use the server-rendered IDs directly — simpler and a
   plain progressive-enhancement form. #}
<div class="tmpl-menu">
  <form method="post" action="/prompts/{{ selected.id }}/_new_version">
    <button type="submit" class="tmpl-menu-item">+ Create new version</button>
  </form>
  {% if selected_version.state == "draft" %}
  <form method="post"
        action="/prompts/{{ selected.id }}/versions/{{ selected_version.id }}/_promote">
    <button type="submit" class="tmpl-menu-item">▶ Promote to production</button>
  </form>
  {% endif %}
  <div class="tmpl-menu-sep"></div>
  <form method="post" action="/prompts/{{ selected.id }}/_duplicate">
    <button type="submit" class="tmpl-menu-item">⎘ Duplicate</button>
  </form>
  <a class="tmpl-menu-item"
     href="/api/prompts/{{ selected.id }}/versions/{{ selected_version.id }}/export"
     download="prompt-{{ selected.id }}-v{{ selected_version.version_num }}.json">
     ⬇ Export JSON
  </a>
  <div class="tmpl-menu-sep"></div>
  {% if not selected.archived %}
  <form method="post" action="/prompts/{{ selected.id }}/_archive">
    <button type="submit" class="tmpl-menu-item danger">▣ Archive</button>
  </form>
  {% else %}
  <form method="post" action="/prompts/{{ selected.id }}/_restore">
    <button type="submit" class="tmpl-menu-item">↺ Restore</button>
  </form>
  {% endif %}
</div>
```

- [ ] **Step 3: Run the page test suite to confirm no regression**

Run: `.venv/bin/python -m pytest tests/integration/test_routes_pages_prompts.py -v`

Expected: PASS.

- [ ] **Step 4: Manual smoke — full lifecycle**

Start the server, open `/prompts`, then walk:

1. Kebab → "Create new version" → URL becomes `/prompts/{id}?version_id={v2}`. Header shows `v2` + `draft`. Save button appears once you edit the body. Save → success, no console errors, page reloads.
2. Kebab → "Promote to production" → header pill flips to `production`. Re-open the version picker — v1 now shows `archived`.
3. Kebab → "Duplicate" → lands on `/prompts/{new_id}`, name "Copy of …", v1@draft.
4. Kebab → "Archive" → redirect to `/prompts` and the prompt is gone from the active list.
5. Visit `/prompts/archived` — see the archived prompt with a Restore action in its kebab.
6. Kebab → "Export JSON" → downloads `prompt-X-vY.json` with `{prompt: {...}, version: {...}}`.

Console clean throughout. Shut down with `kill -TERM`.

- [ ] **Step 5: Commit**

```bash
git add backend/app/routes/pages.py backend/app/templates/pages/_prompt_menu.html
git commit -m "feat(ui): prompt kebab actions — new version / promote / duplicate / archive

Plain POST + 303 redirect for each kebab action. Promote item only
shows on draft versions; archive/restore swap based on prompt state.
Export downloads as JSON via the existing /api/.../export endpoint."
```

---

## Task 13: New-prompt creation flow

**Files:**
- Modify: `backend/app/routes/pages.py` — `GET /prompts/new` (form) + `POST /prompts/_create`
- Create: `backend/app/templates/pages/_prompt_new.html`
- Modify: `backend/app/templates/pages/prompts.html` — wire the "+ New prompt" button to `/prompts/new`

- [ ] **Step 1: Add create-form route + handler**

Append to `backend/app/routes/pages.py`:

```python
@router.get("/prompts/new", response_class=HTMLResponse)
async def prompt_new_page(request: Request):
    ctx = request.app.state.ctx
    prompts = await ctx.prompts_repo.list_active(ctx.db)
    return templates.TemplateResponse(
        request, "pages/_prompt_new.html",
        {
            "prompts": [p.model_dump() for p in prompts],
            "rail_active": "prompts",
            "error": None,
            "form": {"name": "", "description": "", "body": "",
                     "target_map_text": "{}", "output_schema_text": "{}",
                     "model": "gemini-2.5-pro"},
        },
    )


@router.post("/prompts/_create")
async def action_create_prompt(request: Request):
    ctx = request.app.state.ctx
    form = await request.form()
    name = (form.get("name") or "").strip()
    description = (form.get("description") or "").strip() or None
    body = form.get("body") or ""
    target_map_text = form.get("target_map") or "{}"
    output_schema_text = form.get("output_schema") or "{}"
    model = form.get("model") or "gemini-2.5-pro"
    error = None
    try:
        target_map = json.loads(target_map_text)
        output_schema = json.loads(output_schema_text)
    except json.JSONDecodeError as exc:
        error = f"invalid JSON: {exc}"
    if not name:
        error = "name is required"
    if error:
        prompts = await ctx.prompts_repo.list_active(ctx.db)
        return templates.TemplateResponse(
            request, "pages/_prompt_new.html",
            {
                "prompts": [p.model_dump() for p in prompts],
                "rail_active": "prompts",
                "error": error,
                "form": {"name": name, "description": description or "",
                         "body": body, "target_map_text": target_map_text,
                         "output_schema_text": output_schema_text, "model": model},
            },
            status_code=400,
        )
    try:
        pid, _ = await ctx.prompts_repo.create_with_initial_version(
            ctx.db, name=name, description=description,
            body=body, target_map=target_map,
            output_schema=output_schema, model=model,
        )
    except aiosqlite.IntegrityError as exc:
        prompts = await ctx.prompts_repo.list_active(ctx.db)
        return templates.TemplateResponse(
            request, "pages/_prompt_new.html",
            {
                "prompts": [p.model_dump() for p in prompts],
                "rail_active": "prompts",
                "error": f"name already exists: {exc}",
                "form": {"name": name, "description": description or "",
                         "body": body, "target_map_text": target_map_text,
                         "output_schema_text": output_schema_text, "model": model},
            },
            status_code=400,
        )
    return RedirectResponse(f"/prompts/{pid}", status_code=303)
```

(Make sure `import json` and `import aiosqlite` are at the top of `pages.py`; add if missing.)

- [ ] **Step 2: Create the new-prompt template**

Create `backend/app/templates/pages/_prompt_new.html`:

```html
{% extends "pages/layout.html" %}
{% block title %}New Prompt · CatDV Annotator{% endblock %}
{% block rail_active %}prompts{% endblock %}

{% block content %}
<div class="page prompts-page">
  <div class="page-hdr">
    <h1>New prompt</h1>
    <div class="grow"></div>
    <a class="btn ghost" href="/prompts">Cancel</a>
  </div>
  <div class="page-body" style="padding: 16px 20px;">
    {% if error %}<div class="error-banner">{{ error }}</div>{% endif %}
    <form method="post" action="/prompts/_create"
          style="display: flex; flex-direction: column; gap: 14px; max-width: 800px;">
      <label>
        <div class="panel-h" style="padding: 0; background: transparent; border: 0; height: 24px;">Name</div>
        <input type="text" name="name" value="{{ form.name }}" required class="txt"
               style="width: 100%;" />
      </label>
      <label>
        <div class="panel-h" style="padding: 0; background: transparent; border: 0; height: 24px;">Description</div>
        <input type="text" name="description" value="{{ form.description }}" class="txt"
               style="width: 100%;" />
      </label>
      <label>
        <div class="panel-h" style="padding: 0; background: transparent; border: 0; height: 24px;">Model</div>
        <select name="model" class="txt" style="width: 240px;">
          {% for m in ["gemini-2.5-pro", "gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-2.0-pro"] %}
            <option value="{{ m }}"{% if form.model == m %} selected{% endif %}>{{ m }}</option>
          {% endfor %}
        </select>
      </label>
      <label>
        <div class="panel-h" style="padding: 0; background: transparent; border: 0; height: 24px;">Prompt</div>
        <textarea name="body" class="txt scroll"
                  style="width: 100%; min-height: 130px; resize: vertical; font-family: var(--f-mono);">{{ form.body }}</textarea>
      </label>
      <label>
        <div class="panel-h" style="padding: 0; background: transparent; border: 0; height: 24px;">target_map (JSON)</div>
        <textarea name="target_map" class="txt scroll json-editor"
                  style="width: 100%; min-height: 100px; resize: vertical; font-family: var(--f-mono);">{{ form.target_map_text }}</textarea>
      </label>
      <label>
        <div class="panel-h" style="padding: 0; background: transparent; border: 0; height: 24px;">output_schema (JSON)</div>
        <textarea name="output_schema" class="txt scroll json-editor"
                  style="width: 100%; min-height: 140px; resize: vertical; font-family: var(--f-mono);">{{ form.output_schema_text }}</textarea>
      </label>
      <div class="row" style="gap: 8px;">
        <button type="submit" class="btn primary">Create prompt</button>
        <a class="btn ghost" href="/prompts">Cancel</a>
      </div>
    </form>
  </div>
</div>
{% endblock %}
```

- [ ] **Step 3: Wire the "+ New prompt" button**

Edit `backend/app/templates/pages/prompts.html`. Replace the button:

```html
<!-- was: <button class="btn primary" id="prompt-new-btn">+ New prompt</button> -->
<a class="btn primary" href="/prompts/new">+ New prompt</a>
```

- [ ] **Step 4: Add an SSR smoke test**

Append to `tests/integration/test_routes_pages_prompts.py`:

```python
def test_new_prompt_form_renders(client):
    r = client.get("/prompts/new")
    assert r.status_code == 200
    assert 'action="/prompts/_create"' in r.text
    assert "New prompt" in r.text


def test_new_prompt_post_creates_and_redirects(client):
    r = client.post(
        "/prompts/_create",
        data={
            "name": "Brand New",
            "description": "ssr-created",
            "body": "Hello",
            "target_map": '{"x": {"kind": "markers"}}',
            "output_schema": '{"type": "object"}',
            "model": "gemini-2.5-pro",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"].startswith("/prompts/")
    pid = int(r.headers["location"].rsplit("/", 1)[1])
    detail = client.get(f"/api/prompts/{pid}").json()
    assert detail["name"] == "Brand New"
    assert detail["versions"][0]["state"] == "draft"


def test_new_prompt_post_invalid_json_returns_400_with_form(client):
    r = client.post(
        "/prompts/_create",
        data={
            "name": "X", "description": "",
            "body": "h", "target_map": "not json",
            "output_schema": "{}", "model": "gemini-2.5-pro",
        },
    )
    assert r.status_code == 400
    assert "invalid JSON" in r.text
    assert "X" in r.text  # name persists in the form
```

- [ ] **Step 5: Run the test suite**

Run: `.venv/bin/python -m pytest tests/integration/test_routes_pages_prompts.py -v`

Expected: all (3 + 3 new) PASS.

- [ ] **Step 6: Manual smoke**

Start server, open `/prompts`, click "+ New prompt", fill the form, submit. Land on detail page for the new prompt with v1@draft. Edit + Save works. Kebab → Promote works. Shut down with `kill -TERM`.

- [ ] **Step 7: Commit**

```bash
git add backend/app/routes/pages.py \
        backend/app/templates/pages/_prompt_new.html \
        backend/app/templates/pages/prompts.html \
        tests/integration/test_routes_pages_prompts.py
git commit -m "feat(ui): new prompt creation form

GET /prompts/new shows the form; POST /prompts/_create validates JSON
inputs, surfaces errors inline (preserving form state), and on success
303-redirects to the new prompt's detail page."
```

---

## Task 14: Final verification + housekeeping

**Files:**
- Modify: `README.md` — update the "Status" bullet list

- [ ] **Step 1: Run the entire test suite**

Run: `.venv/bin/python -m pytest -q`

Expected: ALL tests pass. No skips beyond those that existed before. If any test fails, investigate root cause (per global rules — no surface patches).

- [ ] **Step 2: Run linter**

Run: `.venv/bin/ruff check backend/ tests/`

Expected: clean. Fix any new warnings introduced by this PR.

- [ ] **Step 3: Sanity-check on a real DB copy**

Backup the local DB, run the migration in place, confirm shape:

```bash
cp data/app.db /tmp/pre-0009.db
# Start the server briefly so migrations apply, then shut down.
/usr/sbin/lsof -nP -iTCP:8765 -sTCP:LISTEN  # must be empty
./run.sh &
sleep 5
/bin/kill -TERM $(/usr/sbin/lsof -nP -iTCP:8765 -sTCP:LISTEN | awk 'NR==2{print $2}')
# Wait for shutdown, then:
/usr/bin/sqlite3 data/app.db "SELECT (SELECT COUNT(*) FROM prompts), (SELECT COUNT(*) FROM prompt_versions), (SELECT COUNT(*) FROM annotations);"
```

Expected: prompts and prompt_versions counts equal what `templates` was before; annotation count unchanged.

If anything looks off, restore the backup:

```bash
cp /tmp/pre-0009.db data/app.db
```

- [ ] **Step 4: Update README "Status" section**

Edit `README.md`. In the "Status" section, add a line:

```markdown
- Prompt management: `docs/plans/2026-05-21-prompt-management.md`
```

- [ ] **Step 5: Final commit**

```bash
git add README.md
git commit -m "docs: link prompt-management plan in README status"
```

- [ ] **Step 6: Verify branch is ready**

Run: `git log --oneline main..HEAD` (or `origin/main..HEAD` if branched).

Expected: a coherent series of commits, one per task, every test passing at every commit. Ready for review / merge.

---

## Done — verify checklist

After Task 14, the following should all be true:

- [ ] Migration 0009 applied cleanly on the live DB; counts match.
- [ ] `templates` table no longer exists.
- [ ] `annotations.prompt_version_id` and `jobs.prompt_version_id` populated.
- [ ] `/api/prompts` and `/prompts*` routes serve full CRUD + version flow.
- [ ] Left rail has the Prompts icon, active when on `/prompts*`.
- [ ] Editing a production version is impossible (button hidden, fields read-only, server 409 if forced).
- [ ] Promoting a draft auto-archives the previous production.
- [ ] Duplicate creates "Copy of X" (or "(2)" / "(3)" if taken).
- [ ] Archive hides from default view; `/prompts/archived` shows it; Restore works.
- [ ] Export JSON downloads the prompt+version snapshot.
- [ ] Full test suite passes; ruff clean.
