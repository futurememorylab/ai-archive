# Image Annotation Prompt Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give still-image clips a no-timestamp annotation by seeding a dedicated image prompt, tagging every prompt with a `media_kind` (video/image/any), filtering the Annotate dropdown by clip kind, and surfacing/editing the kind in the prompts UI — while keeping image output stored and indexed identically to video output.

**Architecture:** `media_kind` is a prompt-level column (migration 0011, existing rows → `video`). The image prompt reuses the video prompt's `summary_cz`/`decade`/`years` schema keys and target_map targets **minus `scenes`**, which is what guarantees identical `annotations`/`annotations_fts`/`review_items` behaviour. The Annotate dropdown filters client-side by `clip.kind`; the prompts editor sets/edits `media_kind` and shows a kind badge.

**Tech Stack:** Python 3.12, FastAPI, aiosqlite (SQLite + FTS5), Jinja2/Alpine, pytest with an in-process fake CatDV.

**Spec:** `docs/specs/2026-05-26-image-annotation-prompt-design.md`

---

### Task 1: Migration 0011 — `prompts.media_kind` column

**Files:**
- Create: `backend/migrations/0011_prompt_media_kind.sql`
- Test: `tests/integration/test_migration_0011.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_migration_0011.py
from pathlib import Path

import pytest

from backend.app.db import open_db
from backend.app.migrations_runner import apply_migrations

MIGRATIONS = Path(__file__).resolve().parents[2] / "backend" / "migrations"


async def _columns(conn, table: str) -> dict[str, dict]:
    cur = await conn.execute(f"PRAGMA table_info({table})")
    rows = await cur.fetchall()
    return {r[1]: {"type": r[2], "notnull": r[3], "dflt": r[4]} for r in rows}


@pytest.mark.asyncio
async def test_prompts_has_media_kind_column(tmp_path: Path):
    db = tmp_path / "t.db"
    async with open_db(db) as conn:
        await apply_migrations(conn, MIGRATIONS)
        cols = await _columns(conn, "prompts")
    assert "media_kind" in cols
    assert cols["media_kind"]["notnull"] == 1


@pytest.mark.asyncio
async def test_existing_prompts_backfilled_to_video(tmp_path: Path):
    db = tmp_path / "t.db"
    async with open_db(db) as conn:
        # Apply everything up to (but not including) 0011 by inserting a prompt
        # after the full migration set, then assert the backfill value.
        await apply_migrations(conn, MIGRATIONS)
        # A prompt inserted WITHOUT specifying media_kind uses the column default.
        await conn.execute(
            "INSERT INTO prompts(name, description, archived, created_at, updated_at) "
            "VALUES ('p', NULL, 0, '2026-01-01', '2026-01-01')"
        )
        await conn.commit()
        cur = await conn.execute("SELECT media_kind FROM prompts WHERE name='p'")
        assert (await cur.fetchone())[0] == "any"


@pytest.mark.asyncio
async def test_media_kind_check_rejects_invalid(tmp_path: Path):
    import aiosqlite

    db = tmp_path / "t.db"
    async with open_db(db) as conn:
        await apply_migrations(conn, MIGRATIONS)
        with pytest.raises(aiosqlite.IntegrityError):
            await conn.execute(
                "INSERT INTO prompts(name, archived, media_kind, created_at, updated_at) "
                "VALUES ('bad', 0, 'audio', '2026-01-01', '2026-01-01')"
            )
            await conn.commit()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/integration/test_migration_0011.py -v`
