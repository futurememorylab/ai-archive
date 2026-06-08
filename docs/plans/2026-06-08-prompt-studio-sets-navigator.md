# Prompt Studio — Sets, Source Tabs & Navigator Polish — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rename Prompt Studio "folders" to "sets" (UI + routes + DB), add source-partitioned Archive/Uploaded tabs (Uploaded stubbed), hide Archive when no archive is connected, restyle the clip navigator (selection checkboxes + year/timecode thumbnails), and add a client-loop "Run on N clips" bulk action.

**Architecture:** A flat `studio_set` table gains a `source` discriminator (`'archive' | 'uploaded'`), name-unique per source. Everything that said "folder" is renamed to "set" across repo, routes, page partials, JS, CSS, and tests. The navigator gets a source tab-bar (HTMX-switched set lists); the Uploaded tab is a "coming soon" stub. Bulk run reuses the existing per-clip `POST /api/studio/runs` endpoint from a bounded-concurrency client loop — no run-engine change.

**Tech Stack:** FastAPI + aiosqlite (SQL migrations via `migrations_runner.py`), Jinja2 partials, HTMX, Alpine.js (`Alpine.store('studio')`), pytest (`TestClient`), plain CSS with design tokens.

**Spec:** `docs/specs/2026-06-08-prompt-studio-sets-navigator-design.md`

---

## Conventions used in this plan

- **Run tests with the project venv:** `.venv/bin/python -m pytest …`.
- **Server discipline:** do NOT start a dev server for these tasks; tests use `TestClient` with no live archive. If you must, follow the `server-start`/`server-stop` skills (SIGTERM only).
- **Commit after every task** once its tests are green.
- **The rename is coordinated:** Tasks 1–8 rename `folder → set` layer by layer. Each task updates *its own* tests in the same commit so the suite stays green at every commit. Do not split a rename across commits.

### Canonical rename map (apply per task, not all at once)

| Old | New |
|---|---|
| table `studio_folder` | `studio_set` |
| table `studio_folder_clip` | `studio_set_clip` (`folder_id` col → `set_id`) |
| `StudioFoldersRepo` | `StudioSetsRepo` |
| `studio_folders_repo` (ctx field) | `studio_sets_repo` |
| `create_folder` / `rename_folder` / `delete_folder` | `create_set` / `rename_set` / `delete_set` |
| `list_folders_with_counts` | `list_sets_with_counts(source=…)` |
| `folder_id_for_clip` | `set_id_for_clip` |
| route prefix `/api/studio/folders` | `/api/studio/sets` |
| page routes `/studio/_folders`, `/studio/_folder` | `/studio/_sets`, `/studio/_set` |
| `FolderCreate` / `FolderPatch` | `SetCreate` / `SetPatch` |
| template `_studio_folder_list.html` | `_studio_set_list.html` |
| template `_studio_folder_card.html` | `_studio_set_card.html` |
| template `_studio_folder.html` | `_studio_set.html` |
| template `_studio_clip_card.html` | `_studio_set_clip_card.html` |
| JS `studioFolders()` / `newFolderOpen` / `newFolderName` / `createFolder` | `studioSets()` / `newSetOpen` / `newSetName` / `createSet` |
| `window.studio.removeClip(folderId,…)` | `removeClip(setId,…)` |
| CSS `.studio-folder*`, `.studio-folders*` | `.studio-set*`, `.studio-sets*` |
| CSS `.studio-clip-card` | keep `.studio-clip-card` (used by `_studio_set_clip_card.html`) — **do not rename**, many CSS rules + `test_studio_css_*` reference it |

> Note: `studio_run` / `studio_runs_repo` / `studio-run*` are a DIFFERENT feature (run history). **Do not** touch anything with `run` in the name. Only `folder`→`set`.

---

## Phase 0 — Data layer (migration + repo)

### Task 1: Migration 0015 — rename tables + `source` column

**Files:**
- Create: `backend/migrations/0015_studio_sets.sql`
- Test: `tests/unit/test_migration_0015_studio_sets.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_migration_0015_studio_sets.py
"""0015 renames studio_folder→studio_set, adds source, preserves rows,
and enforces UNIQUE(source, name)."""

from pathlib import Path

import aiosqlite
import pytest

from backend.app.db import open_db
from backend.app.migrations_runner import apply_migrations

MIG = Path("backend/migrations")


async def _apply_through(conn, *, stop_before: str) -> None:
    """Apply every *.sql migration in lexical order, stopping before
    `stop_before`. Replicates the runner's per-file executescript so later
    migrations see the schema their predecessors built (e.g. 0013's
    `ALTER TABLE jobs` needs the `jobs` table from an earlier file)."""
    for p in sorted(MIG.glob("*.sql")):
        if p.name == stop_before:
            return
        await conn.executescript(p.read_text())
    await conn.commit()


@pytest.fixture
async def conn(tmp_path: Path):
    cm = open_db(tmp_path / "t.db")
    c = await cm.__aenter__()
    yield c
    await cm.__aexit__(None, None, None)


@pytest.mark.asyncio
async def test_0015_preserves_rows_and_defaults_source(conn):
    # Build the full pre-0015 schema, seed the old tables, then run 0015.
    await _apply_through(conn, stop_before="0015_studio_sets.sql")
    await conn.execute(
        "INSERT INTO studio_folder(id, name, created_at) VALUES (1, 'keep', '2026-01-01')"
    )
    await conn.execute(
        "INSERT INTO studio_folder_clip(folder_id, clip_id, added_at) "
        "VALUES (1, 999, '2026-01-02')"
    )
    await conn.commit()
    await conn.executescript((MIG / "0015_studio_sets.sql").read_text())
    await conn.commit()

    cur = await conn.execute("SELECT id, name, source FROM studio_set")
    assert await cur.fetchone() == (1, "keep", "archive")
    cur = await conn.execute("SELECT set_id, clip_id FROM studio_set_clip")
    assert await cur.fetchone() == (1, 999)


@pytest.mark.asyncio
async def test_0015_unique_per_source(conn):
    await apply_migrations(conn, Path("backend/migrations"))
    await conn.execute(
        "INSERT INTO studio_set(name, source, created_at) VALUES ('dup','archive','t')"
    )
    await conn.commit()
    # Same name, same source → reject.
    with pytest.raises(aiosqlite.IntegrityError):
        await conn.execute(
            "INSERT INTO studio_set(name, source, created_at) VALUES ('dup','archive','t')"
        )
        await conn.commit()
    # Same name, different source → allowed.
    await conn.execute(
        "INSERT INTO studio_set(name, source, created_at) VALUES ('dup','uploaded','t')"
    )
    await conn.commit()
    cur = await conn.execute("SELECT COUNT(*) FROM studio_set WHERE name='dup'")
    assert (await cur.fetchone())[0] == 2
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_migration_0015_studio_sets.py -v`
Expected: FAIL — `0015_studio_sets.sql` does not exist / `no such table: studio_set`.

- [ ] **Step 3: Write the migration**

```sql
-- backend/migrations/0015_studio_sets.sql
-- 0015: Rename Prompt Studio "folders" to "sets" and add a per-set source
-- discriminator ('archive' | 'uploaded'). See
-- docs/specs/2026-06-08-prompt-studio-sets-navigator-design.md.
--
-- studio_folder.name was a COLUMN-LEVEL UNIQUE, which can't be dropped in
-- place, so studio_set is built fresh (adds `source`, changes uniqueness to
-- (source, name)) and rows are copied. studio_folder_clip becomes
-- studio_set_clip (folder_id → set_id), FK repointed to studio_set.
--
-- Drop order is child-before-parent so it is safe whether or not foreign
-- keys are enforced.
PRAGMA foreign_keys = OFF;

CREATE TABLE studio_set (
  id         INTEGER PRIMARY KEY,
  name       TEXT    NOT NULL,
  source     TEXT    NOT NULL DEFAULT 'archive'
                     CHECK (source IN ('archive','uploaded')),
  created_at TEXT    NOT NULL
);

INSERT INTO studio_set (id, name, source, created_at)
  SELECT id, name, 'archive', created_at FROM studio_folder;

CREATE UNIQUE INDEX studio_set_source_name ON studio_set(source, name);

CREATE TABLE studio_set_clip (
  set_id   INTEGER NOT NULL REFERENCES studio_set(id) ON DELETE CASCADE,
  clip_id  INTEGER NOT NULL,
  added_at TEXT    NOT NULL,
  PRIMARY KEY (set_id, clip_id)
);

INSERT INTO studio_set_clip (set_id, clip_id, added_at)
  SELECT folder_id, clip_id, added_at FROM studio_folder_clip;

DROP TABLE studio_folder_clip;
DROP TABLE studio_folder;
```

