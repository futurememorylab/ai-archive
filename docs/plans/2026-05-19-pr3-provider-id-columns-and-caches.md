# PR 3: Provider ID Columns + clip_cache + field_def_cache — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Introduce provider-aware clip identity columns (`provider_id` / `provider_clip_id`) on every clip-keyed table, plus two new local mirror tables (`clip_cache`, `field_def_cache`), plus the matching repositories, plus write-through caching inside `CatdvArchiveAdapter` for `get_clip` and a new `list_field_definitions`. No user-visible behaviour change — this PR is a perf and offline-readiness prerequisite for PR 4 (WriteQueue) and PR 5 (Workspaces). This is the third of seven PRs implementing the design in `docs/specs/2026-05-19-archive-abstraction-and-offline-mode-design.md` (§7.1, §13 PR 3).

**Architecture:** A single new migration `0003_provider_id_and_caches.sql` performs three things in one transaction-equivalent script: (1) adds `provider_id TEXT` + `provider_clip_id TEXT` columns to `annotations`, `review_items`, `job_items`, `proxy_cache`, `ai_store_files`, `write_log`, (2) backfills each with `'catdv'` / `CAST(catdv_clip_id AS TEXT)` for existing rows, (3) creates new `clip_cache` and `field_def_cache` tables keyed on `(provider_id, provider_clip_id)` and `(provider_id, identifier)` respectively. Two new repositories live under `backend/app/repositories/`: `ClipCacheRepo` (upsert / get_by_key / list_by_catalog / delete_by_key plus JSON (de)serialisation of `CanonicalClip`) and `FieldDefCacheRepo` (upsert / get_by_key / list_for_provider / delete_by_key plus (de)serialisation of a new `FieldDef` canonical type). `CatdvArchiveAdapter` gains a constructor dependency on both repos + a db-provider callable + a clock; its `get_clip` consults `clip_cache` first (TTL = `settings.clip_cache_ttl_hours`, default 168 h) and writes through on miss/expiry. A new `list_field_definitions` method on the adapter does the same for `field_def_cache`, calling a thin new `CatdvClient.list_fields()` wrapper around `GET /catdv/api/9/fields`. The canonical model gains a `FieldDef` frozen dataclass; the `ArchiveProvider` Protocol gains the `list_field_definitions` method. `AppContext` exposes `ctx.clip_cache_repo` and `ctx.field_def_cache_repo` and threads them into the adapter via `build_archive_provider`.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, dataclasses (frozen), `asyncio`, `aiosqlite`, `pytest` + `pytest-asyncio`. No new third-party deps. Migration is a plain SQL file applied by `migrations_runner.apply_migrations`.

**Scope guardrail:** This plan implements ONLY what spec §13 PR 3 lists. NOT in this PR: `pending_operations` (PR 4), `workspaces` / `workspace_clips` (PR 5), `connection_events` (PR 4), `cache_actions_log` (PR 6), FS adapter (PR 7), `WriteQueue` / `SyncEngine` / `ConnectionMonitor`, any UI changes, dropping `catdv_clip_id` (post-PR 7 cleanup), multi-provider runtime selection. The PK of `ai_store_files` stays `(store_id, catdv_clip_id)` for now — it gains `provider_id` / `provider_clip_id` as dual-write columns but the PK is unchanged (PR-after-cutover renames). No FK on `clip_cache.pinned_to_workspace_id` (PR 5 adds it).

**Decisions recorded in this plan (not yet in `docs/decisions.md` — appended in Task 11 if non-trivial):**
1. **Single migration file** (`0003_provider_id_and_caches.sql`) rather than split into two. The two changes are conceptually one ("provider-aware identity"), and a single migration keeps the rollback boundary tight.
2. **TTL semantics:** on `get_clip`, if the cached row's `fetched_at` is older than `clip_cache_ttl_hours`, the cache is bypassed (provider re-fetched) and the row overwritten on success. Provider errors do NOT delete the stale row — the stale snapshot is still useful for offline workflows in PR 4. The cache write happens after a successful upstream call, never on failure. Same policy for `list_field_definitions`, with one wrinkle: field defs are fetched as a list per provider, so TTL is checked against the *newest* row's `fetched_at` and the whole set is replaced on refresh.
3. **`provider_data` JSON round-trip:** `CanonicalClip.provider_data` (an arbitrary dict from the upstream API) is JSON-serialised verbatim into `clip_cache.canonical_json`. Non-serialisable types (datetime, set, etc.) are coerced via a small `_json_default` that emits ISO-8601 for datetimes and falls back to `str()`. The adapter's existing CatDV payload uses plain JSON types, so the fallback should be cold.
4. **`FieldDef.picklist_values`** is stored as a JSON-encoded list inside `field_def_cache.json` (the whole `FieldDef` is serialised as one blob); no separate column.

---

## File map

### New files

| Path | Responsibility |
|---|---|
| `backend/migrations/0003_provider_id_and_caches.sql` | Adds `provider_id`/`provider_clip_id` to the 6 clip-keyed tables; backfills; creates `clip_cache` and `field_def_cache`. |
| `backend/app/repositories/clip_cache.py` | `ClipCacheRepo` — upsert, get_by_key, list_by_catalog, delete_by_key. Helpers to (de)serialise `CanonicalClip` ↔ JSON. |
| `backend/app/repositories/field_def_cache.py` | `FieldDefCacheRepo` — upsert, get_by_key, list_for_provider, delete_by_key, replace_all_for_provider. Helpers to (de)serialise `FieldDef` ↔ JSON. |
| `tests/integration/test_migration_0003.py` | Asserts (a) the 6 tables gain both columns, (b) backfill populates them for an existing row, (c) `clip_cache` and `field_def_cache` tables exist with the right columns + index. |
| `tests/integration/test_clip_cache_repo.py` | DB-level: upsert, round-trip a `CanonicalClip`, get_by_key, list_by_catalog, delete_by_key, replace-on-conflict. |
| `tests/integration/test_field_def_cache_repo.py` | DB-level: upsert, round-trip a `FieldDef`, list_for_provider, replace_all_for_provider. |
| `tests/unit/test_field_def_model.py` | Constructs `FieldDef`, asserts immutability + defaults. |
| `tests/integration/test_catdv_adapter_caching.py` | Adapter integration: `get_clip` hits cache on warm read; first call writes through; expired entry triggers refresh; `list_field_definitions` writes through and re-reads cache within TTL. |

### Modified files

| Path | Change |
|---|---|
| `backend/app/archive/model.py` | Add `FieldDef` frozen dataclass. |
| `backend/app/archive/provider.py` | Add `list_field_definitions()` to the `ArchiveProvider` Protocol. |
| `backend/app/archive/providers/catdv/adapter.py` | Constructor takes `clip_cache_repo`, `field_def_cache_repo`, `db_provider`, `clip_cache_ttl_hours`, optional `clock`. `get_clip` becomes cache-first. New `list_field_definitions` method. |
| `backend/app/archive/providers/catdv/mapping.py` | Add `field_def_from_catdv` mapper from `GET /catdv/api/9/fields` row shape to `FieldDef`. |
| `backend/app/services/catdv_client.py` | Add `list_fields()` thin wrapper around `GET /catdv/api/9/fields`. |
| `backend/app/archive/registry.py` | `build_archive_provider` now takes `clip_cache_repo`, `field_def_cache_repo`, `db_provider`, `clip_cache_ttl_hours`. |
| `backend/app/settings.py` | Add `clip_cache_ttl_hours: int = 168`. |
| `backend/app/context.py` | Add `clip_cache_repo: ClipCacheRepo` + `field_def_cache_repo: FieldDefCacheRepo` factory fields. Pass them into `build_archive_provider`. |
| `tests/integration/test_initial_schema.py` | `EXPECTED_TABLES` gains `"clip_cache"` and `"field_def_cache"`. |
| `tests/integration/test_catdv_adapter.py` | Existing tests now build the adapter with cache-repo deps wired to a real (or stub) DB. Add a `_make_adapter(db)` helper to keep the call sites tidy. |
| `tests/fakes/fake_catdv.py` | Add a `/catdv/api/9/fields` endpoint serving an empty list by default + a `field_defs` attribute the tests can seed. |