Expected: FAIL — `media_kind` not in columns (migration file doesn't exist yet).

- [ ] **Step 3: Write the migration**

```sql
-- backend/migrations/0011_prompt_media_kind.sql
-- 0011: tag each prompt with the media kind it targets so the Annotate
-- dropdown can offer only kind-appropriate prompts. New prompts default to
-- 'any'; existing prompts are video-oriented (the only seed is the
-- scene-marker prompt), so backfill them to 'video'.

ALTER TABLE prompts
  ADD COLUMN media_kind TEXT NOT NULL DEFAULT 'any'
  CHECK (media_kind IN ('video', 'image', 'any'));

UPDATE prompts SET media_kind = 'video';
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/integration/test_migration_0011.py -v`
Expected: PASS (3 tests). Then confirm no migration regressions:
`.venv/bin/pytest tests/integration/test_migration_0002.py tests/integration/test_migration_0006.py tests/integration/test_migration_0007.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/migrations/0011_prompt_media_kind.sql tests/integration/test_migration_0011.py
git commit -m "feat(db): migration 0011 — prompts.media_kind column"
```

---

### Task 2: `Prompt.media_kind` in model + repo

**Files:**
- Modify: `backend/app/models/prompt.py` (Prompt model)
- Modify: `backend/app/repositories/prompts.py` (`_PROMPT_COLS`, `_row_to_prompt`, `create_with_initial_version`, `update_metadata`)
- Test: `tests/integration/test_prompts_repo.py` (new file)

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_prompts_repo.py
from pathlib import Path

import pytest

from backend.app.db import open_db
from backend.app.migrations_runner import apply_migrations
from backend.app.repositories.prompts import PromptsRepo

MIGRATIONS = Path(__file__).resolve().parents[2] / "backend" / "migrations"


async def _repo_db(tmp_path: Path):
    conn = await open_db(tmp_path / "t.db").__aenter__()
    await apply_migrations(conn, MIGRATIONS)
    return PromptsRepo(), conn


@pytest.mark.asyncio
async def test_create_persists_media_kind(tmp_path: Path):
    repo, conn = await _repo_db(tmp_path)
    pid, _ = await repo.create_with_initial_version(
        conn, name="img", description=None, body="b",
        target_map={"summary_cz": {"kind": "note", "target": "t"}},
        output_schema={}, model="m", media_kind="image",
    )
    prompt, _ = await repo.get_with_versions(conn, pid)
    assert prompt.media_kind == "image"


@pytest.mark.asyncio
async def test_create_defaults_media_kind_any(tmp_path: Path):
    repo, conn = await _repo_db(tmp_path)
    pid, _ = await repo.create_with_initial_version(
        conn, name="def", description=None, body="b",
        target_map={}, output_schema={}, model="m",
    )
    prompt, _ = await repo.get_with_versions(conn, pid)
    assert prompt.media_kind == "any"


@pytest.mark.asyncio
async def test_update_metadata_sets_media_kind(tmp_path: Path):
    repo, conn = await _repo_db(tmp_path)
    pid, _ = await repo.create_with_initial_version(
        conn, name="x", description=None, body="b",
        target_map={}, output_schema={}, model="m",
    )
    await repo.update_metadata(conn, pid, media_kind="video")
    prompt, _ = await repo.get_with_versions(conn, pid)
    assert prompt.media_kind == "video"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/integration/test_prompts_repo.py -v`
Expected: FAIL — `create_with_initial_version` has no `media_kind` kwarg / `Prompt` has no `media_kind`.

- [ ] **Step 3: Implement**

In `backend/app/models/prompt.py`, add `media_kind` to `Prompt` (Literal already importable):

```python
class Prompt(BaseModel):
    id: int | None = None
    name: str
    description: str | None = None
    archived: bool = False
    media_kind: Literal["video", "image", "any"] = "any"
    created_at: str
    updated_at: str

    model_config = ConfigDict(extra="allow")
```

In `backend/app/repositories/prompts.py`:

Extend `_PROMPT_COLS` and `_row_to_prompt`:

```python
def _row_to_prompt(row) -> Prompt:
    return Prompt(
        id=row[0],
        name=row[1],
        description=row[2],
        archived=bool(row[3]),
        created_at=row[4],
        updated_at=row[5],
        media_kind=row[6],
    )


_PROMPT_COLS = "id, name, description, archived, created_at, updated_at, media_kind"
```

Add `media_kind` to `create_with_initial_version` (signature + INSERT):

```python
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
        media_kind: str = "any",
    ) -> tuple[int, int]:
        """Create prompt + v1. Returns (prompt_id, version_id)."""
        now = _now_iso()
        cur = await conn.execute(
            "INSERT INTO prompts(name, description, archived, media_kind, created_at, updated_at) "
            "VALUES (?, ?, 0, ?, ?, ?)",
            (name, description, media_kind, now, now),
        )
        prompt_id = cur.lastrowid
        assert prompt_id is not None
        cur = await conn.execute(
            "INSERT INTO prompt_versions(prompt_id, version_num, state, body, target_map, "
            "output_schema, model, created_at, updated_at) "
            "VALUES (?, 1, ?, ?, ?, ?, ?, ?, ?)",
            (
                prompt_id,
                initial_state,
                body,
                _target_map_to_json(target_map),
                json.dumps(output_schema),
                model,
                now,
                now,
            ),
        )
        version_id = cur.lastrowid
        assert version_id is not None
        await conn.commit()
        return prompt_id, version_id