- [ ] **Step 4: Run it to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/test_migration_0015_studio_sets.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add backend/migrations/0015_studio_sets.sql tests/unit/test_migration_0015_studio_sets.py
git commit -m "feat(studio): migration 0015 — studio_folder→studio_set + source column"
```

---

### Task 2: Rename repo → `StudioSetsRepo` (+ `source` params)

**Files:**
- Rename + rewrite: `backend/app/repositories/studio_folders.py` → `backend/app/repositories/studio_sets.py`
- Modify: `backend/app/context.py` (import line 60, field line 101, LiveCtx property lines 295–296)
- Rename + rewrite test: `tests/unit/test_studio_folders_repo.py` → `tests/unit/test_studio_sets_repo.py`

- [ ] **Step 1: Move the files (git mv) and rewrite the test first**

```bash
git mv backend/app/repositories/studio_folders.py backend/app/repositories/studio_sets.py
git mv tests/unit/test_studio_folders_repo.py tests/unit/test_studio_sets_repo.py
```

Replace the **entire** contents of `tests/unit/test_studio_sets_repo.py`:

```python
"""StudioSetsRepo — create/list/rename/delete sets + clip membership,
partitioned by source."""

from pathlib import Path

import aiosqlite
import pytest

from backend.app.db import open_db
from backend.app.migrations_runner import apply_migrations
from backend.app.repositories.studio_sets import StudioSetsRepo


@pytest.fixture
async def db(tmp_path: Path):
    db_path = tmp_path / "test.db"
    cm = open_db(db_path)
    conn = await cm.__aenter__()
    await apply_migrations(conn, Path("backend/migrations"))
    yield conn
    await cm.__aexit__(None, None, None)


@pytest.mark.asyncio
async def test_create_and_list_set(db: aiosqlite.Connection):
    repo = StudioSetsRepo()
    sid = await repo.create_set(db, name="edge_cases")
    rows = await repo.list_sets_with_counts(db, source="archive")
    assert len(rows) == 1
    assert rows[0]["id"] == sid
    assert rows[0]["name"] == "edge_cases"
    assert rows[0]["source"] == "archive"
    assert rows[0]["clip_count"] == 0


@pytest.mark.asyncio
async def test_list_partitions_by_source(db):
    repo = StudioSetsRepo()
    await repo.create_set(db, name="a", source="archive")
    await repo.create_set(db, name="u", source="uploaded")
    archive = await repo.list_sets_with_counts(db, source="archive")
    uploaded = await repo.list_sets_with_counts(db, source="uploaded")
    assert [r["name"] for r in archive] == ["a"]
    assert [r["name"] for r in uploaded] == ["u"]


@pytest.mark.asyncio
async def test_unique_set_name_per_source(db):
    repo = StudioSetsRepo()
    await repo.create_set(db, name="x", source="archive")
    with pytest.raises(aiosqlite.IntegrityError):
        await repo.create_set(db, name="x", source="archive")
    # Same name in another source is allowed.
    await repo.create_set(db, name="x", source="uploaded")


@pytest.mark.asyncio
async def test_clip_total_for_source(db):
    repo = StudioSetsRepo()
    sid = await repo.create_set(db, name="a", source="archive")
    await repo.add_clips(db, sid, clip_ids=[1, 2, 3])
    assert await repo.clip_total_for_source(db, source="archive") == 3
    assert await repo.clip_total_for_source(db, source="uploaded") == 0


@pytest.mark.asyncio
async def test_add_and_list_clips(db):
    repo = StudioSetsRepo()
    sid = await repo.create_set(db, name="f1")
    added = await repo.add_clips(db, sid, clip_ids=[12041, 12042, 12041])  # dedupe
    assert added == 2
    clips = await repo.list_clips(db, sid)
    assert sorted(c["clip_id"] for c in clips) == [12041, 12042]


@pytest.mark.asyncio
async def test_remove_clip(db):
    repo = StudioSetsRepo()
    sid = await repo.create_set(db, name="f1")
    await repo.add_clips(db, sid, clip_ids=[12041, 12042])
    await repo.remove_clip(db, sid, clip_id=12041)
    clips = await repo.list_clips(db, sid)
    assert [c["clip_id"] for c in clips] == [12042]


@pytest.mark.asyncio
async def test_rename_set(db):
    repo = StudioSetsRepo()
    sid = await repo.create_set(db, name="old")
    await repo.rename_set(db, sid, name="new")
    rows = await repo.list_sets_with_counts(db, source="archive")
    assert rows[0]["name"] == "new"


@pytest.mark.asyncio
async def test_delete_set_cascades_clips(db):
    repo = StudioSetsRepo()
    sid = await repo.create_set(db, name="f1")
    await repo.add_clips(db, sid, clip_ids=[12041])
    await repo.delete_set(db, sid)
    rows = await repo.list_sets_with_counts(db, source="archive")
    assert rows == []
    cur = await db.execute("SELECT COUNT(*) FROM studio_set_clip WHERE set_id = ?", (sid,))
    assert (await cur.fetchone())[0] == 0


@pytest.mark.asyncio
async def test_set_id_for_clip(db):
    repo = StudioSetsRepo()
    sid = await repo.create_set(db, name="f1")
    await repo.add_clips(db, sid, clip_ids=[42])
    assert await repo.set_id_for_clip(db, 42) == sid
    assert await repo.set_id_for_clip(db, 999) is None
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_studio_sets_repo.py -v`
Expected: FAIL — `ModuleNotFoundError`/`StudioSetsRepo` not defined (file still has old class).

- [ ] **Step 3: Rewrite the repo**

Replace the **entire** contents of `backend/app/repositories/studio_sets.py`:

```python
"""StudioSetsRepo — flat, single-source sets of clips for the studio.

A *set* is one level deep (no nesting) and belongs to exactly one `source`
('archive' | 'uploaded'). Set names are unique per source
(`UNIQUE(source, name)`). Removing a set cascades to its clip memberships
via ON DELETE CASCADE.
"""

from datetime import UTC, datetime
from typing import Any

import aiosqlite


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class StudioSetsRepo:
    async def create_set(
        self, conn: aiosqlite.Connection, *, name: str, source: str = "archive"
    ) -> int:
        cur = await conn.execute(
            "INSERT INTO studio_set(name, source, created_at) VALUES (?, ?, ?)",
            (name, source, _now_iso()),
        )
        sid = cur.lastrowid
        assert sid is not None
        await conn.commit()
        return sid

    async def rename_set(
        self, conn: aiosqlite.Connection, set_id: int, *, name: str
    ) -> None:
        await conn.execute(
            "UPDATE studio_set SET name = ? WHERE id = ?", (name, set_id)
        )
        await conn.commit()

    async def delete_set(self, conn: aiosqlite.Connection, set_id: int) -> None:
        await conn.execute("DELETE FROM studio_set WHERE id = ?", (set_id,))
        await conn.commit()

    async def list_sets_with_counts(
        self, conn: aiosqlite.Connection, *, source: str = "archive"
    ) -> list[dict[str, Any]]:
        cur = await conn.execute(
            """
            SELECT s.id, s.name, s.source, s.created_at,
                   COALESCE(COUNT(sc.clip_id), 0) AS clip_count
            FROM studio_set s
            LEFT JOIN studio_set_clip sc ON sc.set_id = s.id
            WHERE s.source = ?
            GROUP BY s.id
            ORDER BY s.name
            """,
            (source,),
        )
        return [
            {
                "id": r[0],
                "name": r[1],
                "source": r[2],
                "created_at": r[3],
                "clip_count": r[4],
            }
            for r in await cur.fetchall()
        ]

    async def clip_total_for_source(
        self, conn: aiosqlite.Connection, *, source: str = "archive"
    ) -> int:
        cur = await conn.execute(
            """
            SELECT COUNT(*)
            FROM studio_set_clip sc
            JOIN studio_set s ON s.id = sc.set_id
            WHERE s.source = ?
            """,
            (source,),
        )
        return int((await cur.fetchone())[0])

    async def add_clips(
        self, conn: aiosqlite.Connection, set_id: int, *, clip_ids: list[int]
    ) -> int:
        """Add clip_ids to set. Returns count of newly added (dedupes)."""
        now = _now_iso()
        added = 0
        for cid in set(clip_ids):
            cur = await conn.execute(
                "INSERT OR IGNORE INTO studio_set_clip(set_id, clip_id, added_at) "
                "VALUES (?, ?, ?)",
                (set_id, cid, now),
            )
            if cur.rowcount:
                added += 1
        await conn.commit()
        return added

    async def remove_clip(
        self, conn: aiosqlite.Connection, set_id: int, *, clip_id: int
    ) -> None:
        await conn.execute(
            "DELETE FROM studio_set_clip WHERE set_id = ? AND clip_id = ?",
            (set_id, clip_id),
        )
        await conn.commit()

    async def list_clips(
        self, conn: aiosqlite.Connection, set_id: int
    ) -> list[dict[str, Any]]:
        cur = await conn.execute(
            "SELECT clip_id, added_at FROM studio_set_clip "
            "WHERE set_id = ? ORDER BY added_at DESC",
            (set_id,),
        )
        return [{"clip_id": r[0], "added_at": r[1]} for r in await cur.fetchall()]

    async def set_id_for_clip(
        self, conn: aiosqlite.Connection, clip_id: int
    ) -> int | None:
        """Lowest set_id containing `clip_id`, or None if not in any set.

        A clip can live in multiple sets; callers that need "the" set
        (e.g. studio page auto-expand) accept the deterministic-but-arbitrary
        pick.
        """
        cur = await conn.execute(
            "SELECT set_id FROM studio_set_clip "
            "WHERE clip_id = ? ORDER BY set_id LIMIT 1",
            (clip_id,),
        )
        row = await cur.fetchone()
        return int(row[0]) if row is not None else None