### Deleted files

None.

---

## Tasks

### Task 1: `FieldDef` canonical type

**Files:**
- Modify: `backend/app/archive/model.py`
- Create: `tests/unit/test_field_def_model.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_field_def_model.py`:

```python
import pytest

from backend.app.archive.model import FieldDef


def test_field_def_holds_identifier_name_and_type():
    fd = FieldDef(
        identifier="pragafilm.barva",
        name="Barva",
        type="bool",
        is_multi=False,
        is_editable=True,
    )
    assert fd.identifier == "pragafilm.barva"
    assert fd.type == "bool"
    assert fd.picklist_values is None
    assert fd.provider_data == {}


def test_field_def_with_picklist_values():
    fd = FieldDef(
        identifier="pragafilm.theme",
        name="Theme",
        type="multi-picklist",
        is_multi=True,
        is_editable=True,
        picklist_values=("rodina", "škola"),
        provider_data={"raw": "anything"},
    )
    assert fd.picklist_values == ("rodina", "škola")
    assert fd.is_multi is True


def test_field_def_is_frozen():
    fd = FieldDef(
        identifier="x", name="x", type="text",
        is_multi=False, is_editable=True,
    )
    with pytest.raises(Exception):
        fd.identifier = "y"  # type: ignore[misc]
```

- [ ] **Step 2: Run test to verify it fails**

```bash
.venv/bin/pytest tests/unit/test_field_def_model.py -v
```
Expected: `ImportError: cannot import name 'FieldDef'`.

- [ ] **Step 3: Add `FieldDef` to the model**

In `backend/app/archive/model.py`, append:

```python
@dataclass(frozen=True)
class FieldDef:
    identifier: str
    name: str
    type: Literal[
        "text", "integer", "decimal", "date",
        "picklist", "multi-picklist", "bool",
    ]
    is_multi: bool
    is_editable: bool
    picklist_values: tuple[str, ...] | None = None
    provider_data: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if isinstance(self.picklist_values, list):
            object.__setattr__(self, "picklist_values", tuple(self.picklist_values))
```

Also add `from dataclasses import dataclass, field` if not already imported in that form (the file currently imports just `dataclass`; switch to both names).

- [ ] **Step 4: Run test, verify pass**

- [ ] **Step 5: Commit**

```bash
git add backend/app/archive/model.py tests/unit/test_field_def_model.py
git commit -m "feat(archive): FieldDef canonical type"
```

---

### Task 2: Migration 0003 — provider columns and cache tables

**Files:**
- Create: `backend/migrations/0003_provider_id_and_caches.sql`
- Create: `tests/integration/test_migration_0003.py`
- Modify: `tests/integration/test_initial_schema.py`

- [ ] **Step 1: Write the failing migration test**

Create `tests/integration/test_migration_0003.py`:

```python
from pathlib import Path

import pytest

from backend.app.db import open_db
from backend.app.migrations_runner import apply_migrations

MIGRATIONS = Path(__file__).resolve().parents[2] / "backend" / "migrations"

TABLES_WITH_PROVIDER_COLUMNS = [
    "annotations",
    "review_items",
    "job_items",
    "proxy_cache",
    "ai_store_files",
    "write_log",
]


async def _columns(conn, table: str) -> set[str]:
    cur = await conn.execute(f"PRAGMA table_info({table})")
    return {row[1] for row in await cur.fetchall()}


@pytest.mark.asyncio
async def test_migration_0003_adds_provider_columns_to_all_clip_tables(tmp_path):
    db_path = tmp_path / "test.db"
    async with open_db(db_path) as conn:
        await apply_migrations(conn, MIGRATIONS)
        for table in TABLES_WITH_PROVIDER_COLUMNS:
            cols = await _columns(conn, table)
            assert "provider_id" in cols, f"missing provider_id in {table}"
            assert "provider_clip_id" in cols, f"missing provider_clip_id in {table}"


@pytest.mark.asyncio
async def test_migration_0003_backfills_existing_rows(tmp_path):
    db_path = tmp_path / "test.db"
    # Apply 0001 and 0002 manually, seed rows, then run full chain (0003).
    async with open_db(db_path) as conn:
        for name in ("0001_initial.sql", "0002_ai_store_files.sql"):
            await conn.executescript((MIGRATIONS / name).read_text())
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                name TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        for name in ("0001_initial.sql", "0002_ai_store_files.sql"):
            await conn.execute(
                "INSERT INTO schema_migrations(name) VALUES (?)", (name,)
            )

        # Seed: one row in each clip-keyed table.
        await conn.execute(
            """
            INSERT INTO templates(id, name, prompt, output_schema, target_map,
                                  model, created_at, updated_at)
            VALUES (1, 't', 'p', '{}', '{}', 'g', '2026', '2026')
            """
        )
        await conn.execute(
            """
            INSERT INTO jobs(id, template_id, status, created_at, total_clips)
            VALUES (1, 1, 'queued', '2026', 0)
            """
        )
        await conn.execute(
            """
            INSERT INTO job_items(job_id, catdv_clip_id, status)
            VALUES (1, 11, 'queued')
            """
        )
        await conn.execute(
            """
            INSERT INTO proxy_cache(catdv_clip_id, file_path, size_bytes,
                                    downloaded_at, last_used_at)
            VALUES (12, '/p', 0, '2026', '2026')
            """
        )
        await conn.execute(
            """
            INSERT INTO ai_store_files(store_id, catdv_clip_id, gcs_uri,
                mime_type, size_bytes, sha256, uploaded_at, last_used_at)
            VALUES ('gcs:b', 13, 'gs://b/clips/13.mov', 'video/quicktime', 1,
                    'x', '2026', '2026')
            """
        )
        await conn.execute(
            """
            INSERT INTO annotations(id, catdv_clip_id, catdv_clip_name,
                template_id, model, prompt_used, raw_response,
                structured_output, clip_snapshot, created_at)
            VALUES (1, 14, 'n', 1, 'g', 'p', '{}', '{}', '{}', '2026')
            """
        )
        await conn.execute(
            """
            INSERT INTO review_items(annotation_id, catdv_clip_id, kind,
                proposed_value, decision)
            VALUES (1, 14, 'field', 'v', 'pending')
            """
        )
        await conn.execute(
            """
            INSERT INTO write_log(catdv_clip_id, payload, response, status,
                                  written_at)
            VALUES (15, '{}', '{}', 'ok', '2026')
            """
        )
        await conn.commit()

    async with open_db(db_path) as conn:
        applied = await apply_migrations(conn, MIGRATIONS)
        assert "0003_provider_id_and_caches.sql" in applied

        for table, expected_clip_id in [
            ("job_items", "11"),
            ("proxy_cache", "12"),
            ("ai_store_files", "13"),
            ("annotations", "14"),
            ("review_items", "14"),
            ("write_log", "15"),
        ]:
            cur = await conn.execute(
                f"SELECT provider_id, provider_clip_id FROM {table}"
            )
            row = await cur.fetchone()
            assert row is not None, f"no row in {table}"
            assert row[0] == "catdv"
            assert row[1] == expected_clip_id


@pytest.mark.asyncio
async def test_migration_0003_creates_clip_cache_table(tmp_path):
    db_path = tmp_path / "test.db"
    async with open_db(db_path) as conn:
        await apply_migrations(conn, MIGRATIONS)
        cols = await _columns(conn, "clip_cache")
        assert {
            "provider_id", "provider_clip_id", "name", "catalog_id",
            "duration_secs", "fps", "canonical_json", "provider_etag",
            "fetched_at", "pinned_to_workspace_id",
        }.issubset(cols)
        cur = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND tbl_name='clip_cache'"
        )
        idx = {row[0] for row in await cur.fetchall()}
        assert "idx_clip_cache_catalog" in idx


@pytest.mark.asyncio
async def test_migration_0003_creates_field_def_cache_table(tmp_path):
    db_path = tmp_path / "test.db"
    async with open_db(db_path) as conn:
        await apply_migrations(conn, MIGRATIONS)
        cols = await _columns(conn, "field_def_cache")
        assert {"provider_id", "identifier", "json", "fetched_at"}.issubset(cols)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
.venv/bin/pytest tests/integration/test_migration_0003.py -v
```
Expected: FAIL — no 0003 file.