```

Extend `update_metadata` to accept `media_kind`:

```python
    async def update_metadata(
        self,
        conn: aiosqlite.Connection,
        prompt_id: int,
        *,
        name: str | None = None,
        description: str | None = None,
        media_kind: str | None = None,
    ) -> None:
        sets, args = [], []
        if name is not None:
            sets.append("name = ?")
            args.append(name)
        if description is not None:
            sets.append("description = ?")
            args.append(description)
        if media_kind is not None:
            sets.append("media_kind = ?")
            args.append(media_kind)
        if not sets:
            return
        sets.append("updated_at = ?")
        args.append(_now_iso())
        args.append(prompt_id)
        await conn.execute(f"UPDATE prompts SET {', '.join(sets)} WHERE id = ?", args)
        await conn.commit()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/integration/test_prompts_repo.py tests/unit/test_prompt_models.py -v`
Expected: PASS (new repo tests + existing prompt-model tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/models/prompt.py backend/app/repositories/prompts.py tests/integration/test_prompts_repo.py
git commit -m "feat(prompts): media_kind on model + repo"
```

---

### Task 3: Seed the image prompt

**Files:**
- Create: `backend/seeds/image_template.json`
- Modify: `backend/seeds/default_template.json` (add `"media_kind": "video"`)
- Modify: `backend/app/seed.py` (pass `media_kind`)
- Modify: `backend/app/main.py` (seed the image template too)
- Test: `tests/integration/test_seed_image_prompt.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_seed_image_prompt.py
from pathlib import Path

import pytest

from backend.app.db import open_db
from backend.app.migrations_runner import apply_migrations
from backend.app.repositories.prompts import PromptsRepo
from backend.app.seed import seed_default_prompt

MIGRATIONS = Path(__file__).resolve().parents[2] / "backend" / "migrations"
SEEDS = Path(__file__).resolve().parents[2] / "backend" / "seeds"


@pytest.mark.asyncio
async def test_image_seed_creates_image_prompt_without_markers(tmp_path: Path):
    async with open_db(tmp_path / "t.db") as conn:
        await apply_migrations(conn, MIGRATIONS)
        await seed_default_prompt(conn, seed_path=SEEDS / "image_template.json")
        repo = PromptsRepo()
        prompt = await repo.get_by_name(conn, "Image description + era (Czech)")
        assert prompt is not None
        assert prompt.media_kind == "image"
        version = await repo.get_production_version(conn, prompt.id)
        tm = version.target_map.model_dump(exclude_unset=True)
        # No markers target -> no timecodes will ever be produced.
        assert all(entry.get("kind") != "markers" for entry in tm.values())
        # Same targets as the video prompt for the shared outputs.
        assert tm["summary_cz"]["target"] == "pragafilm.popis.materialu"
        assert tm["decade"]["identifier"] == "pragafilm.dekáda.natočení"
        assert tm["years"]["identifier"] == "pragafilm.rok.natočení"


@pytest.mark.asyncio
async def test_seed_is_idempotent(tmp_path: Path):
    async with open_db(tmp_path / "t.db") as conn:
        await apply_migrations(conn, MIGRATIONS)
        await seed_default_prompt(conn, seed_path=SEEDS / "image_template.json")
        await seed_default_prompt(conn, seed_path=SEEDS / "image_template.json")
        cur = await conn.execute(
            "SELECT COUNT(*) FROM prompts WHERE name = 'Image description + era (Czech)'"
        )
        assert (await cur.fetchone())[0] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/integration/test_seed_image_prompt.py -v`
Expected: FAIL — `image_template.json` missing and/or `seed_default_prompt` doesn't set `media_kind`.

- [ ] **Step 3: Create the image seed**

`backend/seeds/image_template.json`:

```json
{
  "name": "Image description + era (Czech)",
  "description": "Describes a still photograph in Czech and classifies the era. Default seeded prompt for image clips.",
  "media_kind": "image",
  "prompt": "You are annotating an archival still photograph from a Czech private archive — a digitised monochrome photo, typically 1920s–1950s. Describe the photograph in 2–4 Czech sentences (who/what/where is visible), and classify the era from visual cues (clothing, vehicles, technology). There is no video and no timeline — do not return scenes or timestamps. Return JSON matching the schema.",
  "output_schema": {
    "type": "object",
    "required": ["summary_cz", "decade", "years"],
    "properties": {
      "summary_cz": { "type": "object", "required": ["value"],
        "properties": { "value": { "type": "string" } } },
      "decade": { "type": "object", "required": ["value"],
        "properties": { "value": { "type": "string",
          "enum": ["20.léta", "30.léta", "40.léta", "50.léta", "60.léta"] } } },
      "years": { "type": "array", "items": { "type": "string" } }
    }
  },
  "target_map": {
    "summary_cz": { "kind": "note",  "target": "pragafilm.popis.materialu", "mode": "append" },
    "decade":     { "kind": "field", "identifier": "pragafilm.dekáda.natočení" },
    "years":      { "kind": "field", "identifier": "pragafilm.rok.natočení" }
  },
  "model": "gemini-2.5-pro"
}
```