```

- [ ] **Step 4: Wire the rename into `context.py`**

In `backend/app/context.py`:
- Line ~60: `from backend.app.repositories.studio_folders import StudioFoldersRepo`
  → `from backend.app.repositories.studio_sets import StudioSetsRepo`
- Line ~101: `studio_folders_repo: StudioFoldersRepo = field(default_factory=StudioFoldersRepo)`
  → `studio_sets_repo: StudioSetsRepo = field(default_factory=StudioSetsRepo)`
- Lines ~295–296 (LiveCtx property):

```python
    @property
    def studio_sets_repo(self) -> StudioSetsRepo:
        return self.core.studio_sets_repo
```

Verify no other reference remains:

```bash
grep -rn "studio_folders_repo\|StudioFoldersRepo\|studio_folders import" backend/app
```
Expected: no matches.

- [ ] **Step 5: Run repo test + context drift guard**

Run: `.venv/bin/python -m pytest tests/unit/test_studio_sets_repo.py tests/unit/test_context_delegation.py tests/unit/test_context_split.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/repositories/studio_sets.py backend/app/context.py tests/unit/test_studio_sets_repo.py
git commit -m "refactor(studio): StudioFoldersRepo→StudioSetsRepo with per-source queries"
```

---

## Phase 1 — API + page routes

### Task 3: Rename `/api/studio/sets` routes (+ `?source=`)

**Files:**
- Modify: `backend/app/routes/studio.py`
- Modify tests: `tests/integration/test_studio_api.py`, `tests/integration/test_studio_folders_htmx_partials.py` (→ git mv to `test_studio_sets_htmx_partials.py`)

- [ ] **Step 1: Update the API test first**

In `tests/integration/test_studio_api.py`, replace every `/api/studio/folders` with `/api/studio/sets`, and update the two source-aware assertions. Concretely, rewrite the three folder tests:

```python
def test_create_list_rename_delete_set(client):
    r = client.post("/api/studio/sets", json={"name": "edge_cases"})
    assert r.status_code == 201
    sid = r.json()["id"]

    r = client.get("/api/studio/sets")
    assert r.status_code == 200
    sets = r.json()
    assert len(sets) == 1
    assert sets[0]["name"] == "edge_cases"
    assert sets[0]["clip_count"] == 0

    r = client.patch(f"/api/studio/sets/{sid}", json={"name": "rare"})
    assert r.status_code == 200
    r = client.get("/api/studio/sets")
    assert r.json()[0]["name"] == "rare"

    r = client.delete(f"/api/studio/sets/{sid}")
    assert r.status_code == 204
    r = client.get("/api/studio/sets")
    assert r.json() == []


def test_duplicate_set_name_rejected(client):
    client.post("/api/studio/sets", json={"name": "x"})
    r = client.post("/api/studio/sets", json={"name": "x"})
    assert r.status_code == 409


def test_uploaded_source_is_separate_list(client):
    client.post("/api/studio/sets", json={"name": "a"})  # default source=archive
    r = client.get("/api/studio/sets?source=uploaded")
    assert r.status_code == 200
    assert r.json() == []
    r = client.get("/api/studio/sets?source=archive")
    assert [s["name"] for s in r.json()] == ["a"]


def test_add_list_remove_clips(client):
    r = client.post("/api/studio/sets", json={"name": "f"})
    sid = r.json()["id"]

    r = client.post(f"/api/studio/sets/{sid}/clips", json={"clip_ids": [12041, 12042]})
    assert r.status_code == 200
    assert r.json()["added"] == 2

    r = client.get(f"/api/studio/sets/{sid}/clips")
    assert r.status_code == 200
    clips = r.json()
    assert sorted(c["clip_id"] for c in clips) == [12041, 12042]
```

Also `git mv tests/integration/test_studio_folders_htmx_partials.py tests/integration/test_studio_sets_htmx_partials.py` and inside it replace `/api/studio/folders`→`/api/studio/sets`, `/studio/_folder`→`/studio/_set`, `_studio_folder`→`_studio_set`, `folder_id`→`set_id`, `.studio-folder`→`.studio-set` to match the new routes/markup. (Run the grep in Step 4 to find every line.)

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/integration/test_studio_api.py -v`
Expected: FAIL — 404 on `/api/studio/sets` (routes still `/folders`).

- [ ] **Step 3: Rewrite the routes**

In `backend/app/routes/studio.py`:
- Rename Pydantic models: `FolderCreate`→`SetCreate`, `FolderPatch`→`SetPatch` (keep `AddClips`, `RunCreate`).
- Replace the whole `# ── folders ──` + `# ── folder clips ──` blocks with:

```python
# ── sets ─────────────────────────────────────────────────────────────────────


@router.get("/sets")
async def list_sets(request: Request, source: str = "archive"):
    ctx = get_core_ctx(request)
    return await ctx.studio_sets_repo.list_sets_with_counts(ctx.db, source=source)


@router.post("/sets", status_code=status.HTTP_201_CREATED)
async def create_set(
    request: Request,
    body: SetCreate,
    source: str = "archive",
    hx_request: str | None = Header(None, alias="HX-Request"),
):
    ctx = get_core_ctx(request)
    try:
        sid = await ctx.studio_sets_repo.create_set(ctx.db, name=body.name, source=source)
    except aiosqlite.IntegrityError as exc:
        raise HTTPException(409, f"set name {body.name!r} already exists") from exc

    if hx_request == "true":
        s = {"id": sid, "name": body.name, "clip_count": 0}
        return templates.TemplateResponse(
            request,
            "pages/_studio_set_card.html",
            {"f": s, "active_version": None, "focused_clip_id": None},
        )
    return {"id": sid}


@router.patch("/sets/{set_id}")
async def rename_set(request: Request, set_id: int, body: SetPatch):
    ctx = get_core_ctx(request)
    try:
        await ctx.studio_sets_repo.rename_set(ctx.db, set_id, name=body.name)
    except aiosqlite.IntegrityError as exc:
        raise HTTPException(409, f"set name {body.name!r} already exists") from exc
    return {"id": set_id, "name": body.name}


@router.delete("/sets/{set_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_set(request: Request, set_id: int):
    ctx = get_core_ctx(request)
    await ctx.studio_sets_repo.delete_set(ctx.db, set_id)
    return Response(status_code=204)


# ── set clips ─────────────────────────────────────────────────────────────────


@router.get("/sets/{set_id}/clips")
async def list_set_clips(request: Request, set_id: int):
    ctx = get_core_ctx(request)
    return await ctx.studio_sets_repo.list_clips(ctx.db, set_id)


@router.post("/sets/{set_id}/clips")
async def add_set_clips(
    request: Request,
    set_id: int,
    body: AddClips,
    hx_request: str | None = Header(None, alias="HX-Request"),
):
    ctx = get_core_ctx(request)
    added = await ctx.studio_sets_repo.add_clips(ctx.db, set_id, clip_ids=body.clip_ids)
    if hx_request == "true":
        clips = await ctx.studio_sets_repo.list_clips(ctx.db, set_id)
        return templates.TemplateResponse(
            request,
            "pages/_studio_set.html",
            {"clips": clips, "set_id": set_id},
        )
    return {"added": added}


@router.delete("/sets/{set_id}/clips/{clip_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_set_clip(request: Request, set_id: int, clip_id: int):
    ctx = get_core_ctx(request)
    await ctx.studio_sets_repo.remove_clip(ctx.db, set_id, clip_id=clip_id)
    return Response(status_code=204)
```