- [ ] **Step 3: Write the migration SQL**

Create `backend/migrations/0003_provider_id_and_caches.sql`:

```sql
-- PR 3: provider-aware clip identity columns + local mirror tables.
-- For each clip-keyed table, add (provider_id, provider_clip_id) and
-- backfill from the existing catdv_clip_id column. The catdv_clip_id
-- column itself is kept until a post-cutover migration drops it.

ALTER TABLE annotations    ADD COLUMN provider_id TEXT;
ALTER TABLE annotations    ADD COLUMN provider_clip_id TEXT;
UPDATE annotations
   SET provider_id = 'catdv',
       provider_clip_id = CAST(catdv_clip_id AS TEXT);

ALTER TABLE review_items   ADD COLUMN provider_id TEXT;
ALTER TABLE review_items   ADD COLUMN provider_clip_id TEXT;
UPDATE review_items
   SET provider_id = 'catdv',
       provider_clip_id = CAST(catdv_clip_id AS TEXT);

ALTER TABLE job_items      ADD COLUMN provider_id TEXT;
ALTER TABLE job_items      ADD COLUMN provider_clip_id TEXT;
UPDATE job_items
   SET provider_id = 'catdv',
       provider_clip_id = CAST(catdv_clip_id AS TEXT);

ALTER TABLE proxy_cache    ADD COLUMN provider_id TEXT;
ALTER TABLE proxy_cache    ADD COLUMN provider_clip_id TEXT;
UPDATE proxy_cache
   SET provider_id = 'catdv',
       provider_clip_id = CAST(catdv_clip_id AS TEXT);

ALTER TABLE ai_store_files ADD COLUMN provider_id TEXT;
ALTER TABLE ai_store_files ADD COLUMN provider_clip_id TEXT;
UPDATE ai_store_files
   SET provider_id = 'catdv',
       provider_clip_id = CAST(catdv_clip_id AS TEXT);

ALTER TABLE write_log      ADD COLUMN provider_id TEXT;
ALTER TABLE write_log      ADD COLUMN provider_clip_id TEXT;
UPDATE write_log
   SET provider_id = 'catdv',
       provider_clip_id = CAST(catdv_clip_id AS TEXT);

-- Local mirror of upstream clip state. PR 5 will add the FK on
-- pinned_to_workspace_id once the workspaces table exists.
CREATE TABLE clip_cache (
  provider_id            TEXT NOT NULL,
  provider_clip_id       TEXT NOT NULL,
  name                   TEXT NOT NULL,
  catalog_id             TEXT NOT NULL,
  duration_secs          REAL NOT NULL,
  fps                    REAL NOT NULL,
  canonical_json         TEXT NOT NULL,
  provider_etag          TEXT,
  fetched_at             TEXT NOT NULL,
  pinned_to_workspace_id INTEGER,
  PRIMARY KEY (provider_id, provider_clip_id)
);
CREATE INDEX idx_clip_cache_catalog ON clip_cache(provider_id, catalog_id);

-- Local mirror of provider field definitions.
CREATE TABLE field_def_cache (
  provider_id  TEXT NOT NULL,
  identifier   TEXT NOT NULL,
  json         TEXT NOT NULL,
  fetched_at   TEXT NOT NULL,
  PRIMARY KEY (provider_id, identifier)
);
```

- [ ] **Step 4: Update `EXPECTED_TABLES`**

In `tests/integration/test_initial_schema.py`, add `"clip_cache"` and `"field_def_cache"` to `EXPECTED_TABLES`.

- [ ] **Step 5: Run all migration tests + schema test, verify pass**

```bash
.venv/bin/pytest tests/integration/test_migration_0003.py tests/integration/test_initial_schema.py tests/integration/test_migration_0002.py -v
```

- [ ] **Step 6: Commit**

```bash
git add backend/migrations/0003_provider_id_and_caches.sql \
        tests/integration/test_migration_0003.py \
        tests/integration/test_initial_schema.py
git commit -m "chore(migrations): 0003 provider_id columns + clip_cache/field_def_cache tables"
```

---

### Task 3: `ClipCacheRepo`

**Files:**
- Create: `backend/app/repositories/clip_cache.py`
- Create: `tests/integration/test_clip_cache_repo.py`

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_clip_cache_repo.py`:

```python
from datetime import datetime, timezone

import pytest

from backend.app.archive.model import (
    CanonicalClip,
    FieldValue,
    Marker,
    MediaRef,
    Timecode,
)
from backend.app.repositories.clip_cache import ClipCacheRepo


def _make_clip(clip_id: str = "1", *, name: str = "Clip_1") -> CanonicalClip:
    return CanonicalClip(
        key=("catdv", clip_id),
        name=name,
        duration_secs=12.5,
        fps=25.0,
        markers=(Marker(name="m", in_=Timecode(secs=1.0, fps=25.0), out=None),),
        fields={"pragafilm.barva": FieldValue("pragafilm.barva", "true")},
        notes={"notes": "n"},
        media=MediaRef(
            mime_type="video/quicktime",
            size_bytes=None,
            cached_path=None,
            upstream_handle=clip_id,
        ),
        provider_data={"ID": int(clip_id), "fps": 25.0},
        fetched_at=datetime(2026, 5, 19, tzinfo=timezone.utc),
    )


@pytest.mark.asyncio
async def test_upsert_and_get_round_trips_canonical_clip(db):
    repo = ClipCacheRepo()
    clip = _make_clip("1", name="Clip_A")
    await repo.upsert(db, clip=clip, catalog_id="881507")

    got = await repo.get_by_key(db, provider_id="catdv", provider_clip_id="1")
    assert got is not None
    assert got.key == ("catdv", "1")
    assert got.name == "Clip_A"
    assert got.fps == 25.0
    assert got.markers[0].name == "m"
    assert got.fields["pragafilm.barva"].value == "true"
    assert got.notes == {"notes": "n"}
    assert got.provider_data == {"ID": 1, "fps": 25.0}


@pytest.mark.asyncio
async def test_get_returns_none_when_absent(db):
    repo = ClipCacheRepo()
    assert await repo.get_by_key(
        db, provider_id="catdv", provider_clip_id="404"
    ) is None


@pytest.mark.asyncio
async def test_get_row_returns_metadata(db):
    repo = ClipCacheRepo()
    clip = _make_clip("2")
    await repo.upsert(db, clip=clip, catalog_id="881507", provider_etag="W/1")
    row = await repo.get_row(db, provider_id="catdv", provider_clip_id="2")
    assert row is not None
    assert row["catalog_id"] == "881507"
    assert row["provider_etag"] == "W/1"
    assert row["fetched_at"] is not None