- [ ] **Step 4: Add `media_kind` to the video seed**

In `backend/seeds/default_template.json`, add a top-level key (after `"description"`):

```json
  "media_kind": "video",
```

- [ ] **Step 5: Thread `media_kind` through the seeder**

In `backend/app/seed.py`, in `seed_default_prompt`, pass it through:

```python
    await repo.create_with_initial_version(
        conn,
        name=data["name"],
        description=data.get("description"),
        body=data["prompt"],
        target_map=data["target_map"],
        output_schema=data["output_schema"],
        model=data["model"],
        initial_state="production",
        media_kind=data.get("media_kind", "any"),
    )
```

- [ ] **Step 6: Seed the image template at startup**

In `backend/app/main.py`, where `default_template.json` is seeded (~line 58-60), add an image-template seed call right after it:

```python
    seed_path = SEEDS / "default_template.json"
    if seed_path.exists():
        await seed_default_prompt(ctx.db, seed_path=seed_path)
    image_seed = SEEDS / "image_template.json"
    if image_seed.exists():
        await seed_default_prompt(ctx.db, seed_path=image_seed)
```

(Use the exact `SEEDS` constant and surrounding guard style already present in `main.py`.)

- [ ] **Step 7: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/integration/test_seed_image_prompt.py tests/integration/test_seed_live_prompt.py -v`
Expected: PASS (new image-seed tests + existing seed tests).

- [ ] **Step 8: Commit**

```bash
git add backend/seeds/image_template.json backend/seeds/default_template.json backend/app/seed.py backend/app/main.py tests/integration/test_seed_image_prompt.py
git commit -m "feat(prompts): seed image prompt; tag video seed"
```

---

### Task 4: API carries + edits `media_kind`

**Files:**
- Modify: `backend/app/routes/prompts.py` (`PromptCreate`, `create_prompt`, `PromptPatch`, `patch_prompt`)
- Test: `tests/integration/test_routes_prompts_media_kind.py`

`list_prompts`/`get_prompt` already serialise the full prompt via `model_dump()`, so they return `media_kind` for free once Task 2 lands — no change there.

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_routes_prompts_media_kind.py
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


def _client(monkeypatch, tmp_path) -> TestClient:
    _setenv(monkeypatch, tmp_path)
    from backend.app import main as main_mod

    importlib.reload(main_mod)
    return TestClient(main_mod.app)


def test_create_with_media_kind_and_list_returns_it(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as client:
        r = client.post("/api/prompts", json={
            "name": "img-prompt", "description": None, "body": "b",
            "target_map": {"summary_cz": {"kind": "note", "target": "t"}},
            "output_schema": {}, "model": "gemini-2.5-pro", "media_kind": "image",
        })
        assert r.status_code == 201
        pid = r.json()["id"]
        rows = {p["id"]: p for p in client.get("/api/prompts?archived=0").json()}
        assert rows[pid]["media_kind"] == "image"


def test_patch_media_kind(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as client:
        r = client.post("/api/prompts", json={
            "name": "p2", "description": None, "body": "b",
            "target_map": {}, "output_schema": {}, "model": "gemini-2.5-pro",
        })
        pid = r.json()["id"]
        assert client.patch(
            f"/api/prompts/{pid}", json={"media_kind": "video"}
        ).status_code == 200
        assert client.get(f"/api/prompts/{pid}").json()["media_kind"] == "video"
```

> Harness mirrors `tests/integration/test_routes_jobs.py`: synchronous
> `TestClient(app)` built after `importlib.reload(main_mod)`; there is no
> shared async `client` fixture in this repo.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/integration/test_routes_prompts_media_kind.py -v`
Expected: FAIL — `create_prompt` ignores `media_kind`; `PromptPatch` has no `media_kind`.

- [ ] **Step 3: Implement**

In `backend/app/routes/prompts.py`, add `media_kind` to the request models and route bodies:

```python
class PromptCreate(BaseModel):
    name: str
    description: str | None = None
    body: str
    target_map: TargetMap
    output_schema: dict
    model: str
    media_kind: Literal["video", "image", "any"] = "any"