Leave the `# ── runs ──` section untouched. Update the module docstring's first line to say "sets, set_clips, runs".

- [ ] **Step 4: Verify no stragglers in routes**

```bash
grep -rn "studio_folders_repo\|/folders\|FolderCreate\|FolderPatch\|folder_id" backend/app/routes/studio.py
```
Expected: no matches.

- [ ] **Step 5: Run the API + htmx-partial tests**

Run: `.venv/bin/python -m pytest tests/integration/test_studio_api.py tests/integration/test_studio_sets_htmx_partials.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/routes/studio.py tests/integration/test_studio_api.py tests/integration/test_studio_sets_htmx_partials.py
git commit -m "refactor(studio): /api/studio/folders → /api/studio/sets (+ ?source=)"
```

---

### Task 4: Page routes — rename + `archive_available` + source

**Files:**
- Modify: `backend/app/routes/pages/studio.py`
- Modify test: `tests/integration/test_studio_page.py`

- [ ] **Step 1: Add the failing assertions to the page test**

Append to `tests/integration/test_studio_page.py` (uses the same `client` fixture style as `test_studio_api.py` — copy it if not present):

```python
def test_studio_page_renders_source_tabs(client):
    r = client.get("/studio")
    assert r.status_code == 200
    html = r.text
    # Both tabs present; Archive is hidden only when no archive is connected.
    assert 'data-nav-source="uploaded"' in html
    assert "Uploads coming soon" in html  # the stub copy exists in the panel


def test_studio_sets_partial_partitions_by_source(client):
    # Create one archive set, then ask the uploaded partial — must be empty.
    client.post("/api/studio/sets", json={"name": "a"})
    r = client.get("/studio/_sets?source=uploaded")
    assert r.status_code == 200
    assert "a" not in r.text or "studio-set-card" not in r.text
```

> Note: in the `TestClient` test app there is no live archive (`live_ctx` is None), so `archive_available` is False and the Archive tab is hidden — the assertions target the always-present Uploaded tab. A live-archive variant is covered by the manual flow.

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/integration/test_studio_page.py -k source -v`
Expected: FAIL — strings not present.

- [ ] **Step 3: Update the page routes**

In `backend/app/routes/pages/studio.py`:

1. Add a helper near `_archive`:

```python
def _archive_available(request: Request) -> bool:
    """True when a live archive provider is wired (hide the Archive tab when
    False, e.g. cloud deployments with no archive)."""
    live = request.app.state.live_ctx
    return live is not None and live.archive is not None
```

2. In `studio_page`, replace the folder lookups:

```python
    archive_available = _archive_available(request)
    nav_source = "archive" if archive_available else "uploaded"
    sets = await ctx.studio_sets_repo.list_sets_with_counts(ctx.db, source="archive")
    archive_clip_total = await ctx.studio_sets_repo.clip_total_for_source(
        ctx.db, source="archive"
    )
```

   and replace `folder_id_for_clip` with `set_id_for_clip`:

```python
    focused_set_id: int | None = None
    if clip_id is not None:
        focused_set_id = await ctx.studio_sets_repo.set_id_for_clip(ctx.db, clip_id)
```

   In the `TemplateResponse` context, replace the `"folders"`/`"focused_folder_id"` keys with:

```python
            "sets": sets,
            "archive_available": archive_available,
            "nav_source": nav_source,
            "archive_clip_total": archive_clip_total,
            "focused_clip_id": clip_id,
            "focused_set_id": focused_set_id,
```

3. Replace the `_studio_folders` route with `_studio_sets`:

```python
@router.get("/studio/_sets", response_class=HTMLResponse)
async def _studio_sets(request: Request, source: str = "archive"):
    ctx = get_core_ctx(request)
    sets = await ctx.studio_sets_repo.list_sets_with_counts(ctx.db, source=source)
    return templates.TemplateResponse(
        request,
        "pages/_studio_set_list.html",
        {"sets": sets, "active_version": None, "nav_source": source},
    )
```

4. Replace the `_studio_folder` route with `_studio_set` (rename param `folder_id`→`set_id`, repo call, add `c["fps"]` for the timecode overlay):

```python
@router.get("/studio/_set", response_class=HTMLResponse)
async def _studio_set(
    request: Request,
    set_id: int,
    active_version_id: int | None = None,
    clip_id: int | None = None,
):
    """Expanded set view — clip cards with run-dots."""
    ctx = get_core_ctx(request)
    archive = _archive(request)
    clips_rows = await ctx.studio_sets_repo.list_clips(ctx.db, set_id)

    enriched = []
    for c in clips_rows:
        versions = await ctx.studio_runs_repo.versions_run_on_clip(
            ctx.db, clip_id=c["clip_id"]
        )
        has_cur = active_version_id is not None and active_version_id in versions
        has_other = any(v != active_version_id for v in versions)
        meta: dict = {
            "name": f"clip-{c['clip_id']}",
            "duration_secs": None,
            "year": None,
            "fps": 25.0,
        }
        if archive is not None:
            try:
                clip = await archive.get_clip(str(c["clip_id"]))
                meta = {
                    "name": clip.name,
                    "duration_secs": clip.duration_secs,
                    "year": (clip.provider_data or {}).get("pragafilm.rok.natoceni"),
                    "fps": float(clip.fps or 25.0),
                }
            except Exception:  # noqa: BLE001
                pass
        enriched.append({**c, **meta, "has_cur": has_cur, "has_other": has_other})

    return templates.TemplateResponse(
        request,
        "pages/_studio_set.html",
        {"set_id": set_id, "clips": enriched, "focused_clip_id": clip_id},
    )
```

5. In `_studio_archive_picker`, rename the query param + template var `folder_id`→`set_id`:

```python
@router.get("/studio/_archive_picker", response_class=HTMLResponse)
async def _studio_archive_picker(request: Request, set_id: int):
    return templates.TemplateResponse(
        request,
        "pages/_studio_archive_picker.html",
        {"set_id": set_id},
    )