@pytest.mark.asyncio
async def test_upsert_replaces_on_same_pk(db):
    repo = ClipCacheRepo()
    await repo.upsert(db, clip=_make_clip("3", name="v1"), catalog_id="c1")
    await repo.upsert(db, clip=_make_clip("3", name="v2"), catalog_id="c1")
    got = await repo.get_by_key(db, provider_id="catdv", provider_clip_id="3")
    assert got is not None and got.name == "v2"


@pytest.mark.asyncio
async def test_list_by_catalog(db):
    repo = ClipCacheRepo()
    await repo.upsert(db, clip=_make_clip("4"), catalog_id="A")
    await repo.upsert(db, clip=_make_clip("5"), catalog_id="A")
    await repo.upsert(db, clip=_make_clip("6"), catalog_id="B")
    rows = await repo.list_by_catalog(db, provider_id="catdv", catalog_id="A")
    keys = {(r["provider_id"], r["provider_clip_id"]) for r in rows}
    assert keys == {("catdv", "4"), ("catdv", "5")}


@pytest.mark.asyncio
async def test_delete_by_key(db):
    repo = ClipCacheRepo()
    await repo.upsert(db, clip=_make_clip("7"), catalog_id="A")
    await repo.delete_by_key(db, provider_id="catdv", provider_clip_id="7")
    assert await repo.get_by_key(
        db, provider_id="catdv", provider_clip_id="7"
    ) is None
```

- [ ] **Step 2: Run, confirm failure**

- [ ] **Step 3: Implement the repository**

Create `backend/app/repositories/clip_cache.py`:

```python
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import aiosqlite