class PromptPatch(BaseModel):
    name: str | None = None
    description: str | None = None
    media_kind: Literal["video", "image", "any"] | None = None
```

Add the import at the top of the file:

```python
from typing import Any, Literal
```

Pass `media_kind` in `create_prompt`:

```python
        pid, _ = await ctx.prompts_repo.create_with_initial_version(
            ctx.db,
            name=body.name,
            description=body.description,
            body=body.body,
            target_map=body.target_map,
            output_schema=body.output_schema,
            model=body.model,
            media_kind=body.media_kind,
        )
```

And in `patch_prompt`:

```python
        await ctx.prompts_repo.update_metadata(
            ctx.db, prompt_id, name=body.name, description=body.description,
            media_kind=body.media_kind,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/integration/test_routes_prompts_media_kind.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/routes/prompts.py tests/integration/test_routes_prompts_media_kind.py
git commit -m "feat(api): prompts create/patch carry media_kind"
```

---

### Task 5: Annotate dropdown filters by clip kind

**Files:**
- Modify: `backend/app/static/clipAnnotate.js` (signature + filter)
- Modify: `backend/app/templates/pages/clip_detail.html` (pass `clip.kind`)

This is browser-side; verified by reading + the Task 9 browser check. No unit test framework for the JS in this repo.

- [ ] **Step 1: Add the kind param + filter in `clipAnnotate.js`**

Change the factory signature and store the kind:

```javascript
function clipAnnotate(clipId, clipKind) {
  return {
    open: false,
    prompts: null,
    clipKind: clipKind || "video",
```

In `loadPrompts`, extend the existing filter to also match kind:

```javascript
        this.prompts = (data || []).filter(
          (p) =>
            p.current_production_version_id != null &&
            (p.media_kind === this.clipKind || p.media_kind === "any"),
        );
```

- [ ] **Step 2: Pass `clip.kind` from the template**

In `backend/app/templates/pages/clip_detail.html`, the `x-data` composes `clipAnnotate({{ clip.id }})`. Change that call to pass the kind:

```js
clipAnnotate({{ clip.id }}, "{{ clip.kind }}")
```

(It appears once inside the `Object.assign(...)` in the `.detail` element's `x-data`.)

- [ ] **Step 3: Sanity-check templates compile**

Run: `.venv/bin/python -c "from jinja2 import Environment, FileSystemLoader; e=Environment(loader=FileSystemLoader('backend/app/templates')); e.get_template('pages/clip_detail.html'); print('OK')"`
Expected: `OK`.

- [ ] **Step 4: Commit**

```bash
git add backend/app/static/clipAnnotate.js backend/app/templates/pages/clip_detail.html
git commit -m "feat(ui): filter Annotate dropdown by clip kind"
```

---

### Task 6: Create-form `media_kind` selector

**Files:**
- Modify: `backend/app/templates/pages/_prompt_new.html` (selector)
- Modify: `backend/app/routes/pages/prompts.py` (`prompt_new_page` default + `action_create_prompt`)
- Test: `tests/integration/test_routes_pages_prompt_create.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_routes_pages_prompt_create.py
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


def _client(monkeypatch, tmp_path) -> TestClient:
    _setenv(monkeypatch, tmp_path)
    from backend.app import main as main_mod

    importlib.reload(main_mod)
    return TestClient(main_mod.app)


def test_create_form_persists_media_kind(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as client:
        r = client.post(
            "/prompts/_create",
            data={
                "name": "ui-image-prompt", "description": "", "body": "b",
                "target_map": "{}", "output_schema": "{}",
                "model": "gemini-2.5-pro", "media_kind": "image",
            },
            follow_redirects=False,
        )
        assert r.status_code == 303
        rows = client.get("/api/prompts?archived=0").json()
        created = next(p for p in rows if p["name"] == "ui-image-prompt")
        assert created["media_kind"] == "image"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/integration/test_routes_pages_prompt_create.py -v`
Expected: FAIL — `action_create_prompt` ignores `media_kind` (created prompt is `any`).

- [ ] **Step 3: Add the selector to the form**

In `backend/app/templates/pages/_prompt_new.html`, after the Model `<label>...</label>` block (the `<select name="model">`), add:

```html
      <label>
        <div class="panel-h" style="padding: 0; background: transparent; border: 0; height: 24px;">Applies to</div>
        <select name="media_kind" class="txt" style="width: 240px;">
          {% for k in ["any", "video", "image"] %}
            <option value="{{ k }}"{% if form.media_kind == k %} selected{% endif %}>{{ k }}</option>
          {% endfor %}
        </select>
      </label>
```

- [ ] **Step 4: Thread `media_kind` through the page route**

In `backend/app/routes/pages/prompts.py`:

In `prompt_new_page`, add `"media_kind": "any"` to the `form` dict.

In `action_create_prompt`, read the field and pass it. Read it near the other form reads:

```python
    media_kind = form.get("media_kind") or "any"
```

Add `"media_kind": media_kind` to **both** `form` dicts in the two error-path `TemplateResponse` calls, and pass it to the repo call:

```python
        pid, _ = await ctx.prompts_repo.create_with_initial_version(
            ctx.db,
            name=name,
            description=description,
            body=body,
            target_map=target_map,
            output_schema=output_schema,
            model=model,
            media_kind=media_kind,
        )
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/pytest tests/integration/test_routes_pages_prompt_create.py -v`
Expected: PASS. Also: `.venv/bin/pytest tests/integration/test_routes_pages.py -q` (no regressions).

- [ ] **Step 6: Commit**

```bash
git add backend/app/templates/pages/_prompt_new.html backend/app/routes/pages/prompts.py tests/integration/test_routes_pages_prompt_create.py
git commit -m "feat(ui): media_kind selector on the create-prompt form"
```

---

### Task 7: Edit `media_kind` on the prompt detail page

**Files:**
- Modify: `backend/app/templates/pages/_prompt_detail.html` (Applies-to control)
- Modify: `backend/app/static/promptEditor.js` (init field + `setMediaKind`)

Backend already supports this via `PATCH /api/prompts/{id}` (Task 4). Browser-verified in Task 9.

- [ ] **Step 1: Pass `media_kind` into the editor component**

In `backend/app/templates/pages/_prompt_detail.html`, add to the `promptEditor({...})` init object (after `prompt_description`):

```js
                prompt_description: {{ (selected.description or "")|tojson }},
                media_kind: {{ selected.media_kind|tojson }}
```

- [ ] **Step 2: Add the Applies-to control in the header tag row**

In the header tag row (the `<div class="row" ... margin-bottom: 2px; flex-wrap: wrap;">` that holds the version picker + state tag), add after the state tag block (`{% endif %}` closing the production/draft/archived tags):

```html
        <select class="txt" style="height: 24px; padding: 0 6px;"
                x-model="mediaKind" @change="setMediaKind()"
                title="Which clip kind this prompt applies to">
          <template x-for="k in ['any','video','image']" :key="k">
            <option :value="k" x-text="k"></option>
          </template>
        </select>
```

- [ ] **Step 3: Wire `mediaKind` + `setMediaKind` in `promptEditor.js`**

In `backend/app/static/promptEditor.js`, in the returned object add the field (near `prompt_name`):

```javascript
    prompt_name: initial.prompt_name || "",
    mediaKind: initial.media_kind || "any",
```

Add a method (alongside the other async methods like `save`):

```javascript
    async setMediaKind() {
      try {
        const resp = await fetch(`/api/prompts/${this.prompt_id}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ media_kind: this.mediaKind }),
        });
        if (!resp.ok) this.error = `kind update failed (${resp.status})`;
      } catch (e) {
        this.error = String(e);
      }
    },