```

- [ ] **Step 4: Verify no stragglers**

```bash
grep -rn "studio_folders_repo\|folder_id_for_clip\|_studio_folders\|_studio_folder\b\|focused_folder\|\"folders\"" backend/app/routes/pages/studio.py
```
Expected: no matches.

- [ ] **Step 5: Run the page test (will still fail on missing templates — that's Task 5)**

Run: `.venv/bin/python -m pytest tests/integration/test_studio_page.py -k source -v`
Expected: FAIL with `TemplateNotFound: pages/_studio_set_list.html` — proceed to Task 5; this test goes green there. Commit anyway so the route rename is captured.

- [ ] **Step 6: Commit**

```bash
git add backend/app/routes/pages/studio.py tests/integration/test_studio_page.py
git commit -m "refactor(studio): page routes folder→set + archive_available/source context"
```

---

## Phase 2 — Templates + CSS

### Task 5: Rename + rewrite the navigator templates

**Files:**
- `git mv` + rewrite: `_studio_folder_list.html` → `_studio_set_list.html`
- `git mv` + rewrite: `_studio_folder_card.html` → `_studio_set_card.html`
- `git mv` + rewrite: `_studio_folder.html` → `_studio_set.html`
- `git mv` + rewrite: `_studio_clip_card.html` → `_studio_set_clip_card.html`
- Modify: `_studio_archive_picker.html` (param rename), `studio.html` (include rename + tab bar in Task 6)

- [ ] **Step 1: Move the template files**

```bash
cd backend/app/templates/pages
git mv _studio_folder_list.html _studio_set_list.html
git mv _studio_folder_card.html _studio_set_card.html
git mv _studio_folder.html _studio_set.html
git mv _studio_clip_card.html _studio_set_clip_card.html
cd -
```

- [ ] **Step 2: Rewrite `_studio_set_list.html`**

```jinja
{# Set list with single-expand-at-a-time behavior. Each set header toggles an
   inline panel that HTMX-loads its clip cards from /studio/_set?set_id=X.
   "+ Add from archive" inside the expanded panel opens the archive picker. #}
<div class="studio-sets" x-data="studioSets({{ focused_set_id or 'null' }})">
  <div class="studio-sets-hdr">
    <span class="muted" x-text="setCountLabel()"></span>
    <span class="grow"></span>
    <button class="btn ghost sm" @click="newSetOpen = !newSetOpen" title="New set">+</button>
  </div>

  <div x-show="newSetOpen" x-cloak class="studio-set-new">
    <input type="text" class="txt sm" placeholder="set name…"
           x-model="newSetName" @keyup.enter="createSet()" />
    <button class="btn primary sm" @click="createSet()">Create</button>
  </div>

  <div class="studio-sets-list">
    {% for f in sets %}
      {% include "pages/_studio_set_card.html" with context %}
    {% endfor %}
    {% if not sets %}
      <div class="studio-sets-empty muted">No sets yet. Create one above.</div>
    {% endif %}
  </div>
</div>
```

- [ ] **Step 3: Rewrite `_studio_set_card.html`** (keeps `f` as the loop var so the HTMX create-response context is unchanged; adds a set-level select checkbox + selected/total badge)

```jinja
{# Single set card — rendered both inside _studio_set_list.html's loop and as
   the HTMX response to POST /api/studio/sets. Expects `f` (set) with
   id / name / clip_count. #}
<div class="studio-set" data-set-id="{{ f.id }}" :class="expandedId === {{ f.id }} && 'open'">
  <div class="studio-set-row">
    <input type="checkbox" class="set-check"
           @click.stop="toggleSet({{ f.id }})"
           :checked="setFullySelected({{ f.id }})" />
    <span class="twist" @click="toggle({{ f.id }})"
          x-text="expandedId === {{ f.id }} ? '▾' : '▸'"></span>
    <span class="name" @click="toggle({{ f.id }})">{{ f.name }}</span>
    <span class="count" x-text="setBadge({{ f.id }}, {{ f.clip_count }})">{{ f.clip_count }}</span>
  </div>
  <div class="studio-set-kids" x-show="expandedId === {{ f.id }}" x-cloak
       hx-get="/studio/_set?set_id={{ f.id }}{% if active_version %}&active_version_id={{ active_version.id }}{% endif %}{% if focused_clip_id %}&clip_id={{ focused_clip_id }}{% endif %}"
       hx-trigger="intersect once"
       hx-swap="innerHTML">
    <div class="muted">loading…</div>
  </div>
</div>
```

- [ ] **Step 4: Rewrite `_studio_set.html`**

```jinja
{% for c in clips %}
  {% include "pages/_studio_set_clip_card.html" with context %}
{% endfor %}
{% if not clips %}
  <div class="muted" style="padding:8px 4px">Empty set.</div>
{% endif %}
<button class="btn ghost mini studio-add-from-archive"
        hx-get="/studio/_archive_picker?set_id={{ set_id }}"
        hx-target="#modal-root" hx-swap="innerHTML">
  + Add from archive
</button>
```

- [ ] **Step 5: Rewrite `_studio_set_clip_card.html`** (adds selection checkbox + year + SMPTE timecode overlay; keeps `.studio-clip-card` class, run-dots, remove-x; `removeClip` now takes `setId`)

```jinja
{# Clip card — focus on click, select via checkbox, remove on hover X,
   run-dots top-right. Vanilla onclick (HTMX-injected, no x-data). Variables:
     c       — dict: clip_id, name, duration_secs, year, fps, has_cur, has_other
     set_id  — outer set id (used for the remove DELETE call) #}
<div class="studio-clip-card{% if focused_clip_id is defined and focused_clip_id == c.clip_id %} selected{% endif %}"
     onclick="window.studio.focusClip({{ c.clip_id }})"
     data-clip-id="{{ c.clip_id }}">
  <input type="checkbox" class="clip-check"
         onclick="event.stopPropagation(); window.studio.toggleClip({{ c.clip_id }}, this.checked);" />
  <div class="thumb"
       {% set thumb_url = '/api/media/' ~ c.clip_id ~ '/thumb' %}
       style="background-image:url('{{ thumb_url }}')">
    {% if c.year %}<span class="yr">{{ c.year }}</span>{% endif %}
    {% if c.duration_secs %}
      <span class="tc">{{ smpte(c.duration_secs, c.fps or 25.0) }}</span>
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
  <button class="remove-x" title="remove from set"
          onclick="event.stopPropagation(); window.studio.removeClip({{ set_id }}, {{ c.clip_id }}, this);">×</button>
</div>
```

- [ ] **Step 6: Fix `_studio_archive_picker.html` + `studio.html` include**

In `_studio_archive_picker.html`, replace `folderId`/`folder_id` with `setId`/`set_id` (the Alpine component call becomes `archivePicker({{ set_id }})`; grep the file).

In `studio.html` line 27, replace the include:

```jinja
      {% include "pages/_studio_set_list.html" %}
```

(The tab-bar wrapper is added in Task 6.)

- [ ] **Step 7: Run the no-phantom-token + page tests (page-source test still needs JS component `studioSets`, but template render should now succeed)**

Run: `.venv/bin/python -m pytest tests/unit/test_studio_css_no_phantom_tokens.py tests/integration/test_studio_page.py -v`
Expected: page render no longer raises `TemplateNotFound`. Some assertions about `studio-set-card`/tabs may still fail until Task 6 — that's expected; the `-k source` test asserting "Uploads coming soon" passes only after Task 6. Note which remain red.

- [ ] **Step 8: Commit**

```bash
git add backend/app/templates/pages/_studio_set_list.html backend/app/templates/pages/_studio_set_card.html backend/app/templates/pages/_studio_set.html backend/app/templates/pages/_studio_set_clip_card.html backend/app/templates/pages/_studio_archive_picker.html backend/app/templates/pages/studio.html
git commit -m "refactor(studio): rename navigator partials folder→set + richer clip card"
```

---

### Task 6: Source tab-bar + Uploaded stub + archive hiding

**Files:**
- Create: `backend/app/templates/pages/_studio_nav.html`
- Modify: `backend/app/templates/pages/studio.html` (wrap the set list in the nav)
- Test: `tests/integration/test_studio_page.py` (the `-k source` cases from Task 4)

- [ ] **Step 1: Create the tab-bar partial**

```jinja
{# Source tabs for the studio navigator. Archive is hidden when no archive is
   connected (archive_available False). The active list is swapped by HTMX
   from /studio/_sets?source=…; the Uploaded tab is a Spec-A stub. #}
<div class="studio-nav" x-data="studioNav('{{ nav_source }}')">
  <div class="studio-nav-tabs" role="tablist">
    {% if archive_available %}
    <button class="studio-nav-tab" data-nav-source="archive"
            :class="source === 'archive' && 'active'"
            @click="switchSource('archive', $el)">
      <span class="ico">🗄</span> Archive
      <span class="badge">{{ archive_clip_total }}</span>
    </button>
    {% endif %}
    <button class="studio-nav-tab" data-nav-source="uploaded"
            :class="source === 'uploaded' && 'active'"
            @click="switchSource('uploaded', $el)">
      <span class="ico">⤒</span> Uploaded
      <span class="badge">0</span>
    </button>
  </div>

  <div class="studio-nav-body" data-studio-nav-body>
    {% if nav_source == 'uploaded' %}
      {% include "pages/_studio_uploaded_stub.html" %}
    {% else %}
      {% include "pages/_studio_set_list.html" %}
    {% endif %}
  </div>
</div>
```

- [ ] **Step 2: Create the Uploaded stub**

```jinja
{# backend/app/templates/pages/_studio_uploaded_stub.html
   Spec A placeholder. The upload subsystem is Spec B. #}
<div class="studio-uploaded-stub muted">
  <div class="su-ico">☁</div>
  <div class="su-title">Uploads coming soon</div>
  <div class="su-sub">Uploaded clips will appear here.</div>
</div>
```

- [ ] **Step 3: Wrap the include in `studio.html`**

Replace the Task-5 include with:

```jinja
      {% include "pages/_studio_nav.html" %}
```

- [ ] **Step 4: Add the `studioNav` Alpine component** to `backend/app/static/studio.js` (inside the existing `alpine:init` listener, alongside `studioSets`):

```javascript
  Alpine.data('studioNav', (initial = 'archive') => ({
    source: initial,
    async switchSource(next, btn) {
      if (this.source === next) return;
      this.source = next;
      // Clear any cross-tab selection when the source changes.
      Alpine.store('studio').clearSelection();
      const body = document.querySelector('[data-studio-nav-body]');
      if (!body) return;
      try {
        const html = await fetch(`/studio/_sets?source=${next}`).then(r => r.text());
        if (next === 'uploaded') {
          body.innerHTML =
            '<div class="studio-uploaded-stub muted">' +
            '<div class="su-ico">☁</div>' +
            '<div class="su-title">Uploads coming soon</div>' +
            '<div class="su-sub">Uploaded clips will appear here.</div></div>';
        } else {
          body.innerHTML = html;
          window.htmxAlpine.reinit(body);
        }
        localStorage.setItem('studio.navSource', next);
      } catch (err) {
        console.error('switchSource failed', err);
        Alpine.store('toast').push(`Could not load ${next} sets.`, { level: 'error' });
      }
    },
  }));
```

> `clearSelection()` is defined in Task 9. If implementing strictly in order, add a temporary `clearSelection(){}` no-op to the store now and flesh it out in Task 9, or implement Task 9's store changes before wiring this call. Either keeps commits green.

- [ ] **Step 5: Run the page source tests**

Run: `.venv/bin/python -m pytest tests/integration/test_studio_page.py -k source -v`
Expected: PASS — `data-nav-source="uploaded"` and "Uploads coming soon" present; the uploaded partial is empty of archive sets.

- [ ] **Step 6: Commit**

```bash
git add backend/app/templates/pages/_studio_nav.html backend/app/templates/pages/_studio_uploaded_stub.html backend/app/templates/pages/studio.html backend/app/static/studio.js
git commit -m "feat(studio): source tabs (Archive/Uploaded) + uploaded stub + archive hiding"
```

---

### Task 7: CSS — rename selectors + tab/checkbox/overlay styles

**Files:**
- Modify: `backend/app/static/app.css` (lines ~1541, ~1567, ~1653–1821)
- Test: `tests/unit/test_studio_nav_css.py` (new)

- [ ] **Step 1: Write a CSS-presence guard test**

```python
# tests/unit/test_studio_nav_css.py
"""Studio navigator restyle — required selectors exist and old folder
selectors are gone."""

from pathlib import Path

CSS = Path("backend/app/static/app.css").read_text()


def test_set_selectors_replace_folder_selectors():
    assert ".studio-folder" not in CSS  # renamed to .studio-set
    assert ".studio-folders" not in CSS
    assert ".studio-set-row" in CSS
    assert ".studio-sets-list" in CSS


def test_nav_and_card_selectors_present():
    for sel in (
        ".studio-nav-tab",
        ".studio-nav-tab.active",
        ".studio-uploaded-stub",
        ".studio-clip-card .clip-check",
        ".studio-clip-card .thumb .yr",
        ".studio-clip-card .thumb .tc",
    ):
        assert sel in CSS, sel
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_studio_nav_css.py -v`
Expected: FAIL — old `.studio-folder` still present, new selectors missing.

- [ ] **Step 3: Rename the folder selectors**

In `backend/app/static/app.css`, rename every `.studio-folders`→`.studio-sets`, `.studio-folder-new`→`.studio-set-new`, `.studio-folders-hdr`→`.studio-sets-hdr`, `.studio-folders-list`→`.studio-sets-list`, `.studio-folders-empty`→`.studio-sets-empty`, `.studio-folder-row`→`.studio-set-row`, `.studio-folder-kids`→`.studio-set-kids`, `.studio-folder`→`.studio-set` (note: also line ~1541 `.studio-body.no-list .studio-videos` is unaffected — only `folder` tokens change). Do NOT rename `.studio-clip-card` (kept) or `.studio-videos`.

Verify:
```bash
grep -n "studio-folder" backend/app/static/app.css
```
Expected: no matches.

- [ ] **Step 4: Append the new styles** (after the existing `.studio-clip-card:hover .remove-x` block, ~line 1821):

```css
/* ── Source tabs ─────────────────────────────────────────────────── */
.studio-nav { display: flex; flex-direction: column; min-height: 0; height: 100%; }
.studio-nav-tabs {
  display: flex;
  border-bottom: 1px solid var(--line);
}
.studio-nav-tab {
  display: flex; align-items: center; gap: 6px;
  padding: 10px 14px;
  background: none; border: none; cursor: pointer;
  font-size: 13px; font-weight: 600;
  color: var(--text-3);
  border-bottom: 2px solid transparent;
}
.studio-nav-tab.active { color: var(--text-1); border-bottom-color: var(--accent); }
.studio-nav-tab .ico { font-size: 13px; }
.studio-nav-tab .badge {
  font-family: var(--f-mono, monospace); font-size: 10.5px;
  background: var(--surface-2); color: var(--text-3);
  padding: 1px 6px; border-radius: 9px;
}
.studio-nav-body { flex: 1; min-height: 0; display: flex; flex-direction: column; }

/* ── Uploaded stub ───────────────────────────────────────────────── */
.studio-uploaded-stub {
  flex: 1; display: flex; flex-direction: column;
  align-items: center; justify-content: center; gap: 6px;
  padding: 32px 16px; text-align: center;
}
.studio-uploaded-stub .su-ico { font-size: 28px; opacity: .6; }
.studio-uploaded-stub .su-title { font-weight: 600; }
.studio-uploaded-stub .su-sub { font-size: 12px; }

/* ── Selection checkboxes + thumbnail overlays ───────────────────── */
.studio-set-row .set-check, .studio-clip-card .clip-check {
  width: 14px; height: 14px; cursor: pointer; accent-color: var(--accent);
}
.studio-clip-card { grid-template-columns: 16px 64px 1fr; }
.studio-clip-card .clip-check { align-self: center; }
.studio-clip-card .thumb .yr {
  position: absolute; left: 2px; top: 2px;
  font-family: var(--f-mono, monospace); font-size: 9.5px;
  background: rgba(0,0,0,.55); color: #fff; padding: 1px 3px; border-radius: 2px;
}
.studio-clip-card .thumb .tc {
  position: absolute; right: 2px; bottom: 2px;
  font-family: var(--f-mono, monospace); font-size: 9.5px;
  background: rgba(0,0,0,.6); color: #fff; padding: 1px 3px; border-radius: 2px;
}
```

> The `.studio-clip-card` grid was `64px 1fr`; the new checkbox column makes it `16px 64px 1fr`. The old `.thumb .dur` rule is now unused (template emits `.tc`/`.yr`) — leave it or delete it; it does not affect output.

- [ ] **Step 5: Run the CSS test**

Run: `.venv/bin/python -m pytest tests/unit/test_studio_nav_css.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/static/app.css tests/unit/test_studio_nav_css.py
git commit -m "feat(studio): navigator CSS — set rename, source tabs, checkboxes, thumb overlays"
```

---

## Phase 3 — JS: rename + selection + bulk run

### Task 8: Rename JS `studioFolders`→`studioSets` + shim

**Files:**
- Modify: `backend/app/static/studio.js`
- Modify test: `tests/unit/test_studio_archive_picker_js.py` (and any JS-text test referencing `studioFolders`/`folderId` — find via grep)

- [ ] **Step 1: Find every JS reference**

```bash
grep -rn "studioFolders\|newFolderOpen\|newFolderName\|createFolder\|folderId\|/api/studio/folders\|studio-folder\|studio-folders-list\|data-folder-id" backend/app/static/studio.js tests
```
Record the list; every hit gets renamed in this task.

- [ ] **Step 2: Rewrite the `window.studio` shim** (top of `studio.js`):

```javascript
window.studio = {
  _root() {
    return window.Alpine?.store('studio') ?? null;
  },
  focusClip(clipId) {
    this._root()?.focusClip(clipId);
    document.querySelectorAll('.studio-clip-card.selected')
      .forEach(el => el.classList.remove('selected'));
    document.querySelectorAll(`.studio-clip-card[data-clip-id="${clipId}"]`)
      .forEach(el => el.classList.add('selected'));
  },
  toggleClip(clipId, checked) {
    this._root()?.toggleClip(clipId, checked);
  },
  removeClip(setId, clipId, btnEl) {
    if (!confirm('Remove from set?')) return;
    fetch(`/api/studio/sets/${setId}/clips/${clipId}`, {method: 'DELETE'})
      .then(() => btnEl.closest('.studio-clip-card').remove());
  },
};
```

- [ ] **Step 3: Rename the `archivePicker` component** — `folderId`→`setId`, `/api/studio/folders/…`→`/api/studio/sets/…`, `.studio-folder[data-folder-id=…] .studio-folder-kids`→`.studio-set[data-set-id=…] .studio-set-kids`, and the success toast "folder"→"set". The `Alpine.data('archivePicker', (setId) => ({ …, setId, … }))` signature and `this.setId` references update accordingly.

- [ ] **Step 4: Rename `studioFolders`→`studioSets`** component:

```javascript
  Alpine.data('studioSets', (initialExpandedId = null) => ({
    expandedId: initialExpandedId,
    newSetOpen: false,
    newSetName: '',

    toggle(id) { this.expandedId = this.expandedId === id ? null : id; },

    // Selection helpers (state lives in the store; see Task 9).
    toggleSet(id) { Alpine.store('studio').toggleSet(id); },
    setFullySelected(id) { return Alpine.store('studio').setFullySelected(id); },
    setBadge(id, total) { return Alpine.store('studio').setBadge(id, total); },
    setCountLabel() {
      const n = document.querySelectorAll('.studio-sets-list .studio-set').length;
      return `${n} set${n === 1 ? '' : 's'}`;
    },

    async createSet() {
      const name = this.newSetName.trim();
      if (!name) return;
      const res = await fetch('/api/studio/sets', {
        method: 'POST',
        headers: {'Content-Type': 'application/json', 'HX-Request': 'true'},
        body: JSON.stringify({name}),
      });
      if (res.ok) {
        const html = await res.text();
        const list = document.querySelector('.studio-sets-list');
        if (list) {
          list.insertAdjacentHTML('beforeend', html);
          const cards = list.querySelectorAll('.studio-set');
          const newCard = cards[cards.length - 1];
          if (newCard) window.htmxAlpine.reinit(newCard);
          else console.warn('studioSets.createSet: no .studio-set card after insert');
        } else {
          console.warn('studioSets.createSet: .studio-sets-list not found');
        }
        this.newSetName = '';
        this.newSetOpen = false;
        Alpine.store('toast').push(`Created set "${name}".`, { level: 'success' });
      } else if (res.status === 409) {
        Alpine.store('toast').push(`Set "${name}" already exists.`, { level: 'error' });
      } else {
        Alpine.store('toast').push(`Set create failed (HTTP ${res.status}).`, { level: 'error' });
      }
    },
  }));
```

- [ ] **Step 5: Update the JS test(s)**

In `tests/unit/test_studio_archive_picker_js.py` (and any other grep hit), replace `studioFolders`→`studioSets`, `folderId`→`setId`, `/api/studio/folders`→`/api/studio/sets`, `.studio-folder-kids`→`.studio-set-kids`, `data-folder-id`→`data-set-id` to match the rewritten file.

- [ ] **Step 6: Verify + run**

```bash
grep -rn "studioFolders\|newFolderOpen\|createFolder\|/api/studio/folders\|studio-folder" backend/app/static/studio.js
```
Expected: no matches.

Run: `.venv/bin/python -m pytest tests/unit/test_studio_archive_picker_js.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/app/static/studio.js tests/unit/test_studio_archive_picker_js.py
git commit -m "refactor(studio): JS studioFolders→studioSets + set-aware shim"
```

---

### Task 9: Store — selection state + `clearSelection`/`toggleClip`/`toggleSet`

**Files:**
- Modify: `backend/app/static/studioStore.js`
- Test: `tests/unit/test_studio_selection_state.py` (new — exercises the selection logic via a tiny JS harness)

> The repo already has JS-logic unit tests that load a store method into a JS engine or assert on its source. If the project has no JS test runner, mirror the existing pattern in `tests/unit/test_studio_run_button_label.py` / `tests/_helpers/studio_state.py` (a Python mirror of the JS logic). Implement the selection logic in **pure functions** on the store so it can be mirrored. Below uses a source-assertion test consistent with the simplest existing pattern; if a node-based test exists, prefer that.

- [ ] **Step 1: Write the test**

```python
# tests/unit/test_studio_selection_state.py
"""Studio selection state — the store exposes selection methods used by the
navigator checkboxes and the bulk-run bar."""

from pathlib import Path

SRC = Path("backend/app/static/studioStore.js").read_text()


def test_store_declares_selection_api():
    for token in (
        "selectedClipIds",
        "toggleClip(",
        "toggleSet(",
        "clearSelection(",
        "setFullySelected(",
        "setBadge(",
        "runOnSelectedClips(",
        "_runOne(",
    ):
        assert token in SRC, token


def test_bulk_run_bounded_concurrency_constant():
    # The bulk loop must cap in-flight runs (seat/quota protection).
    assert "BULK_RUN_CONCURRENCY" in SRC
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_studio_selection_state.py -v`
Expected: FAIL — tokens absent.

- [ ] **Step 3: Add selection state + methods to the store**

In `backend/app/static/studioStore.js`, add to the store's state block (near `focusedClipId`):

```javascript
    // ── Multi-select (navigator checkboxes → bulk run) ───────────────
    // Set of clip ids selected in the CURRENT source tab. Plain array for
    // Alpine reactivity friendliness; membership ops go through helpers.
    selectedClipIds: [],
    bulkRunning: false,
    bulkDone: 0,
    bulkTotal: 0,
```

Add a module-scope constant near the top (next to `_studioEstimateKey`):

```javascript
// Max concurrent studio runs during a bulk "Run on N clips" — protects the
// single CatDV seat and Gemini quota. See spec §5.
const BULK_RUN_CONCURRENCY = 2;
```

Add these methods to the store (e.g. after `focusClip`):

```javascript
    // ── Selection ────────────────────────────────────────────────────
    isClipSelected(clipId) { return this.selectedClipIds.includes(clipId); },

    toggleClip(clipId, checked) {
      const on = checked ?? !this.isClipSelected(clipId);
      if (on && !this.isClipSelected(clipId)) {
        this.selectedClipIds = [...this.selectedClipIds, clipId];
      } else if (!on) {
        this.selectedClipIds = this.selectedClipIds.filter(id => id !== clipId);
      }
    },

    _clipIdsInSet(setId) {
      const kids = document.querySelector(`.studio-set[data-set-id="${setId}"] .studio-set-kids`);
      if (!kids) return [];
      return [...kids.querySelectorAll('.studio-clip-card[data-clip-id]')]
        .map(el => Number(el.dataset.clipId));
    },

    setFullySelected(setId) {
      const ids = this._clipIdsInSet(setId);
      return ids.length > 0 && ids.every(id => this.isClipSelected(id));
    },

    toggleSet(setId) {
      const ids = this._clipIdsInSet(setId);
      const allOn = ids.length > 0 && ids.every(id => this.isClipSelected(id));
      ids.forEach(id => this.toggleClip(id, !allOn));
      // Reflect into the rendered clip checkboxes (HTMX-injected, no x-model).
      document.querySelectorAll(`.studio-set[data-set-id="${setId}"] .clip-check`)
        .forEach((cb, i) => { cb.checked = !allOn; });
    },

    setBadge(setId, total) {
      const ids = this._clipIdsInSet(setId);
      const sel = ids.filter(id => this.isClipSelected(id)).length;
      return sel > 0 ? `${sel}/${total}` : String(total);
    },

    clearSelection() {
      this.selectedClipIds = [];
      document.querySelectorAll('.studio-clip-card .clip-check, .studio-set .set-check')
        .forEach(cb => { cb.checked = false; });
    },
```

- [ ] **Step 4: Extract `_runOne` and add `runOnSelectedClips`**

Refactor the existing `runOnFocusedClip` POST/poll into a shared `_runOne(clipId)`, then add the bulk loop. Insert after `runOnFocusedClip`:

```javascript
    // One run: POST + poll to terminal. Returns the final status string
    // ('ok' | 'error' | 'cancelled' | null). Shared by single + bulk run.
    async _runOne(clipId) {
      const res = await fetch('/api/studio/runs', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          prompt_version_id: this.activeVersionId,
          clip_id: clipId,
          model: this.activeModel || null,
        }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const {run_id} = await res.json();
      // Poll this run id to terminal status (independent of this.running).
      while (true) {
        await new Promise(r => setTimeout(r, 1000));
        const pr = await fetch(`/api/studio/runs/${run_id}`);
        if (!pr.ok) continue;
        const run = await pr.json();
        if (['ok', 'error', 'cancelled'].includes(run.status)) return run.status;
      }
    },

    // Bulk "Run on N clips": run the active version over every selected clip
    // with bounded concurrency. Per-clip failures toast and the loop
    // continues. Refreshes run-dots by re-fetching the focused clip's set
    // panel is unnecessary; the navigator re-renders run-dots on next expand.
    async runOnSelectedClips() {
      if (!this.activeVersionId || this.bulkRunning) return;
      const ids = [...this.selectedClipIds];
      if (!ids.length) return;
      this.bulkRunning = true;
      this.bulkTotal = ids.length;
      this.bulkDone = 0;
      const queue = ids.slice();
      const worker = async () => {
        while (queue.length) {
          const clipId = queue.shift();
          try {
            const status = await this._runOne(clipId);
            if (status === 'error') {
              Alpine.store('toast').push(`Run failed for clip ${clipId}.`, { level: 'error' });
            }
          } catch (err) {
            console.error('bulk run failed', clipId, err);
            Alpine.store('toast').push(
              `Run failed for clip ${clipId}: ${err.message || String(err)}`,
              { level: 'error' },
            );
          } finally {
            this.bulkDone++;
          }
        }
      };
      try {
        await Promise.all(
          Array.from({length: Math.min(BULK_RUN_CONCURRENCY, ids.length)}, worker)
        );
        Alpine.store('toast').push(
          `Ran ${this.bulkDone} clip${this.bulkDone === 1 ? '' : 's'}.`,
          { level: 'success' },
        );
        this.pendingRunSwap++;  // nudge the focused-clip output to refresh
      } finally {
        this.bulkRunning = false;
      }
    },

    bulkRunLabel() {
      return this.bulkRunning
        ? `Running ${this.bulkDone}/${this.bulkTotal}…`
        : `Run on ${this.selectedClipIds.length} clip${this.selectedClipIds.length === 1 ? '' : 's'}`;
    },
```

- [ ] **Step 5: Run the selection test**

Run: `.venv/bin/python -m pytest tests/unit/test_studio_selection_state.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/static/studioStore.js tests/unit/test_studio_selection_state.py
git commit -m "feat(studio): selection state + bounded-concurrency bulk run in the store"
```

---

### Task 10: Bulk-action bar markup

**Files:**
- Modify: `backend/app/templates/pages/_studio_nav.html` (add the bar)
- Test: `tests/integration/test_studio_page.py` (assert the bar markup renders)

- [ ] **Step 1: Add the failing assertion**

```python
def test_studio_nav_has_bulk_action_bar(client):
    html = client.get("/studio").text
    assert "studio-bulk-bar" in html
    assert "runOnSelectedClips()" in html
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/integration/test_studio_page.py -k bulk -v`
Expected: FAIL — markup absent.

- [ ] **Step 3: Add the bar to `_studio_nav.html`** (inside `.studio-nav`, after `.studio-nav-tabs`):

```jinja
  <div class="studio-bulk-bar" x-show="$store.studio.selectedClipIds.length > 0" x-cloak>
    <button class="btn primary sm"
            :disabled="$store.studio.bulkRunning || !$store.studio.activeVersionId"
            @click="$store.studio.runOnSelectedClips()"
            x-text="$store.studio.bulkRunLabel()">Run on 0 clips</button>
    <button class="btn ghost sm" @click="$store.studio.clearSelection()">Clear</button>
  </div>
```

- [ ] **Step 4: Add minimal CSS** to `app.css` (after the tab styles from Task 7):

```css
.studio-bulk-bar {
  display: flex; align-items: center; gap: 8px;
  padding: 8px 12px; border-bottom: 1px solid var(--line);
  background: var(--surface-2);
}
```

- [ ] **Step 5: Run the test**

Run: `.venv/bin/python -m pytest tests/integration/test_studio_page.py -k bulk -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/templates/pages/_studio_nav.html backend/app/static/app.css tests/integration/test_studio_page.py
git commit -m "feat(studio): bulk-action bar (Run on N clips / Clear)"
```

---

## Phase 4 — Sweep, guards, and verification

### Task 11: Straggler sweep + full suite + lint

**Files:** any remaining references; no new production code expected.

- [ ] **Step 1: Grep the whole tree for leftover "folder" references in studio surfaces**

```bash
grep -rn "studio_folder\|studio-folder\|studioFolders\|/api/studio/folders\|/studio/_folder\|StudioFoldersRepo\|studio_folders_repo\|folder_id_for_clip\|_studio_folder" \
  backend/app tests | grep -v "studio_run"
```
Expected: **no matches.** Fix any straggler in the file it appears in, plus its test, and commit with `chore(studio): sweep remaining folder→set references`.

- [ ] **Step 2: Run the N+1 / query-count guard for the sets list**

Add (or extend) a perf test mirroring the existing pattern in `tests/integration/test_clips_page_perf.py`, asserting the `/studio/_sets` render statement count does not scale with set count:

```python
# tests/integration/test_studio_sets_perf.py
"""Sets-list render must not be N+1 in the number of sets."""

import pytest

from tests._helpers.query_count import assert_query_count


@pytest.mark.asyncio
async def test_sets_list_query_count_flat(studio_app_db):
    """`list_sets_with_counts` is a single grouped query regardless of N.

    `studio_app_db` is the project's app+db fixture; if absent, build a
    StudioSetsRepo over an in-memory migrated db like test_studio_sets_repo.py
    and call list_sets_with_counts for 10 vs 100 sets, asserting equal counts.
    """
    from backend.app.repositories.studio_sets import StudioSetsRepo
    repo = StudioSetsRepo()
    conn = studio_app_db
    for i in range(100):
        await repo.create_set(conn, name=f"s{i}")
    async with assert_query_count(conn, 1):
        await repo.list_sets_with_counts(conn, source="archive")
```

> If there is no `studio_app_db` fixture, reuse the `db` fixture from
> `tests/unit/test_studio_sets_repo.py` (copy it into this file). The
> assertion is: `list_sets_with_counts` issues exactly one SQL statement.

Run: `.venv/bin/python -m pytest tests/integration/test_studio_sets_perf.py -v`
Expected: PASS (1 statement).

- [ ] **Step 3: Run the entire studio test set**

Run: `.venv/bin/python -m pytest tests -k studio -q`
Expected: all green. Investigate and fix any red (most likely a missed rename in a test asserting old markup/route).

- [ ] **Step 4: Run import-linter + the architecture guards**

Run:
```bash
.venv/bin/python -m pytest tests/unit/test_templates_shared.py tests/unit/test_context_delegation.py tests/unit/test_no_x_data_stack.py tests/unit/test_htmx_alpine_single_lifecycle.py -q
lint-imports
```
Expected: PASS / "Contracts: N kept, 0 broken".

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: green (or only pre-existing unrelated failures — note them, don't "fix" by deleting assertions).

- [ ] **Step 6: Final commit**

```bash
git add tests/integration/test_studio_sets_perf.py
git commit -m "test(studio): N+1 guard on sets-list render + straggler sweep"
```

---

## Manual acceptance flows (from the spec — run on a live app)

Start the server via the `server-start` skill (it enforces seat discipline). With the archive connected and at least one prompt version + a couple of archive sets:

1. **Rename complete.** `/studio` says "set" everywhere; create/rename/delete a set, add clips via the archive picker, remove a clip — all update in place (no full reload).
2. **Source tabs.** Archive + Uploaded tabs; Archive default with a count; clicking Uploaded shows "Uploads coming soon"; reload remembers the last tab.
3. **No archive.** Run with no `live_ctx.archive` (or stop the archive): Archive tab hidden, Uploaded selected + stub, rest of studio navigable.
4. **Restyled cards.** Clip cards show thumbnail + year + SMPTE timecode, a selection checkbox, name, `id:N · year`, run-dots; clicking a card focuses it and loads the player.
5. **Bulk run.** Tick 3 clips (or a set checkbox) → "Run on 3 clips" → progress 1/3→3/3 → all three gain the active-version run-dot, outputs saved; force one failure → toast appears, the other two still complete.
6. **Regressions.** Single-clip focus+run still works; the archive picker still searches and adds; with the archive offline the picker shows its existing clear error.

---

## Self-review notes (author)

- **Spec coverage:** §1 rename → Tasks 1–8,11; §2 tabs → Task 6; §3 archive-absent → Tasks 4,6; §4 restyle → Tasks 5,7; §5 selection+bulk run → Tasks 9,10; §6 error/offline → Tasks 9 (toasts, no reload) + 4 (DB-backed); §7 reuse → reuses `/api/studio/runs`, `clipPickerCore`, `htmxAlpine`, `smpte`/`fmtTimecode`; §8 tests → each task is TDD + Task 11 perf/guard.
- **Type/name consistency:** `set_id` used in routes, templates, JS, and SQL; `studio_sets_repo` used in both `CoreCtx` field and `LiveCtx` property; `_runOne`/`runOnSelectedClips`/`clearSelection` referenced by Task 6/10 are defined in Task 9 (with the no-op-then-fill note to keep commits green).
- **Known ordering coupling:** Task 6's `studioNav.switchSource` calls `clearSelection()` (defined in Task 9). If executing strictly in order, add the no-op stub as noted, or implement Task 9 before wiring Task 6's call.