from backend.app.archive.model import (
    CanonicalClip,
    FieldValue,
    Marker,
    MediaRef,
    Timecode,
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_default(o: Any) -> Any:
    if isinstance(o, datetime):
        return o.isoformat()
    return str(o)


def _clip_to_json(clip: CanonicalClip) -> str:
    payload = {
        "key": list(clip.key),
        "name": clip.name,
        "duration_secs": clip.duration_secs,
        "fps": clip.fps,
        "markers": [_marker_to_dict(m) for m in clip.markers],
        "fields": {
            k: {"identifier": v.identifier, "value": v.value, "is_multi": v.is_multi}
            for k, v in clip.fields.items()
        },
        "notes": dict(clip.notes),
        "media": {
            "mime_type": clip.media.mime_type,
            "size_bytes": clip.media.size_bytes,
            "cached_path": str(clip.media.cached_path) if clip.media.cached_path else None,
            "upstream_handle": clip.media.upstream_handle,
        },
        "provider_data": clip.provider_data,
        "fetched_at": clip.fetched_at.isoformat(),
    }
    return json.dumps(payload, default=_json_default)


def _marker_to_dict(m: Marker) -> dict[str, Any]:
    return {
        "name": m.name,
        "in_": _tc_to_dict(m.in_),
        "out": _tc_to_dict(m.out) if m.out is not None else None,
        "description": m.description,
        "category": m.category,
        "color": m.color,
    }


def _tc_to_dict(tc: Timecode) -> dict[str, Any]:
    return {"secs": tc.secs, "fps": tc.fps, "frm": tc.frm, "txt": tc.txt}


def _clip_from_json(raw: str) -> CanonicalClip:
    p = json.loads(raw)
    media = p["media"]
    from pathlib import Path
    return CanonicalClip(
        key=(p["key"][0], p["key"][1]),
        name=p["name"],
        duration_secs=float(p["duration_secs"]),
        fps=float(p["fps"]),
        markers=tuple(_marker_from_dict(m) for m in p["markers"]),
        fields={
            k: FieldValue(
                identifier=v["identifier"],
                value=v["value"],
                is_multi=bool(v.get("is_multi", False)),
            )
            for k, v in p["fields"].items()
        },
        notes=dict(p["notes"]),
        media=MediaRef(
            mime_type=media["mime_type"],
            size_bytes=media["size_bytes"],
            cached_path=Path(media["cached_path"]) if media["cached_path"] else None,
            upstream_handle=media["upstream_handle"],
        ),
        provider_data=p["provider_data"],
        fetched_at=datetime.fromisoformat(p["fetched_at"]),
    )


def _marker_from_dict(d: dict[str, Any]) -> Marker:
    return Marker(
        name=d["name"],
        in_=_tc_from_dict(d["in_"]),
        out=_tc_from_dict(d["out"]) if d.get("out") else None,
        description=d.get("description"),
        category=d.get("category"),
        color=d.get("color"),
    )


def _tc_from_dict(d: dict[str, Any]) -> Timecode:
    return Timecode(
        secs=float(d["secs"]),
        fps=float(d["fps"]),
        frm=d.get("frm"),
        txt=d.get("txt"),
    )


_ROW_COLS = (
    "provider_id",
    "provider_clip_id",
    "name",
    "catalog_id",
    "duration_secs",
    "fps",
    "canonical_json",
    "provider_etag",
    "fetched_at",
    "pinned_to_workspace_id",
)


class ClipCacheRepo:
    """DB-backed local mirror of upstream clip state."""

    async def upsert(
        self,
        conn: aiosqlite.Connection,
        *,
        clip: CanonicalClip,
        catalog_id: str,
        provider_etag: str | None = None,
    ) -> None:
        provider_id, provider_clip_id = clip.key
        await conn.execute(
            """
            INSERT INTO clip_cache
              (provider_id, provider_clip_id, name, catalog_id,
               duration_secs, fps, canonical_json, provider_etag, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(provider_id, provider_clip_id) DO UPDATE SET
              name           = excluded.name,
              catalog_id     = excluded.catalog_id,
              duration_secs  = excluded.duration_secs,
              fps            = excluded.fps,
              canonical_json = excluded.canonical_json,
              provider_etag  = excluded.provider_etag,
              fetched_at     = excluded.fetched_at
            """,
            (
                provider_id,
                provider_clip_id,
                clip.name,
                catalog_id,
                clip.duration_secs,
                clip.fps,
                _clip_to_json(clip),
                provider_etag,
                _now_iso(),
            ),
        )
        await conn.commit()

    async def get_by_key(
        self,
        conn: aiosqlite.Connection,
        *,
        provider_id: str,
        provider_clip_id: str,
    ) -> CanonicalClip | None:
        cur = await conn.execute(
            "SELECT canonical_json FROM clip_cache "
            "WHERE provider_id = ? AND provider_clip_id = ?",
            (provider_id, provider_clip_id),
        )
        row = await cur.fetchone()
        if row is None:
            return None
        return _clip_from_json(row[0])

    async def get_row(
        self,
        conn: aiosqlite.Connection,
        *,
        provider_id: str,
        provider_clip_id: str,
    ) -> dict[str, Any] | None:
        cur = await conn.execute(
            f"SELECT {', '.join(_ROW_COLS)} FROM clip_cache "
            "WHERE provider_id = ? AND provider_clip_id = ?",
            (provider_id, provider_clip_id),
        )
        row = await cur.fetchone()
        if row is None:
            return None
        return dict(zip(_ROW_COLS, row))

    async def list_by_catalog(
        self,
        conn: aiosqlite.Connection,
        *,
        provider_id: str,
        catalog_id: str,
    ) -> list[dict[str, Any]]:
        cur = await conn.execute(
            f"SELECT {', '.join(_ROW_COLS)} FROM clip_cache "
            "WHERE provider_id = ? AND catalog_id = ?",
            (provider_id, catalog_id),
        )
        return [dict(zip(_ROW_COLS, row)) for row in await cur.fetchall()]

    async def delete_by_key(
        self,
        conn: aiosqlite.Connection,
        *,
        provider_id: str,
        provider_clip_id: str,
    ) -> None:
        await conn.execute(
            "DELETE FROM clip_cache "
            "WHERE provider_id = ? AND provider_clip_id = ?",
            (provider_id, provider_clip_id),
        )
        await conn.commit()
```

- [ ] **Step 4: Run, verify pass**

- [ ] **Step 5: Commit**

```bash
git add backend/app/repositories/clip_cache.py tests/integration/test_clip_cache_repo.py
git commit -m "feat(repo): ClipCacheRepo with CanonicalClip JSON round-trip"
```

---

### Task 4: `FieldDefCacheRepo`

**Files:**
- Create: `backend/app/repositories/field_def_cache.py`
- Create: `tests/integration/test_field_def_cache_repo.py`

- [ ] **Step 1: Write failing test**

Create `tests/integration/test_field_def_cache_repo.py`:

```python
import pytest

from backend.app.archive.model import FieldDef
from backend.app.repositories.field_def_cache import FieldDefCacheRepo


def _fd(identifier: str = "pragafilm.barva", *, name: str = "Barva") -> FieldDef:
    return FieldDef(
        identifier=identifier,
        name=name,
        type="bool",
        is_multi=False,
        is_editable=True,
        picklist_values=None,
        provider_data={"raw": True},
    )


@pytest.mark.asyncio
async def test_upsert_and_get_round_trip(db):
    repo = FieldDefCacheRepo()
    await repo.upsert(db, provider_id="catdv", field_def=_fd())
    got = await repo.get_by_key(
        db, provider_id="catdv", identifier="pragafilm.barva"
    )
    assert got is not None
    assert got.identifier == "pragafilm.barva"
    assert got.name == "Barva"
    assert got.type == "bool"
    assert got.provider_data == {"raw": True}


@pytest.mark.asyncio
async def test_upsert_picklist_values_round_trip(db):
    repo = FieldDefCacheRepo()
    fd = FieldDef(
        identifier="t.theme",
        name="Theme",
        type="multi-picklist",
        is_multi=True,
        is_editable=True,
        picklist_values=("a", "b"),
    )
    await repo.upsert(db, provider_id="catdv", field_def=fd)
    got = await repo.get_by_key(db, provider_id="catdv", identifier="t.theme")
    assert got is not None
    assert got.picklist_values == ("a", "b")
    assert got.is_multi is True


@pytest.mark.asyncio
async def test_list_for_provider(db):
    repo = FieldDefCacheRepo()
    await repo.upsert(db, provider_id="catdv", field_def=_fd("a"))
    await repo.upsert(db, provider_id="catdv", field_def=_fd("b"))
    await repo.upsert(db, provider_id="fs", field_def=_fd("c"))
    rows = await repo.list_for_provider(db, provider_id="catdv")
    assert {fd.identifier for fd in rows} == {"a", "b"}


@pytest.mark.asyncio
async def test_replace_all_for_provider_overwrites_existing(db):
    repo = FieldDefCacheRepo()
    await repo.upsert(db, provider_id="catdv", field_def=_fd("old"))
    await repo.replace_all_for_provider(
        db, provider_id="catdv", field_defs=[_fd("new1"), _fd("new2")]
    )
    rows = await repo.list_for_provider(db, provider_id="catdv")
    assert {fd.identifier for fd in rows} == {"new1", "new2"}


@pytest.mark.asyncio
async def test_delete_by_key(db):
    repo = FieldDefCacheRepo()
    await repo.upsert(db, provider_id="catdv", field_def=_fd("x"))
    await repo.delete_by_key(db, provider_id="catdv", identifier="x")
    assert await repo.get_by_key(db, provider_id="catdv", identifier="x") is None


@pytest.mark.asyncio
async def test_latest_fetched_at(db):
    repo = FieldDefCacheRepo()
    assert await repo.latest_fetched_at(db, provider_id="catdv") is None
    await repo.upsert(db, provider_id="catdv", field_def=_fd("x"))
    ts = await repo.latest_fetched_at(db, provider_id="catdv")
    assert ts is not None
```

- [ ] **Step 2: Run, confirm failure**

- [ ] **Step 3: Implement the repository**

Create `backend/app/repositories/field_def_cache.py`:

```python
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Iterable

import aiosqlite

from backend.app.archive.model import FieldDef


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _field_def_to_json(fd: FieldDef) -> str:
    return json.dumps(
        {
            "identifier": fd.identifier,
            "name": fd.name,
            "type": fd.type,
            "is_multi": fd.is_multi,
            "is_editable": fd.is_editable,
            "picklist_values": list(fd.picklist_values)
                if fd.picklist_values is not None else None,
            "provider_data": fd.provider_data,
        }
    )


def _field_def_from_json(raw: str) -> FieldDef:
    p = json.loads(raw)
    pv = p.get("picklist_values")
    return FieldDef(
        identifier=p["identifier"],
        name=p["name"],
        type=p["type"],
        is_multi=bool(p["is_multi"]),
        is_editable=bool(p["is_editable"]),
        picklist_values=tuple(pv) if pv is not None else None,
        provider_data=p.get("provider_data") or {},
    )


class FieldDefCacheRepo:
    """DB-backed cache of provider field definitions."""

    async def upsert(
        self,
        conn: aiosqlite.Connection,
        *,
        provider_id: str,
        field_def: FieldDef,
    ) -> None:
        await conn.execute(
            """
            INSERT INTO field_def_cache (provider_id, identifier, json, fetched_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(provider_id, identifier) DO UPDATE SET
              json       = excluded.json,
              fetched_at = excluded.fetched_at
            """,
            (provider_id, field_def.identifier, _field_def_to_json(field_def), _now_iso()),
        )
        await conn.commit()

    async def get_by_key(
        self,
        conn: aiosqlite.Connection,
        *,
        provider_id: str,
        identifier: str,
    ) -> FieldDef | None:
        cur = await conn.execute(
            "SELECT json FROM field_def_cache "
            "WHERE provider_id = ? AND identifier = ?",
            (provider_id, identifier),
        )
        row = await cur.fetchone()
        if row is None:
            return None
        return _field_def_from_json(row[0])

    async def list_for_provider(
        self, conn: aiosqlite.Connection, *, provider_id: str
    ) -> list[FieldDef]:
        cur = await conn.execute(
            "SELECT json FROM field_def_cache WHERE provider_id = ? "
            "ORDER BY identifier",
            (provider_id,),
        )
        return [_field_def_from_json(row[0]) for row in await cur.fetchall()]

    async def replace_all_for_provider(
        self,
        conn: aiosqlite.Connection,
        *,
        provider_id: str,
        field_defs: Iterable[FieldDef],
    ) -> None:
        await conn.execute(
            "DELETE FROM field_def_cache WHERE provider_id = ?", (provider_id,)
        )
        now = _now_iso()
        for fd in field_defs:
            await conn.execute(
                "INSERT INTO field_def_cache "
                "(provider_id, identifier, json, fetched_at) VALUES (?, ?, ?, ?)",
                (provider_id, fd.identifier, _field_def_to_json(fd), now),
            )
        await conn.commit()

    async def delete_by_key(
        self,
        conn: aiosqlite.Connection,
        *,
        provider_id: str,
        identifier: str,
    ) -> None:
        await conn.execute(
            "DELETE FROM field_def_cache "
            "WHERE provider_id = ? AND identifier = ?",
            (provider_id, identifier),
        )
        await conn.commit()

    async def latest_fetched_at(
        self, conn: aiosqlite.Connection, *, provider_id: str
    ) -> str | None:
        cur = await conn.execute(
            "SELECT MAX(fetched_at) FROM field_def_cache WHERE provider_id = ?",
            (provider_id,),
        )
        row = await cur.fetchone()
        if row is None or row[0] is None:
            return None
        return row[0]
```

- [ ] **Step 4: Run, verify pass**

- [ ] **Step 5: Commit**

```bash
git add backend/app/repositories/field_def_cache.py tests/integration/test_field_def_cache_repo.py
git commit -m "feat(repo): FieldDefCacheRepo with FieldDef JSON round-trip"
```

---

### Task 5: CatdvClient `list_fields()` + fake server endpoint + mapping

**Files:**
- Modify: `backend/app/services/catdv_client.py`
- Modify: `backend/app/archive/providers/catdv/mapping.py`
- Modify: `tests/fakes/fake_catdv.py`

- [ ] **Step 1: Add `list_fields()` to `CatdvClient`**

Append a method modelled on `list_clips()`:

```python
async def list_fields(self) -> list[dict[str, Any]]:
    env = await self._call_json("GET", "/catdv/api/9/fields")
    data = env.data
    if isinstance(data, dict):
        items = data.get("fields") or []
    elif isinstance(data, list):
        items = data
    else:
        items = []
    return list(items)
```

- [ ] **Step 2: Add `field_def_from_catdv` mapper**

In `backend/app/archive/providers/catdv/mapping.py`, append:

```python
from backend.app.archive.model import FieldDef

_CATDV_TYPE_MAP: dict[str, str] = {
    "TEXT": "text",
    "STRING": "text",
    "INTEGER": "integer",
    "INT": "integer",
    "DECIMAL": "decimal",
    "FLOAT": "decimal",
    "DATE": "date",
    "PICKLIST": "picklist",
    "MULTI_PICKLIST": "multi-picklist",
    "BOOLEAN": "bool",
    "BOOL": "bool",
}


def field_def_from_catdv(raw: dict[str, Any]) -> FieldDef:
    identifier = str(raw.get("identifier") or raw.get("id") or raw.get("name") or "")
    name = str(raw.get("name") or identifier)
    raw_type = str(raw.get("type") or "TEXT").upper()
    is_multi_raw = raw.get("multi") or raw.get("isMulti") or False
    mapped_type = _CATDV_TYPE_MAP.get(raw_type, "text")
    if mapped_type == "picklist" and bool(is_multi_raw):
        mapped_type = "multi-picklist"
    pv = raw.get("picklistValues") or raw.get("values") or None
    if isinstance(pv, list):
        pv_tuple: tuple[str, ...] | None = tuple(str(v) for v in pv)
    else:
        pv_tuple = None
    return FieldDef(
        identifier=identifier,
        name=name,
        type=mapped_type,  # type: ignore[arg-type]
        is_multi=bool(is_multi_raw) or mapped_type == "multi-picklist",
        is_editable=bool(raw.get("editable", True)),
        picklist_values=pv_tuple,
        provider_data=raw,
    )
```

- [ ] **Step 3: Extend the fake CatDV server**

In `tests/fakes/fake_catdv.py`, in `FakeCatdv.__init__`, add:

```python
self.field_defs: list[dict] = []
```

And in `_register_routes`, add:

```python
@self.app.get("/catdv/api/9/fields")
async def list_fields(request: Request):
    if request.cookies.get("JSESSIONID") != "fake-session":
        return self._envelope("AUTH")
    return self._envelope("OK", data={"fields": self.field_defs})
```

- [ ] **Step 4: Commit (no new tests in this task; covered by Task 7)**

```bash
git add backend/app/services/catdv_client.py \
        backend/app/archive/providers/catdv/mapping.py \
        tests/fakes/fake_catdv.py
git commit -m "feat(archive/catdv): list_fields + FieldDef mapping; fake serves /api/9/fields"
```

---

### Task 6: `ArchiveProvider.list_field_definitions` Protocol method

**Files:**
- Modify: `backend/app/archive/provider.py`

- [ ] **Step 1: Update the Protocol**

In `backend/app/archive/provider.py`, add the method:

```python
from backend.app.archive.model import (
    CanonicalClip,
    ChangeSet,
    ClipPage,
    ClipQuery,
    FieldDef,
    ProviderClipId,
    ProviderId,
    WriteResult,
)

...

@runtime_checkable
class ArchiveProvider(Protocol):
    id: ProviderId = ""
    capabilities: ProviderCapabilities = None  # type: ignore[assignment]

    async def list_clips(self, catalog: str, query: ClipQuery) -> ClipPage: ...
    async def get_clip(self, clip: ProviderClipId) -> CanonicalClip: ...
    async def list_field_definitions(self) -> list[FieldDef]: ...
    async def apply_changes(self, change_set: ChangeSet) -> WriteResult: ...
```

- [ ] **Step 2: Commit alongside Task 7 (no test on its own; protocol shape verified via adapter)**

---

### Task 7: `CatdvArchiveAdapter` write-through caching for `get_clip` and `list_field_definitions`

**Files:**
- Modify: `backend/app/archive/providers/catdv/adapter.py`
- Modify: `tests/integration/test_catdv_adapter.py` (constructor signature)
- Create: `tests/integration/test_catdv_adapter_caching.py`

- [ ] **Step 1: Write the failing caching test**

Create `tests/integration/test_catdv_adapter_caching.py`:

```python
from datetime import datetime, timedelta, timezone

import pytest

from backend.app.archive.providers.catdv.adapter import CatdvArchiveAdapter
from backend.app.repositories.clip_cache import ClipCacheRepo
from backend.app.repositories.field_def_cache import FieldDefCacheRepo
from backend.app.services.catdv_client import CatdvClient
from tests.fakes.fake_catdv import running_fake_catdv


def _adapter(client, db, *, ttl_hours: int = 168, now=None):
    return CatdvArchiveAdapter(
        client=client,
        clip_cache_repo=ClipCacheRepo(),
        field_def_cache_repo=FieldDefCacheRepo(),
        db_provider=lambda: db,
        clip_cache_ttl_hours=ttl_hours,
        clock=now or (lambda: datetime.now(timezone.utc)),
    )


@pytest.mark.asyncio
async def test_get_clip_writes_through_to_cache(db):
    with running_fake_catdv() as (base_url, fake):
        fake.clips[1] = {"ID": 1, "name": "Clip_A", "fps": 25.0, "markers": []}
        async with CatdvClient(base_url, "klientAI", "secret") as client:
            adapter = _adapter(client, db)
            clip = await adapter.get_clip("1")
            assert clip.name == "Clip_A"

        # Underneath: clip_cache row exists.
        cur = await db.execute(
            "SELECT name FROM clip_cache WHERE provider_id='catdv' "
            "AND provider_clip_id='1'"
        )
        row = await cur.fetchone()
        assert row is not None and row[0] == "Clip_A"


@pytest.mark.asyncio
async def test_get_clip_serves_from_cache_within_ttl(db):
    with running_fake_catdv() as (base_url, fake):
        fake.clips[2] = {"ID": 2, "name": "Original", "fps": 25.0, "markers": []}
        async with CatdvClient(base_url, "klientAI", "secret") as client:
            adapter = _adapter(client, db, ttl_hours=24)
            first = await adapter.get_clip("2")
            assert first.name == "Original"

            # Mutate upstream; cache should still serve original.
            fake.clips[2]["name"] = "Mutated"
            second = await adapter.get_clip("2")
            assert second.name == "Original"


@pytest.mark.asyncio
async def test_get_clip_bypasses_cache_when_expired(db):
    with running_fake_catdv() as (base_url, fake):
        fake.clips[3] = {"ID": 3, "name": "Old", "fps": 25.0, "markers": []}
        # Frozen clock; advance manually.
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        clock = lambda: now  # noqa: E731

        async with CatdvClient(base_url, "klientAI", "secret") as client:
            adapter = _adapter(client, db, ttl_hours=1, now=clock)
            await adapter.get_clip("3")

            # Advance clock past TTL.
            now = now + timedelta(hours=2)
            fake.clips[3]["name"] = "Fresh"
            second = await adapter.get_clip("3")
            assert second.name == "Fresh"


@pytest.mark.asyncio
async def test_list_field_definitions_writes_through(db):
    with running_fake_catdv() as (base_url, fake):
        fake.field_defs = [
            {"identifier": "pragafilm.barva", "name": "Barva", "type": "BOOLEAN"},
            {"identifier": "pragafilm.theme", "name": "Theme",
             "type": "PICKLIST", "multi": True,
             "picklistValues": ["a", "b"]},
        ]
        async with CatdvClient(base_url, "klientAI", "secret") as client:
            adapter = _adapter(client, db)
            fds = await adapter.list_field_definitions()
        ids = {fd.identifier for fd in fds}
        assert ids == {"pragafilm.barva", "pragafilm.theme"}

        cur = await db.execute(
            "SELECT COUNT(*) FROM field_def_cache WHERE provider_id='catdv'"
        )
        assert (await cur.fetchone())[0] == 2


@pytest.mark.asyncio
async def test_list_field_definitions_serves_from_cache_within_ttl(db):
    with running_fake_catdv() as (base_url, fake):
        fake.field_defs = [
            {"identifier": "f", "name": "F", "type": "TEXT"},
        ]
        async with CatdvClient(base_url, "klientAI", "secret") as client:
            adapter = _adapter(client, db, ttl_hours=24)
            first = await adapter.list_field_definitions()
            assert len(first) == 1

            # Mutate upstream; cache should still serve old set.
            fake.field_defs = [
                {"identifier": "f", "name": "F", "type": "TEXT"},
                {"identifier": "g", "name": "G", "type": "TEXT"},
            ]
            second = await adapter.list_field_definitions()
            assert {fd.identifier for fd in second} == {"f"}
```

- [ ] **Step 2: Run, confirm failure**

- [ ] **Step 3: Rewrite the adapter**

Update `backend/app/archive/providers/catdv/adapter.py`:

```python
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from backend.app.archive.errors import (
    AuthError,
    FatalProviderError,
    RetryableError,
)
from backend.app.archive.model import (
    CanonicalClip,
    ChangeSet,
    ClipPage,
    ClipQuery,
    FieldDef,
    WriteResult,
)
from backend.app.archive.provider import ProviderCapabilities
from backend.app.archive.providers.catdv.mapping import (
    field_def_from_catdv,
    from_catdv_clip,
)
from backend.app.services.catdv_client import (
    CatdvAuthError,
    CatdvBusyError,
    CatdvClient,
    CatdvError,
)


class CatdvArchiveAdapter:
    id = "catdv"
    capabilities = ProviderCapabilities(
        supports_markers=True,
        supports_notes=frozenset({"notes", "bigNotes"}),
        supports_field_create=False,
        supports_etag=False,
        media_is_local=False,
        write_atomicity="per-clip",
    )

    def __init__(
        self,
        *,
        client: CatdvClient,
        clip_cache_repo: Any = None,
        field_def_cache_repo: Any = None,
        db_provider: Callable[[], Any] | None = None,
        clip_cache_ttl_hours: int = 168,
        clock: Callable[[], datetime] | None = None,
        default_catalog_id: str = "",
    ) -> None:
        self._client = client
        self._clip_cache = clip_cache_repo
        self._field_def_cache = field_def_cache_repo
        self._db_provider = db_provider
        self._ttl = timedelta(hours=clip_cache_ttl_hours)
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._default_catalog_id = default_catalog_id

    # --- read API -----------------------------------------------------

    async def list_clips(self, catalog: str, query: ClipQuery) -> ClipPage:
        try:
            data = await self._client.list_clips(
                int(catalog),
                offset=query.offset,
                limit=query.limit,
                q=query.text,
            )
        except CatdvAuthError as exc:
            raise AuthError(str(exc)) from exc
        except CatdvBusyError as exc:
            raise RetryableError(str(exc)) from exc
        except CatdvError as exc:
            raise FatalProviderError(str(exc)) from exc

        now = self._clock()
        raw_items = data.get("clips") if isinstance(data, dict) else []
        items = tuple(from_catdv_clip(raw, fetched_at=now) for raw in (raw_items or []))
        return ClipPage(
            items=items,
            total=int((data or {}).get("total", len(items))),
            offset=query.offset,
            limit=query.limit,
        )

    async def get_clip(self, clip: str) -> CanonicalClip:
        # Cache-first: serve fresh row if within TTL.
        cached = await self._read_clip_from_cache(clip)
        if cached is not None:
            return cached

        try:
            raw = await self._client.get_clip(int(clip))
        except CatdvAuthError as exc:
            raise AuthError(str(exc)) from exc
        except CatdvBusyError as exc:
            raise RetryableError(str(exc)) from exc
        except CatdvError as exc:
            raise FatalProviderError(str(exc)) from exc

        canonical = from_catdv_clip(raw, fetched_at=self._clock())
        await self._write_clip_through(canonical, raw)
        return canonical

    async def list_field_definitions(self) -> list[FieldDef]:
        cached = await self._read_field_defs_from_cache()
        if cached is not None:
            return cached

        try:
            rows = await self._client.list_fields()
        except CatdvAuthError as exc:
            raise AuthError(str(exc)) from exc
        except CatdvBusyError as exc:
            raise RetryableError(str(exc)) from exc
        except CatdvError as exc:
            raise FatalProviderError(str(exc)) from exc

        defs = [field_def_from_catdv(r) for r in rows]
        await self._write_field_defs_through(defs)
        return defs

    # --- write API ----------------------------------------------------

    async def apply_changes(self, change_set: ChangeSet) -> WriteResult:
        provider_id, clip_id_str = change_set.clip_key
        if provider_id != self.id:
            raise FatalProviderError(
                f"ChangeSet for provider {provider_id!r} sent to catdv adapter"
            )
        from backend.app.archive.providers.catdv.payload import build_put_payload

        try:
            current = await self._client.get_clip(int(clip_id_str))
        except CatdvAuthError as exc:
            raise AuthError(str(exc)) from exc
        except CatdvBusyError as exc:
            raise RetryableError(str(exc)) from exc
        except CatdvError as exc:
            raise FatalProviderError(str(exc)) from exc

        payload = build_put_payload(current=current, ops=list(change_set.ops))
        if not payload:
            return WriteResult(status="ok", upstream_response={}, detail="no-op")

        try:
            response = await self._client.put_clip(int(clip_id_str), payload)
        except CatdvAuthError as exc:
            raise AuthError(str(exc)) from exc
        except CatdvBusyError as exc:
            raise RetryableError(str(exc)) from exc
        except CatdvError as exc:
            raise FatalProviderError(str(exc)) from exc

        return WriteResult(status="ok", upstream_response=response)

    # --- cache helpers -----------------------------------------------

    def _cache_enabled(self) -> bool:
        return (
            self._clip_cache is not None
            and self._db_provider is not None
        )

    def _field_def_cache_enabled(self) -> bool:
        return (
            self._field_def_cache is not None
            and self._db_provider is not None
        )

    async def _read_clip_from_cache(self, clip_id: str) -> CanonicalClip | None:
        if not self._cache_enabled():
            return None
        db = self._db_provider()
        row = await self._clip_cache.get_row(
            db, provider_id=self.id, provider_clip_id=clip_id
        )
        if row is None:
            return None
        if self._is_expired(row.get("fetched_at")):
            return None
        return await self._clip_cache.get_by_key(
            db, provider_id=self.id, provider_clip_id=clip_id
        )

    async def _write_clip_through(
        self, canonical: CanonicalClip, raw: dict[str, Any]
    ) -> None:
        if not self._cache_enabled():
            return
        catalog_id = self._catalog_id_for_clip(raw)
        await self._clip_cache.upsert(
            self._db_provider(),
            clip=canonical,
            catalog_id=catalog_id,
        )

    def _catalog_id_for_clip(self, raw: dict[str, Any]) -> str:
        # CatDV clip payloads embed catalogue ID under varying keys depending
        # on server version; fall back to the configured default.
        for key in ("catalogId", "catalogID", "catalog_id"):
            v = raw.get(key)
            if v is not None:
                return str(v)
        cat = raw.get("catalog")
        if isinstance(cat, dict):
            for key in ("ID", "id"):
                if key in cat:
                    return str(cat[key])
        return self._default_catalog_id

    async def _read_field_defs_from_cache(self) -> list[FieldDef] | None:
        if not self._field_def_cache_enabled():
            return None
        db = self._db_provider()
        latest = await self._field_def_cache.latest_fetched_at(
            db, provider_id=self.id
        )
        if latest is None or self._is_expired(latest):
            return None
        return await self._field_def_cache.list_for_provider(
            db, provider_id=self.id
        )

    async def _write_field_defs_through(self, defs: list[FieldDef]) -> None:
        if not self._field_def_cache_enabled():
            return
        await self._field_def_cache.replace_all_for_provider(
            self._db_provider(),
            provider_id=self.id,
            field_defs=defs,
        )

    def _is_expired(self, fetched_at_iso: str | None) -> bool:
        if fetched_at_iso is None:
            return True
        try:
            ts = datetime.fromisoformat(fetched_at_iso)
        except (TypeError, ValueError):
            return True
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return (self._clock() - ts) > self._ttl
```

- [ ] **Step 4: Update existing adapter tests for new constructor**

In `tests/integration/test_catdv_adapter.py`, the existing tests construct `CatdvArchiveAdapter(client=client)` — with the new defaults (`clip_cache_repo=None`, etc.), caching is silently skipped, so those tests should keep passing as-is. Verify by running them.

- [ ] **Step 5: Run all adapter tests, verify pass**

```bash
.venv/bin/pytest tests/integration/test_catdv_adapter.py tests/integration/test_catdv_adapter_caching.py -v
```

- [ ] **Step 6: Commit**

```bash
git add backend/app/archive/provider.py \
        backend/app/archive/providers/catdv/adapter.py \
        tests/integration/test_catdv_adapter_caching.py
git commit -m "feat(archive/catdv): write-through cache for get_clip + list_field_definitions"
```

---

### Task 8: Settings + registry + AppContext wiring

**Files:**
- Modify: `backend/app/settings.py`
- Modify: `backend/app/archive/registry.py`
- Modify: `backend/app/context.py`

- [ ] **Step 1: Add `clip_cache_ttl_hours` to Settings**

In `backend/app/settings.py`, add next to `archive_provider`:

```python
clip_cache_ttl_hours: int = 168
```

- [ ] **Step 2: Update `build_archive_provider`**

```python
def build_archive_provider(
    settings: Any,
    *,
    catdv_client: Any,
    clip_cache_repo: Any = None,
    field_def_cache_repo: Any = None,
    db_provider: Any = None,
) -> ArchiveProvider:
    name = getattr(settings, "archive_provider", "catdv")
    if name == "catdv":
        if catdv_client is None:
            raise ValueError("archive_provider=catdv requires a catdv_client")
        return CatdvArchiveAdapter(
            client=catdv_client,
            clip_cache_repo=clip_cache_repo,
            field_def_cache_repo=field_def_cache_repo,
            db_provider=db_provider,
            clip_cache_ttl_hours=int(
                getattr(settings, "clip_cache_ttl_hours", 168)
            ),
            default_catalog_id=str(getattr(settings, "catdv_catalog_id", "")),
        )
    raise ValueError(f"unknown archive_provider: {name!r}")
```

- [ ] **Step 3: Wire AppContext**

In `backend/app/context.py`:

- Import `ClipCacheRepo`, `FieldDefCacheRepo`.
- Add two `field(default_factory=...)` attributes alongside `ai_store_files_repo`.
- Pass them into `build_archive_provider` in `build()`.

```python
from backend.app.repositories.clip_cache import ClipCacheRepo
from backend.app.repositories.field_def_cache import FieldDefCacheRepo

# inside the dataclass:
clip_cache_repo: ClipCacheRepo = field(default_factory=ClipCacheRepo)
field_def_cache_repo: FieldDefCacheRepo = field(default_factory=FieldDefCacheRepo)

# inside build(), replace the existing archive build line:
ctx.archive = build_archive_provider(
    settings,
    catdv_client=ctx.catdv,
    clip_cache_repo=ctx.clip_cache_repo,
    field_def_cache_repo=ctx.field_def_cache_repo,
    db_provider=lambda c=ctx: c.db,
)
```

- [ ] **Step 4: Run context test + everything else affected**

```bash
.venv/bin/pytest tests/integration/test_context.py -v
```

- [ ] **Step 5: Commit**

```bash
git add backend/app/settings.py \
        backend/app/archive/registry.py \
        backend/app/context.py
git commit -m "feat(context): wire ClipCacheRepo + FieldDefCacheRepo into CatdvArchiveAdapter"
```

---

### Task 9: End-to-end validation

**Files:** none (verification only)

- [ ] **Step 1: Run full test suite (excluding the known pre-existing root-perm failure)**

```bash
source .venv/bin/activate && python -m pytest -q \
  --deselect tests/integration/test_proxy_resolver_fs.py::test_fs_resolver_raises_when_unreadable
```

Expected: all green.

- [ ] **Step 2: Run linter**

```bash
ruff check backend tests
```

Expected: clean. Fix any issues introduced.

- [ ] **Step 3: Decision log (if any non-spec design call survives)**

If the chosen TTL semantics, single-vs-split migration, or `provider_data` JSON encoding policy differ from a future reader's reasonable expectation, append an entry to `docs/decisions.md` matching the existing two-paragraph format (context / alternatives / choice / why).

**Acceptance criteria for this task:**
- Test suite passes (pre-existing root-perm test deselected).
- Ruff is clean.
- A grep for `provider_id` in `clip_cache`, `field_def_cache`, and the six clip-keyed tables confirms columns exist post-migration on a fresh DB.
- Caching test demonstrates: warm read hits SQLite, cold/expired re-fetches upstream.
- No call site outside the adapter touches `clip_cache` / `field_def_cache` tables (verifiable via `git grep`).

---

## End-to-end validation summary

After all tasks land, the following holds:

1. A fresh DB initialised by `apply_migrations` has six clip-keyed tables, each with `provider_id` and `provider_clip_id` columns; existing rows (if migrating from a 0001/0002 DB) are backfilled with `'catdv'` and the stringified `catdv_clip_id`.
2. Two new tables — `clip_cache` keyed on `(provider_id, provider_clip_id)` with the documented columns and a `(provider_id, catalog_id)` index; `field_def_cache` keyed on `(provider_id, identifier)`.
3. Two repositories — `ClipCacheRepo` and `FieldDefCacheRepo` — provide opaque (de)serialisation of the canonical types.
4. `CatdvArchiveAdapter.get_clip` is cache-first with TTL = `settings.clip_cache_ttl_hours`. Same for the new `list_field_definitions`.
5. `AppContext` exposes both repos and threads them into the adapter; the public read API of the adapter is unchanged from the caller's perspective.
6. No behaviour change visible to the user; PR 4 (WriteQueue + SyncEngine) builds on this groundwork.