```

- [ ] **Step 4: Sanity-check the template compiles**

Run: `.venv/bin/python -c "from jinja2 import Environment, FileSystemLoader; e=Environment(loader=FileSystemLoader('backend/app/templates')); e.get_template('pages/_prompt_detail.html'); print('OK')"`
Expected: `OK`.

- [ ] **Step 5: Commit**

```bash
git add backend/app/templates/pages/_prompt_detail.html backend/app/static/promptEditor.js
git commit -m "feat(ui): edit prompt media_kind on the detail page"
```

---

### Task 8: Kind badge in list, detail header, and Annotate dropdown

**Files:**
- Modify: `backend/app/templates/pages/_prompts_list.html`
- Modify: `backend/app/templates/pages/_prompt_detail.html`
- Modify: `backend/app/templates/pages/_annotate_dropdown.html`
- Test: `tests/integration/test_routes_pages_prompt_badge.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_routes_pages_prompt_badge.py
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


def _client(monkeypatch, tmp_path) -> TestClient:
    _setenv(monkeypatch, tmp_path)
    from backend.app import main as main_mod

    importlib.reload(main_mod)
    return TestClient(main_mod.app)


def test_prompts_list_shows_kind_chip(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as client:
        # Name deliberately contains no kind word, so the assertion below only
        # passes once the chip markup itself renders the kind.
        client.post("/api/prompts", json={
            "name": "Holiday snaps", "description": None, "body": "b",
            "target_map": {}, "output_schema": {},
            "model": "gemini-2.5-pro", "media_kind": "image",
        })
        html = client.get("/prompts").text
        assert "Holiday snaps" in html          # the prompt is listed
        assert ">image<" in html                # the kind chip rendered
```

> Harness mirrors `tests/integration/test_routes_jobs.py` (sync
> `TestClient`). The test creates its own `image` prompt with a kind-free
> name, so `">image<"` only appears once the chip markup is added —
> a meaningful red→green.

- [ ] **Step 2: Run test to verify it fails (or is inconclusive), then implement**

Run: `.venv/bin/pytest tests/integration/test_routes_pages_prompt_badge.py -v`
Expected: FAIL if no chip is rendered yet (depending on seed state). Proceed to implement regardless.

- [ ] **Step 3: Add the chip to the list rows**

In `backend/app/templates/pages/_prompts_list.html`, inside the row, add a chip next to the name (only when not `any`):

```html
      <div>
        <div class="name">
          {{ p.name }}
          {% if p.media_kind and p.media_kind != "any" %}
            <span class="tag" style="margin-left: 6px;">{{ p.media_kind }}</span>
          {% endif %}
        </div>
        <div class="desc">{{ p.description or "" }}</div>
      </div>
```

- [ ] **Step 4: Add the chip to the detail header**

In `backend/app/templates/pages/_prompt_detail.html`, in the header tag row right after the prompt-name `<div>` (before the version picker include), add:

```html
        {% if selected.media_kind and selected.media_kind != "any" %}
          <span class="tag">{{ selected.media_kind }}</span>
        {% endif %}
```

- [ ] **Step 5: Add the chip to the Annotate dropdown items**

In `backend/app/templates/pages/_annotate_dropdown.html`, inside the `annotate-item` button, next to the version meta:

```html
              <span class="annotate-meta mono">
                <span x-show="p.media_kind && p.media_kind !== 'any'"
                      class="tag" x-text="p.media_kind"></span>
                v<span x-text="p.current_production_version_num"></span>
              </span>
```

- [ ] **Step 6: Run test + template compile**

Run: `.venv/bin/pytest tests/integration/test_routes_pages_prompt_badge.py -v`
Expected: PASS.
Run: `.venv/bin/python -c "from jinja2 import Environment, FileSystemLoader; e=Environment(loader=FileSystemLoader('backend/app/templates')); [e.get_template(t) for t in ('pages/_prompts_list.html','pages/_prompt_detail.html','pages/_annotate_dropdown.html')]; print('OK')"`
Expected: `OK`.

- [ ] **Step 7: Commit**

```bash
git add backend/app/templates/pages/_prompts_list.html backend/app/templates/pages/_prompt_detail.html backend/app/templates/pages/_annotate_dropdown.html tests/integration/test_routes_pages_prompt_badge.py
git commit -m "feat(ui): kind badge in prompts list, detail, and dropdown"
```

---

### Task 9: Full suite, lint, browser check, ADR 0027

**Files:**
- Create: `docs/adr/0027-image-annotation-prompt-and-media-kind.md`
- Modify: `docs/decisions.md`

- [ ] **Step 1: Full suite**

Run: `.venv/bin/pytest -q`
Expected: all pass. Fix any regression in code this plan touched; if a failure is clearly pre-existing/unrelated, report it rather than fixing unrelated code.

- [ ] **Step 2: Lint / type / imports**

Run: `.venv/bin/pre-commit run --all-files`
- Fix issues in files THIS plan created/modified (`backend/app/models/prompt.py`, `backend/app/repositories/prompts.py`, `backend/app/routes/prompts.py`, `backend/app/routes/pages/prompts.py`, `backend/app/seed.py`, `backend/app/main.py`, the new migration + seeds + tests).
- If basedpyright reports NEW errors in changed files, fix them; if pre-commit auto-formats our files, re-stage. Pre-existing failures in untouched files: note, don't fix. Commit any fixes:
  `git add -A && git commit -m "chore: satisfy lint/type/test gate for image annotation prompt"` (stage selectively — exclude unrelated files pre-commit may have reformatted).

- [ ] **Step 3: Browser verification (coordinate via controller — uses the CatDV seat)**

This step touches the real CatDV and the user's running dev server; the controller coordinates it (graceful restart per the seat discipline in `CLAUDE.md`). Verify:
- On an image clip, the Annotate dropdown lists the "Image description + era (Czech)" prompt (and not the scene prompt); running it produces a Czech summary + decade/years and **no markers/timestamps**, with the annotation appearing in the draft view and searchable.
- On a video clip, the dropdown still lists the scene prompt and behaves as before.
- `/prompts`: the image prompt shows an `image` chip; the detail page shows the chip and an editable "Applies to" control that persists on change; the create form has the selector.
If the UI can't be exercised here, say so explicitly rather than claiming success.

- [ ] **Step 4: Write the ADR**

Create `docs/adr/0027-image-annotation-prompt-and-media-kind.md`:

```markdown
# 0027. Image annotation prompt + prompt media_kind

**Date:** 2026-05-26
**Status:** Accepted

## Context

After image clips became viewable/annotatable (ADR 0026), running the
default scene-marker prompt on a still produced nonsensical timestamped
"scenes". Stills need a prompt with no scenes/timecodes, while keeping the
Czech summary + era metadata — and the output had to be stored and indexed
exactly like video output.

## Decision

Tag each prompt with `media_kind` (`video` / `image` / `any`, migration
0011; existing prompts backfilled to `video`). Seed a dedicated image
prompt that is the video prompt's schema/target_map **minus `scenes`**,
reusing the same `summary_cz`/`decade`/`years` keys and the same CatDV
targets (`pragafilm.popis.materialu`, `pragafilm.dekáda.natočení`,
`pragafilm.rok.natočení`). Filter the Annotate dropdown by the clip's kind
(`media_kind == clip.kind or "any"`). Surface and edit `media_kind` in the
prompts UI (create selector, detail-page control via `PATCH
/api/prompts/{id}`, and a kind badge).

## Alternatives

- **Strip `scenes` from the single prompt at runtime** — rejected: mutating
  user-authored schema/target_map is brittle and leaves contradictory
  scene instructions in the prompt body; gives operators no way to tune
  image wording.
- **Manual prompt choice only** — rejected: easy to run the scene prompt on
  a still by mistake.

## Consequences

- Image and video annotations are stored and indexed identically (same
  `annotations` row, same `annotations_fts` trigger, same `review_items`
  note/field kinds) — the only difference is the absence of markers. This
  is guaranteed by the seed content reusing identical targets, not by code.
- `media_kind` lives on `prompts` (stable across versions), edited at the
  prompt level rather than per version.
- Operators can author custom image-only / video-only / any prompts.
```

- [ ] **Step 5: Update the decisions index**

In `docs/decisions.md`, append:

```markdown
| 0027 | 2026-05-26 | [Image annotation prompt + prompt media_kind](./adr/0027-image-annotation-prompt-and-media-kind.md) |
```

- [ ] **Step 6: Commit**

```bash
git add docs/adr/0027-image-annotation-prompt-and-media-kind.md docs/decisions.md
git commit -m "docs(adr): 0027 image annotation prompt + media_kind"
```

---

## Self-review notes

- **Spec coverage:** §1 media_kind model → Task 1 (migration) + Task 2 (model/repo); §2 storage/indexing invariant → enforced by Task 3 seed content + asserted in Task 3 test (no `markers` target; identical targets); §2/§3 image prompt → Task 3; §2 model/repo/API → Tasks 2 + 4; §4 dropdown filter → Task 5; §5 editor create+edit → Tasks 6 + 7; §6 kind badge → Task 8; testing → each task; out-of-scope prefetcher bug → not in this plan (tracked separately).
- **No new annotator/expand change needed:** the image target_map has no `markers` entry, so `target_map.expand` emits only note/field review items and `_render_prompt` already skips the duration anchor at `duration==0`.
- **Signature consistency:** `create_with_initial_version(..., media_kind="any")` (Task 2) is called with `media_kind=` by the seeder (Task 3) and both create routes (Tasks 4, 6); `update_metadata(..., media_kind=None)` (Task 2) is called by `patch_prompt` (Task 4) and the detail control's `PATCH` (Task 7); `Prompt.media_kind` (Task 2) is read by `model_dump()` in the list/get API and the page handlers' `p.model_dump()` (already present) and rendered by the badges (Task 8).
- **`client` fixture caveat:** Tasks 4/6/8 assume the existing async app test client fixture; the implementer must match the actual fixture name/shape used in `tests/integration/test_routes_*.py`.
