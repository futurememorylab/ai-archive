# PR 2: AIInputStore Port + GcsInputStore Adapter — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Introduce the `AIInputStore` port + a canonical `UploadedRef`/`AIStoreCapabilities` model, route every "where does Gemini read media from" access through a `GcsInputStore` adapter, generalise `GeminiService.annotate` to accept an opaque file reference, and rename the `gcs_files` registry table to `ai_store_files` with a `store_id` column. No user-visible behaviour change. This is the second of seven PRs implementing the design in `docs/specs/2026-05-19-archive-abstraction-and-offline-mode-design.md` (§6, §13 PR 2).

**Architecture:** New `backend/app/archive/ai_store.py` defines the `AIInputStore` Protocol + `AIStoreCapabilities` + `UploadedRef` + `StoreHealth`. A new `backend/app/archive/ai_stores/` package holds adapter implementations. The `GcsInputStore` adapter wraps the existing low-level `GcsService` (kept as-is) and the new `AIStoreFilesRepo`, exposing the `AIInputStore` surface to the app. The annotator stops referring to `gs://` URIs or `GcsService` directly; it asks `ai_store.ensure_uploaded()` for an `UploadedRef`, then `ai_store.reference_for_gemini(upload)` for the SDK-shaped dict it hands to Gemini. `GeminiService.annotate` is generalised to take `file_ref: dict` rather than `gcs_uri: str` + `mime: str`. A `GeminiFilesInputStore` stub (every method raises `NotImplementedError`) proves the port compiles against a non-GCS shape. `AppContext` exposes `ctx.ai_store: AIInputStore` and `ctx.ai_store_files_repo: AIStoreFilesRepo`; the old `ctx.gcs` and `ctx.gcs_files_repo` attributes are removed.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, dataclasses (frozen), `asyncio`, `aiosqlite`, `pytest` + `pytest-asyncio`. No new third-party deps. The migration is a plain SQL file applied by `migrations_runner.apply_migrations`.

**Scope guardrail:** This plan implements ONLY what spec §13 PR 2 lists. No `provider_id`/`provider_clip_id` columns (those land in PR 3), no `clip_cache` table, no `pending_operations`, no workspaces, no FS adapter, no `handle` column rename (that lands in PR 6). The `GeminiFilesInputStore` is a `NotImplementedError` stub on purpose — wiring it for real is a separate PR when a real user asks. The blob naming scheme inside the bucket stays at `clips/<clip_id>.mov`; multi-provider blob namespacing is deferred to a later PR. **No new ArchiveProvider methods** — `archive.fetch_or_resolve_media(clip_key)` (mentioned in spec §6.4) is PR 5/7 work; the annotator keeps calling `proxy_resolver.path_for_clip_id(...)` directly in this PR.

**Decision recorded in plan (not yet in `docs/decisions.md`):** PR 2 deliberately keeps `catdv_clip_id INTEGER` as the row key in `ai_store_files`. PR 3 will add `provider_id`/`provider_clip_id` columns and migrate the PK. Keeping the int column for PR 2 means the adapter has to cast `int(clip_key[1])` when reading/writing the registry — acceptable, since `clip_key[0]` is always `"catdv"` until PR 3.

---

## File map

### New files

| Path | Responsibility |
|---|---|
| `backend/app/archive/ai_store_model.py` | Canonical AI-store types: `UploadedRef`, `AIStoreCapabilities`, `StoreHealth`. Frozen dataclasses. |
| `backend/app/archive/ai_store.py` | `AIInputStore` `Protocol` definition. Imports `UploadedRef`/`AIStoreCapabilities`/`StoreHealth` from `ai_store_model`. |
| `backend/app/archive/ai_stores/__init__.py` | Package marker. |
| `backend/app/archive/ai_stores/registry.py` | `build_ai_input_store(settings, *, ai_store_files_repo, db) -> AIInputStore` factory. Selects by `settings.ai_input_store`. |
| `backend/app/archive/ai_stores/gcs/__init__.py` | Package marker. Re-exports `GcsInputStore`. |
| `backend/app/archive/ai_stores/gcs/adapter.py` | `GcsInputStore` — implements `AIInputStore` over an existing `GcsService` + `AIStoreFilesRepo`. |
| `backend/app/archive/ai_stores/gemini_files/__init__.py` | Package marker. Re-exports `GeminiFilesInputStore`. |
| `backend/app/archive/ai_stores/gemini_files/adapter.py` | `GeminiFilesInputStore` — stub. Every method raises `NotImplementedError`. Proves the Protocol shape. |
| `backend/app/repositories/ai_store_files.py` | `AIStoreFilesRepo` — DB-backed registry keyed on `(store_id, catdv_clip_id)`. Replaces `GcsFilesRepo`. |
| `backend/migrations/0002_ai_store_files.sql` | Migration: create `ai_store_files`, copy rows from `gcs_files`, drop `gcs_files`. |
| `tests/unit/test_ai_store_model.py` | Constructs `UploadedRef`/`AIStoreCapabilities`/`StoreHealth`, asserts immutability + defaults. |
| `tests/unit/test_ai_store_protocol.py` | Asserts `AIInputStore` Protocol exposes the right names; `GeminiFilesInputStore` is a `runtime_checkable` instance. |
| `tests/unit/test_ai_store_registry.py` | `build_ai_input_store` returns the right adapter; raises on unknown id. |
| `tests/unit/test_gcs_input_store.py` | Unit tests for `GcsInputStore`: dedup, reference_for_gemini shape, capabilities, evict, status. Uses `MagicMock` for `GcsService` (mirroring existing `tests/unit/test_gcs.py`). |
| `tests/integration/test_ai_store_files_repo.py` | DB-level tests for `AIStoreFilesRepo`: upsert, get by `(store_id, clip_id)`, touch, two stores coexist. |
| `tests/integration/test_migration_0002.py` | Runs the full migration chain on a DB with rows in `gcs_files`; asserts they land in `ai_store_files` with `store_id='gcs:<bucket>'`. |

### Modified files

| Path | Change |
|---|---|
| `backend/app/settings.py` | Add `ai_input_store: str = "gcs"`. |
| `backend/app/services/gemini.py` | Change `annotate(*, gcs_uri, mime, prompt, schema, model)` → `annotate(*, file_ref: dict, prompt, schema, model)`. Update `annotate_with_retry` signature accordingly. |
| `backend/app/context.py` | Remove `gcs` field + `gcs_files_repo: GcsFilesRepo`. Add `ai_store: AIInputStore \| None = None` + `ai_store_files_repo: AIStoreFilesRepo = field(default_factory=AIStoreFilesRepo)`. Build `ctx.ai_store = build_ai_input_store(settings, ...)` after archive wiring. |
| `backend/app/services/annotator.py` | Drop `gcs` and `gcs_files_repo` params; add `ai_store: AIInputStore` and `ai_store_files_repo: AIStoreFilesRepo`. Rewrite the upload/dedup block to call `ai_store.ensure_uploaded()` + `reference_for_gemini()`. Pass `file_ref` (not `gcs_uri`) into `gemini.annotate`. |
| `backend/app/routes/jobs.py` | Replace `ctx.gcs` and `ctx.gcs_files_repo` with `ctx.ai_store` and `ctx.ai_store_files_repo` in the readiness gate and in `run_job(...)` keyword args. |
| `backend/app/startup.py` | Replace `gcs=...` param with `ai_store=...`. Replace `gcs._bucket.exists()` with `await ai_store.health()` → `result.ok`. Drop `bucket_name` reference. |
| `backend/app/main.py` | No code path actually calls `run_checks` from `lifespan`, but the dev-only `_real_external_enabled` helper still gates on `gcs_bucket_name`; leave it alone (`gcs_bucket_name` is still a required setting for the GCS adapter). |
| `tests/integration/test_initial_schema.py` | `EXPECTED_TABLES`: remove `"gcs_files"`, add `"ai_store_files"`. |
| `tests/integration/test_context.py` | The `_StubGcs` monkeypatch keeps working (it stubs the low-level `GcsService` that the adapter wraps). Add assertion that `ctx.ai_store` is a `GcsInputStore` instance. Remove any reference to `ctx.gcs_files_repo` that asserts the old class. |
| `tests/integration/test_annotator_worker.py` | Replace `FakeGcs` with `FakeAIStore` exposing `ensure_uploaded()`, `reference_for_gemini()`, `status()`. Pass `ai_store=FakeAIStore(...)` and `ai_store_files_repo=AIStoreFilesRepo()` into `run_job(...)`. |
| `tests/integration/test_startup_check.py` | Replace `FakeGcs`/`FakeBucket` with `FakeAIStore` that has an `async health()` returning a `StoreHealth`. Update parameter name in calls. |
| `tests/unit/test_gemini.py` | Update calls: pass `file_ref={"file_data": {"file_uri": "gs://...", "mime_type": "video/quicktime"}}` instead of `gcs_uri=` + `mime=`. |
| `tests/unit/test_gemini_retry.py` | Same shape change as `test_gemini.py`. |

### Deleted files

| Path | Reason |
|---|---|
| `backend/app/repositories/gcs_files.py` | Replaced by `backend/app/repositories/ai_store_files.py`. After all imports updated. |
| `tests/integration/test_gcs_files_repo.py` | Replaced by `tests/integration/test_ai_store_files_repo.py`. |

### Files kept as-is

| Path | Reason |
|---|---|
| `backend/app/services/gcs.py` | The low-level `GcsService` (bucket + blob operations) is exactly what the spec means by "wrapping current `services/gcs.py`". `GcsInputStore` composes it; we don't rewrite it. |
| `tests/unit/test_gcs.py` | These exercise `GcsService` directly. The class is unchanged. |

---

## Tasks

### Task 1: Canonical AI-store model types

**Files:**
- Create: `backend/app/archive/ai_store_model.py`
- Test: `tests/unit/test_ai_store_model.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_ai_store_model.py`:

```python
from datetime import datetime, timezone

import pytest

from backend.app.archive.ai_store_model import (
    AIStoreCapabilities,
    StoreHealth,
    UploadedRef,
)


def test_uploaded_ref_holds_handle_and_metadata():
    ref = UploadedRef(
        handle="gs://bucket/clips/1.mov",
        mime_type="video/quicktime",
        size_bytes=12345,
        sha256="deadbeef",
        uploaded_at=datetime.now(timezone.utc),
        expires_at=None,
    )
    assert ref.handle == "gs://bucket/clips/1.mov"
    assert ref.expires_at is None


def test_uploaded_ref_is_frozen():
    ref = UploadedRef(
        handle="gs://b/x.mov",
        mime_type="video/quicktime",
        size_bytes=1,
        sha256="a",
        uploaded_at=datetime.now(timezone.utc),
        expires_at=None,
    )
    with pytest.raises(Exception):
        ref.handle = "gs://other"  # type: ignore[misc]


def test_capabilities_is_frozen_dataclass():
    caps = AIStoreCapabilities(
        persistent=True,
        dedup_by_sha256=True,
        max_file_bytes=10_000_000_000,
    )
    assert caps.persistent is True
    with pytest.raises(Exception):
        caps.persistent = False  # type: ignore[misc]


def test_store_health_defaults():
    h = StoreHealth(ok=True)
    assert h.ok is True
    assert h.detail is None
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
.venv/bin/pytest tests/unit/test_ai_store_model.py -v
```
Expected: FAIL — `ModuleNotFoundError: No module named 'backend.app.archive.ai_store_model'`.

- [ ] **Step 3: Implement `ai_store_model.py`**

Create `backend/app/archive/ai_store_model.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class UploadedRef:
    """Where an AI input store put a copy of a clip's media bytes."""

    handle: str
    mime_type: str
    size_bytes: int
    sha256: str
    uploaded_at: datetime
    expires_at: datetime | None = None


@dataclass(frozen=True)
class AIStoreCapabilities:
    persistent: bool
    dedup_by_sha256: bool
    max_file_bytes: int


@dataclass(frozen=True)
class StoreHealth:
    ok: bool
    detail: str | None = None
```

- [ ] **Step 4: Run test to verify it passes**

Run:
```bash
.venv/bin/pytest tests/unit/test_ai_store_model.py -v
```
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/app/archive/ai_store_model.py tests/unit/test_ai_store_model.py
git commit -m "feat(archive): canonical AI input store model (UploadedRef, capabilities, health)"
```

---

### Task 2: `AIInputStore` Protocol

**Files:**
- Create: `backend/app/archive/ai_store.py`
- Test: `tests/unit/test_ai_store_protocol.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_ai_store_protocol.py`:

```python
from backend.app.archive.ai_store import AIInputStore


def test_ai_input_store_protocol_exposes_expected_names():
    expected = {
        "id",
        "capabilities",
        "ensure_uploaded",
        "status",
        "evict",
        "health",
        "reference_for_gemini",
    }
    assert expected.issubset(set(dir(AIInputStore)))
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
.venv/bin/pytest tests/unit/test_ai_store_protocol.py -v
```
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement `ai_store.py`**

Create `backend/app/archive/ai_store.py`:

```python
from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from backend.app.archive.ai_store_model import (
    AIStoreCapabilities,
    StoreHealth,
    UploadedRef,
)
from backend.app.archive.model import ClipKey


@runtime_checkable
class AIInputStore(Protocol):
    """Port: where Gemini reads media bytes from.

    Implementations are responsible for (a) putting a copy of the local file
    somewhere Vertex AI Gemini can read it, (b) producing the SDK-shaped
    fragment that `generate_content()` accepts, and (c) tracking the upload
    in their own persistent index.
    """

    id: str
    capabilities: AIStoreCapabilities

    async def ensure_uploaded(
        self, clip_key: ClipKey, local_path: Path, mime: str
    ) -> UploadedRef: ...

    async def status(self, clip_key: ClipKey) -> UploadedRef | None: ...

    async def evict(self, clip_key: ClipKey) -> None: ...

    async def health(self) -> StoreHealth: ...

    async def reference_for_gemini(self, ref: UploadedRef) -> dict: ...
```

- [ ] **Step 4: Run test to verify it passes**

Run:
```bash
.venv/bin/pytest tests/unit/test_ai_store_protocol.py -v
```
Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/app/archive/ai_store.py tests/unit/test_ai_store_protocol.py
git commit -m "feat(archive): AIInputStore Protocol"
```

---

### Task 3: Migration 0002 — rename `gcs_files` → `ai_store_files`

**Files:**
- Create: `backend/migrations/0002_ai_store_files.sql`
- Modify: `tests/integration/test_initial_schema.py`
- Test: `tests/integration/test_migration_0002.py`

- [ ] **Step 1: Inspect the existing schema**

Read `backend/migrations/0001_initial.sql` lines 50-59 to confirm the current `gcs_files` columns. Do not modify it.

- [ ] **Step 2: Write the failing migration test**

Create `tests/integration/test_migration_0002.py`:

```python
from pathlib import Path

import pytest

from backend.app.db import open_db
from backend.app.migrations_runner import apply_migrations

MIGRATIONS = Path(__file__).resolve().parents[2] / "backend" / "migrations"


@pytest.mark.asyncio
async def test_migration_0002_renames_gcs_files_and_backfills_store_id(tmp_path):
    db_path = tmp_path / "test.db"
    # Step 1: apply only the initial migration manually so we can seed rows.
    async with open_db(db_path) as conn:
        sql = (MIGRATIONS / "0001_initial.sql").read_text()
        await conn.executescript(sql)
        await conn.execute(
            "INSERT INTO schema_migrations(name) VALUES ('0001_initial.sql')"
        )
        await conn.commit()

        # Seed a row in the old shape.
        await conn.execute(
            """
            INSERT INTO gcs_files
              (catdv_clip_id, gcs_uri, mime_type, size_bytes, sha256,
               uploaded_at, last_used_at)
            VALUES (42, 'gs://my-bucket/clips/42.mov', 'video/quicktime',
                    100, 'abc', '2026-05-19T00:00:00Z', '2026-05-19T00:00:00Z')
            """
        )
        await conn.commit()

    # Step 2: now run the full migrations chain (0002 should run).
    async with open_db(db_path) as conn:
        applied = await apply_migrations(conn, MIGRATIONS)
        assert "0002_ai_store_files.sql" in applied

        # Old table is gone.
        cur = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='gcs_files'"
        )
        assert await cur.fetchone() is None

        # New table exists with the row migrated.
        cur = await conn.execute(
            "SELECT store_id, catdv_clip_id, gcs_uri, sha256, expires_at "
            "FROM ai_store_files WHERE catdv_clip_id = 42"
        )
        row = await cur.fetchone()
        assert row is not None
        store_id, clip_id, uri, sha, expires = row
        assert store_id == "gcs:my-bucket"
        assert clip_id == 42
        assert uri == "gs://my-bucket/clips/42.mov"
        assert sha == "abc"
        assert expires is None


@pytest.mark.asyncio
async def test_migration_0002_is_idempotent_when_gcs_files_empty(tmp_path):
    db_path = tmp_path / "test.db"
    async with open_db(db_path) as conn:
        applied = await apply_migrations(conn, MIGRATIONS)
        assert "0001_initial.sql" in applied
        assert "0002_ai_store_files.sql" in applied

        cur = await conn.execute("SELECT COUNT(*) FROM ai_store_files")
        assert (await cur.fetchone())[0] == 0
```

- [ ] **Step 3: Run test to verify it fails**

Run:
```bash
.venv/bin/pytest tests/integration/test_migration_0002.py -v
```
Expected: FAIL — no `0002_ai_store_files.sql` migration file yet.

- [ ] **Step 4: Write the migration SQL**

Create `backend/migrations/0002_ai_store_files.sql`:

```sql
-- Rename gcs_files -> ai_store_files; introduce store_id column.
-- store_id format is "gcs:<bucket>" for GCS uploads. Backfill by parsing the
-- existing gs:// URI: substring after "gs://" up to the next "/".
-- PK becomes (store_id, catdv_clip_id) so the same clip can have rows in
-- multiple stores (e.g. someone switches AI_INPUT_STORE later).

CREATE TABLE ai_store_files (
  store_id        TEXT NOT NULL,
  catdv_clip_id   INTEGER NOT NULL,
  gcs_uri         TEXT NOT NULL,
  mime_type       TEXT NOT NULL,
  size_bytes      INTEGER NOT NULL,
  sha256          TEXT NOT NULL,
  uploaded_at     TEXT NOT NULL,
  last_used_at    TEXT NOT NULL,
  expires_at      TEXT,
  PRIMARY KEY (store_id, catdv_clip_id)
);

CREATE INDEX idx_ai_store_files_clip ON ai_store_files(catdv_clip_id);

INSERT INTO ai_store_files
  (store_id, catdv_clip_id, gcs_uri, mime_type, size_bytes, sha256,
   uploaded_at, last_used_at, expires_at)
SELECT
  'gcs:' || substr(gcs_uri, 6, instr(substr(gcs_uri, 6), '/') - 1),
  catdv_clip_id,
  gcs_uri,
  mime_type,
  size_bytes,
  sha256,
  uploaded_at,
  last_used_at,
  NULL
FROM gcs_files;

DROP TABLE gcs_files;
```

- [ ] **Step 5: Update `EXPECTED_TABLES` in the schema test**

In `tests/integration/test_initial_schema.py`, modify the `EXPECTED_TABLES` set:

```python
EXPECTED_TABLES = {
    "templates",
    "jobs",
    "job_items",
    "proxy_cache",
    "ai_store_files",   # was: "gcs_files"
    "annotations",
    "annotations_fts",
    "review_items",
    "write_log",
    "embeddings",
    "tags",
    "schema_migrations",
}
```

- [ ] **Step 6: Run both migration tests**

Run:
```bash
.venv/bin/pytest tests/integration/test_migration_0002.py tests/integration/test_initial_schema.py -v
```
Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add backend/migrations/0002_ai_store_files.sql \
        tests/integration/test_migration_0002.py \
        tests/integration/test_initial_schema.py
git commit -m "feat(archive): migration 0002 — rename gcs_files to ai_store_files with store_id"
```

---

### Task 4: `AIStoreFilesRepo`

**Files:**
- Create: `backend/app/repositories/ai_store_files.py`
- Create: `tests/integration/test_ai_store_files_repo.py`

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_ai_store_files_repo.py`:

```python
import pytest

from backend.app.repositories.ai_store_files import AIStoreFilesRepo


@pytest.mark.asyncio
async def test_upsert_and_get(db):
    repo = AIStoreFilesRepo()
    await repo.upsert(
        db,
        store_id="gcs:b",
        clip_id=42,
        gcs_uri="gs://b/clips/42.mov",
        mime_type="video/quicktime",
        size_bytes=100,
        sha256="abc",
    )
    row = await repo.get(db, store_id="gcs:b", clip_id=42)
    assert row is not None
    assert row["gcs_uri"] == "gs://b/clips/42.mov"
    assert row["store_id"] == "gcs:b"
    assert row["sha256"] == "abc"


@pytest.mark.asyncio
async def test_get_returns_none_when_store_mismatch(db):
    repo = AIStoreFilesRepo()
    await repo.upsert(
        db,
        store_id="gcs:a",
        clip_id=42,
        gcs_uri="gs://a/clips/42.mov",
        mime_type="video/quicktime",
        size_bytes=100,
        sha256="x",
    )
    assert await repo.get(db, store_id="gcs:other", clip_id=42) is None


@pytest.mark.asyncio
async def test_upsert_replaces_on_same_pk(db):
    repo = AIStoreFilesRepo()
    await repo.upsert(
        db,
        store_id="gcs:b",
        clip_id=42,
        gcs_uri="gs://b/clips/42.mov",
        mime_type="video/quicktime",
        size_bytes=100,
        sha256="aaa",
    )
    await repo.upsert(
        db,
        store_id="gcs:b",
        clip_id=42,
        gcs_uri="gs://b/clips/42.mov",
        mime_type="video/quicktime",
        size_bytes=200,
        sha256="bbb",
    )
    row = await repo.get(db, store_id="gcs:b", clip_id=42)
    assert row["sha256"] == "bbb"
    assert row["size_bytes"] == 200


@pytest.mark.asyncio
async def test_two_stores_can_hold_same_clip_independently(db):
    repo = AIStoreFilesRepo()
    await repo.upsert(
        db,
        store_id="gcs:a",
        clip_id=42,
        gcs_uri="gs://a/clips/42.mov",
        mime_type="video/quicktime",
        size_bytes=100,
        sha256="aa",
    )
    await repo.upsert(
        db,
        store_id="gcs:b",
        clip_id=42,
        gcs_uri="gs://b/clips/42.mov",
        mime_type="video/quicktime",
        size_bytes=100,
        sha256="bb",
    )
    a = await repo.get(db, store_id="gcs:a", clip_id=42)
    b = await repo.get(db, store_id="gcs:b", clip_id=42)
    assert a["sha256"] == "aa"
    assert b["sha256"] == "bb"


@pytest.mark.asyncio
async def test_touch_updates_last_used_at(db):
    repo = AIStoreFilesRepo()
    await repo.upsert(
        db,
        store_id="gcs:b",
        clip_id=42,
        gcs_uri="gs://b/clips/42.mov",
        mime_type="video/quicktime",
        size_bytes=100,
        sha256="abc",
    )
    before = (await repo.get(db, store_id="gcs:b", clip_id=42))["last_used_at"]

    # Force a different timestamp by writing it directly. (We don't sleep.)
    await db.execute(
        "UPDATE ai_store_files SET last_used_at = ? "
        "WHERE store_id = ? AND catdv_clip_id = ?",
        ("2020-01-01T00:00:00+00:00", "gcs:b", 42),
    )
    await db.commit()

    await repo.touch(db, store_id="gcs:b", clip_id=42)
    after = (await repo.get(db, store_id="gcs:b", clip_id=42))["last_used_at"]
    assert after > "2020-01-01T00:00:00+00:00"
    # before-fixture and after-touch are both recent ISO strings; we only assert
    # the touched value advanced past the artificially-old timestamp above.


@pytest.mark.asyncio
async def test_delete_row(db):
    repo = AIStoreFilesRepo()
    await repo.upsert(
        db,
        store_id="gcs:b",
        clip_id=42,
        gcs_uri="gs://b/clips/42.mov",
        mime_type="video/quicktime",
        size_bytes=100,
        sha256="abc",
    )
    await repo.delete(db, store_id="gcs:b", clip_id=42)
    assert await repo.get(db, store_id="gcs:b", clip_id=42) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
.venv/bin/pytest tests/integration/test_ai_store_files_repo.py -v
```
Expected: FAIL — `ModuleNotFoundError: No module named 'backend.app.repositories.ai_store_files'`.

- [ ] **Step 3: Implement the repository**

Create `backend/app/repositories/ai_store_files.py`:

```python
from datetime import datetime, timezone
from typing import Any

import aiosqlite


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class AIStoreFilesRepo:
    """DB-backed registry of AI input store uploads.

    Keyed on (store_id, catdv_clip_id). store_id is the AIInputStore's id,
    e.g. "gcs:my-bucket" or "gemini-files".
    """

    async def upsert(
        self,
        conn: aiosqlite.Connection,
        *,
        store_id: str,
        clip_id: int,
        gcs_uri: str,
        mime_type: str,
        size_bytes: int,
        sha256: str,
        expires_at: str | None = None,
    ) -> None:
        now = _now_iso()
        await conn.execute(
            """
            INSERT INTO ai_store_files
              (store_id, catdv_clip_id, gcs_uri, mime_type, size_bytes,
               sha256, uploaded_at, last_used_at, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(store_id, catdv_clip_id) DO UPDATE SET
              gcs_uri      = excluded.gcs_uri,
              mime_type    = excluded.mime_type,
              size_bytes   = excluded.size_bytes,
              sha256       = excluded.sha256,
              uploaded_at  = excluded.uploaded_at,
              last_used_at = excluded.last_used_at,
              expires_at   = excluded.expires_at
            """,
            (store_id, clip_id, gcs_uri, mime_type, size_bytes, sha256,
             now, now, expires_at),
        )
        await conn.commit()

    async def get(
        self, conn: aiosqlite.Connection, *, store_id: str, clip_id: int
    ) -> dict[str, Any] | None:
        cur = await conn.execute(
            """
            SELECT store_id, catdv_clip_id, gcs_uri, mime_type, size_bytes,
                   sha256, uploaded_at, last_used_at, expires_at
            FROM ai_store_files
            WHERE store_id = ? AND catdv_clip_id = ?
            """,
            (store_id, clip_id),
        )
        row = await cur.fetchone()
        if row is None:
            return None
        return dict(
            zip(
                (
                    "store_id",
                    "catdv_clip_id",
                    "gcs_uri",
                    "mime_type",
                    "size_bytes",
                    "sha256",
                    "uploaded_at",
                    "last_used_at",
                    "expires_at",
                ),
                row,
            )
        )

    async def touch(
        self, conn: aiosqlite.Connection, *, store_id: str, clip_id: int
    ) -> None:
        await conn.execute(
            "UPDATE ai_store_files SET last_used_at = ? "
            "WHERE store_id = ? AND catdv_clip_id = ?",
            (_now_iso(), store_id, clip_id),
        )
        await conn.commit()

    async def delete(
        self, conn: aiosqlite.Connection, *, store_id: str, clip_id: int
    ) -> None:
        await conn.execute(
            "DELETE FROM ai_store_files WHERE store_id = ? AND catdv_clip_id = ?",
            (store_id, clip_id),
        )
        await conn.commit()
```

- [ ] **Step 4: Run tests, verify pass**

Run:
```bash
.venv/bin/pytest tests/integration/test_ai_store_files_repo.py -v
```
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/app/repositories/ai_store_files.py tests/integration/test_ai_store_files_repo.py
git commit -m "feat(archive): AIStoreFilesRepo keyed on (store_id, clip_id)"
```

---

### Task 5: `GcsInputStore` adapter

**Files:**
- Create: `backend/app/archive/ai_stores/__init__.py`
- Create: `backend/app/archive/ai_stores/gcs/__init__.py`
- Create: `backend/app/archive/ai_stores/gcs/adapter.py`
- Test: `tests/unit/test_gcs_input_store.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_gcs_input_store.py`:

```python
import hashlib
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.app.archive.ai_store_model import (
    AIStoreCapabilities,
    StoreHealth,
    UploadedRef,
)
from backend.app.archive.ai_stores.gcs.adapter import GcsInputStore


def _sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _make_fake_db() -> MagicMock:
    """The adapter is constructed with a callable that yields the live db
    connection per call. Tests pass a sentinel; the repo is mocked anyway."""
    return MagicMock(name="db_conn")


class FakeRepo:
    """Stands in for AIStoreFilesRepo. In-memory dict."""

    def __init__(self) -> None:
        self.rows: dict[tuple[str, int], dict] = {}

    async def get(self, db, *, store_id: str, clip_id: int):
        return self.rows.get((store_id, clip_id))

    async def upsert(self, db, *, store_id: str, clip_id: int, **kwargs):
        self.rows[(store_id, clip_id)] = {
            "store_id": store_id, "catdv_clip_id": clip_id, **kwargs,
            "uploaded_at": "now", "last_used_at": "now",
        }

    async def touch(self, db, *, store_id: str, clip_id: int):
        self.rows[(store_id, clip_id)]["last_used_at"] = "later"

    async def delete(self, db, *, store_id: str, clip_id: int):
        self.rows.pop((store_id, clip_id), None)


class FakeGcsService:
    def __init__(self, bucket_name: str = "test-bucket"):
        self.bucket_name = bucket_name
        self.uploads: list[tuple[int, Path, str]] = []
        self.deletes: list[int] = []
        self._bucket = MagicMock()
        self._bucket.exists = MagicMock(return_value=True)

    def gs_uri(self, clip_id: int) -> str:
        return f"gs://{self.bucket_name}/clips/{clip_id}.mov"

    def upload_if_absent(self, *, clip_id: int, local_path: Path, mime: str) -> str:
        self.uploads.append((clip_id, local_path, mime))
        return self.gs_uri(clip_id)

    def delete(self, *, clip_id: int) -> None:
        self.deletes.append(clip_id)


@pytest.fixture
def adapter_factory(tmp_path):
    def _factory(*, bucket: str = "test-bucket"):
        gcs = FakeGcsService(bucket_name=bucket)
        repo = FakeRepo()
        db = _make_fake_db()
        adapter = GcsInputStore(
            gcs=gcs, files_repo=repo, db_provider=lambda: db
        )
        return adapter, gcs, repo, db
    return _factory


def test_id_is_gcs_prefixed_with_bucket(adapter_factory):
    adapter, _, _, _ = adapter_factory(bucket="my-bucket")
    assert adapter.id == "gcs:my-bucket"


def test_capabilities(adapter_factory):
    adapter, _, _, _ = adapter_factory()
    assert isinstance(adapter.capabilities, AIStoreCapabilities)
    assert adapter.capabilities.persistent is True
    assert adapter.capabilities.dedup_by_sha256 is True
    assert adapter.capabilities.max_file_bytes > 0


@pytest.mark.asyncio
async def test_ensure_uploaded_first_time_uploads_and_records(adapter_factory, tmp_path):
    adapter, gcs, repo, db = adapter_factory()
    local = tmp_path / "1.mov"
    local.write_bytes(b"hello")

    ref = await adapter.ensure_uploaded(
        clip_key=("catdv", "1"), local_path=local, mime="video/quicktime"
    )

    assert isinstance(ref, UploadedRef)
    assert ref.handle == "gs://test-bucket/clips/1.mov"
    assert ref.mime_type == "video/quicktime"
    assert ref.sha256 == _sha256(local)
    assert gcs.uploads == [(1, local, "video/quicktime")]
    assert ("gcs:test-bucket", 1) in repo.rows


@pytest.mark.asyncio
async def test_ensure_uploaded_dedups_when_sha256_matches(adapter_factory, tmp_path):
    adapter, gcs, repo, db = adapter_factory()
    local = tmp_path / "1.mov"
    local.write_bytes(b"hello")
    sha = _sha256(local)

    # Pre-seed the repo as if a previous upload happened.
    repo.rows[("gcs:test-bucket", 1)] = {
        "store_id": "gcs:test-bucket",
        "catdv_clip_id": 1,
        "gcs_uri": "gs://test-bucket/clips/1.mov",
        "mime_type": "video/quicktime",
        "size_bytes": 5,
        "sha256": sha,
        "uploaded_at": "earlier",
        "last_used_at": "earlier",
        "expires_at": None,
    }

    ref = await adapter.ensure_uploaded(
        clip_key=("catdv", "1"), local_path=local, mime="video/quicktime"
    )
    assert ref.handle == "gs://test-bucket/clips/1.mov"
    assert gcs.uploads == []  # no re-upload
    # touch happened.
    assert repo.rows[("gcs:test-bucket", 1)]["last_used_at"] == "later"


@pytest.mark.asyncio
async def test_ensure_uploaded_reuploads_when_sha256_mismatch(adapter_factory, tmp_path):
    adapter, gcs, repo, db = adapter_factory()
    local = tmp_path / "1.mov"
    local.write_bytes(b"new bytes")

    repo.rows[("gcs:test-bucket", 1)] = {
        "store_id": "gcs:test-bucket",
        "catdv_clip_id": 1,
        "gcs_uri": "gs://test-bucket/clips/1.mov",
        "mime_type": "video/quicktime",
        "size_bytes": 5,
        "sha256": "STALE_SHA",
        "uploaded_at": "earlier",
        "last_used_at": "earlier",
        "expires_at": None,
    }

    ref = await adapter.ensure_uploaded(
        clip_key=("catdv", "1"), local_path=local, mime="video/quicktime"
    )
    assert ref.sha256 == _sha256(local)
    assert gcs.uploads == [(1, local, "video/quicktime")]


@pytest.mark.asyncio
async def test_status_returns_none_when_absent(adapter_factory):
    adapter, _, _, _ = adapter_factory()
    assert await adapter.status(("catdv", "1")) is None


@pytest.mark.asyncio
async def test_status_returns_uploaded_ref_when_present(adapter_factory, tmp_path):
    adapter, _, repo, _ = adapter_factory()
    repo.rows[("gcs:test-bucket", 1)] = {
        "store_id": "gcs:test-bucket",
        "catdv_clip_id": 1,
        "gcs_uri": "gs://test-bucket/clips/1.mov",
        "mime_type": "video/quicktime",
        "size_bytes": 5,
        "sha256": "abc",
        "uploaded_at": "2026-05-19T00:00:00+00:00",
        "last_used_at": "2026-05-19T00:00:00+00:00",
        "expires_at": None,
    }
    ref = await adapter.status(("catdv", "1"))
    assert ref is not None
    assert ref.handle == "gs://test-bucket/clips/1.mov"
    assert ref.sha256 == "abc"


@pytest.mark.asyncio
async def test_evict_deletes_blob_and_row(adapter_factory):
    adapter, gcs, repo, _ = adapter_factory()
    repo.rows[("gcs:test-bucket", 7)] = {
        "store_id": "gcs:test-bucket",
        "catdv_clip_id": 7,
        "gcs_uri": "gs://test-bucket/clips/7.mov",
        "mime_type": "video/quicktime",
        "size_bytes": 1,
        "sha256": "z",
        "uploaded_at": "x",
        "last_used_at": "x",
        "expires_at": None,
    }
    await adapter.evict(("catdv", "7"))
    assert gcs.deletes == [7]
    assert ("gcs:test-bucket", 7) not in repo.rows


@pytest.mark.asyncio
async def test_evict_is_noop_when_no_row(adapter_factory):
    adapter, gcs, _, _ = adapter_factory()
    # Should not raise; no row, no blob delete.
    await adapter.evict(("catdv", "404"))
    assert gcs.deletes == []


@pytest.mark.asyncio
async def test_reference_for_gemini_returns_file_data_shape(adapter_factory):
    from datetime import datetime, timezone

    adapter, _, _, _ = adapter_factory()
    ref = UploadedRef(
        handle="gs://test-bucket/clips/1.mov",
        mime_type="video/quicktime",
        size_bytes=10,
        sha256="x",
        uploaded_at=datetime.now(timezone.utc),
        expires_at=None,
    )
    out = await adapter.reference_for_gemini(ref)
    assert out == {
        "file_data": {
            "file_uri": "gs://test-bucket/clips/1.mov",
            "mime_type": "video/quicktime",
        }
    }


@pytest.mark.asyncio
async def test_health_reports_bucket_exists(adapter_factory):
    adapter, gcs, _, _ = adapter_factory()
    h = await adapter.health()
    assert isinstance(h, StoreHealth)
    assert h.ok is True


@pytest.mark.asyncio
async def test_health_reports_failure_when_bucket_missing(adapter_factory):
    adapter, gcs, _, _ = adapter_factory()
    gcs._bucket.exists = MagicMock(return_value=False)
    h = await adapter.health()
    assert h.ok is False
    assert "test-bucket" in (h.detail or "")
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
.venv/bin/pytest tests/unit/test_gcs_input_store.py -v
```
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Create the package files**

Create `backend/app/archive/ai_stores/__init__.py` (empty):

```python
"""AI input store adapters."""
```

Create `backend/app/archive/ai_stores/gcs/__init__.py`:

```python
"""GCS-backed AI input store adapter."""

from backend.app.archive.ai_stores.gcs.adapter import GcsInputStore

__all__ = ["GcsInputStore"]
```

- [ ] **Step 4: Implement the adapter**

Create `backend/app/archive/ai_stores/gcs/adapter.py`:

```python
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from backend.app.archive.ai_store_model import (
    AIStoreCapabilities,
    StoreHealth,
    UploadedRef,
)
from backend.app.archive.model import ClipKey

log = logging.getLogger(__name__)


class GcsInputStore:
    """Implements the AIInputStore Protocol over a GcsService and a DB-backed
    registry. PR 2 keeps the bucket-side blob naming as 'clips/<int>.mov'.
    """

    capabilities = AIStoreCapabilities(
        persistent=True,
        dedup_by_sha256=True,
        # No hard cap from GCS itself; we set a large bound for parity with
        # the AIInputStore contract.
        max_file_bytes=5 * 1024 * 1024 * 1024 * 1024,  # 5 TiB
    )

    def __init__(
        self,
        *,
        gcs: Any,                     # GcsService duck-typed for testability
        files_repo: Any,              # AIStoreFilesRepo
        db_provider: Callable[[], Any],
    ) -> None:
        self._gcs = gcs
        self._repo = files_repo
        self._db_provider = db_provider
        self.id = f"gcs:{gcs.bucket_name}"

    async def ensure_uploaded(
        self, clip_key: ClipKey, local_path: Path, mime: str
    ) -> UploadedRef:
        clip_id = int(clip_key[1])
        db = self._db_provider()
        sha = _sha256(local_path)

        existing = await self._repo.get(db, store_id=self.id, clip_id=clip_id)
        if existing is not None and existing["sha256"] == sha:
            await self._repo.touch(db, store_id=self.id, clip_id=clip_id)
            return self._row_to_ref(existing)

        gcs_uri = self._gcs.upload_if_absent(
            clip_id=clip_id, local_path=local_path, mime=mime
        )
        size = local_path.stat().st_size

        await self._repo.upsert(
            db,
            store_id=self.id,
            clip_id=clip_id,
            gcs_uri=gcs_uri,
            mime_type=mime,
            size_bytes=size,
            sha256=sha,
            expires_at=None,
        )

        return UploadedRef(
            handle=gcs_uri,
            mime_type=mime,
            size_bytes=size,
            sha256=sha,
            uploaded_at=datetime.now(timezone.utc),
            expires_at=None,
        )

    async def status(self, clip_key: ClipKey) -> UploadedRef | None:
        clip_id = int(clip_key[1])
        row = await self._repo.get(
            self._db_provider(), store_id=self.id, clip_id=clip_id
        )
        if row is None:
            return None
        return self._row_to_ref(row)

    async def evict(self, clip_key: ClipKey) -> None:
        clip_id = int(clip_key[1])
        db = self._db_provider()
        row = await self._repo.get(db, store_id=self.id, clip_id=clip_id)
        if row is None:
            return
        try:
            self._gcs.delete(clip_id=clip_id)
        except Exception:  # noqa: BLE001
            log.exception("gcs delete failed for clip_id=%s", clip_id)
        await self._repo.delete(db, store_id=self.id, clip_id=clip_id)

    async def health(self) -> StoreHealth:
        try:
            ok = self._gcs._bucket.exists()
        except Exception as exc:  # noqa: BLE001
            return StoreHealth(ok=False, detail=str(exc))
        if not ok:
            return StoreHealth(
                ok=False, detail=f"bucket not found: {self._gcs.bucket_name}"
            )
        return StoreHealth(ok=True)

    async def reference_for_gemini(self, ref: UploadedRef) -> dict:
        return {
            "file_data": {
                "file_uri": ref.handle,
                "mime_type": ref.mime_type,
            }
        }

    @staticmethod
    def _row_to_ref(row: dict[str, Any]) -> UploadedRef:
        uploaded_at = _parse_iso(row.get("uploaded_at"))
        expires_at = _parse_iso(row.get("expires_at"))
        return UploadedRef(
            handle=row["gcs_uri"],
            mime_type=row["mime_type"],
            size_bytes=row["size_bytes"],
            sha256=row["sha256"],
            uploaded_at=uploaded_at,
            expires_at=expires_at,
        )


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _parse_iso(s: str | None) -> datetime | None:
    if s is None:
        return None
    try:
        return datetime.fromisoformat(s)
    except (TypeError, ValueError):
        return None
```

- [ ] **Step 5: Run tests, verify pass**

Run:
```bash
.venv/bin/pytest tests/unit/test_gcs_input_store.py -v
```
Expected: 12 passed.

- [ ] **Step 6: Commit**

```bash
git add backend/app/archive/ai_stores/__init__.py \
        backend/app/archive/ai_stores/gcs/__init__.py \
        backend/app/archive/ai_stores/gcs/adapter.py \
        tests/unit/test_gcs_input_store.py
git commit -m "feat(archive/gcs): GcsInputStore adapter implements AIInputStore"
```

---

### Task 6: `GeminiFilesInputStore` stub

**Files:**
- Create: `backend/app/archive/ai_stores/gemini_files/__init__.py`
- Create: `backend/app/archive/ai_stores/gemini_files/adapter.py`
- Extend: `tests/unit/test_ai_store_protocol.py`

- [ ] **Step 1: Extend the protocol test to assert the stub exists**

Append to `tests/unit/test_ai_store_protocol.py`:

```python
import pytest

from backend.app.archive.ai_store import AIInputStore as _AIInputStore  # noqa: F401
from backend.app.archive.ai_stores.gemini_files.adapter import (
    GeminiFilesInputStore,
)


def test_gemini_files_stub_advertises_correct_id_and_capabilities():
    stub = GeminiFilesInputStore()
    assert stub.id == "gemini-files"
    assert stub.capabilities.persistent is False
    assert stub.capabilities.dedup_by_sha256 is False
    assert stub.capabilities.max_file_bytes == 2 * 1024 * 1024 * 1024  # 2 GB


@pytest.mark.asyncio
async def test_gemini_files_stub_methods_raise_not_implemented(tmp_path):
    stub = GeminiFilesInputStore()
    local = tmp_path / "x.mov"
    local.write_bytes(b"x")
    with pytest.raises(NotImplementedError):
        await stub.ensure_uploaded(("catdv", "1"), local, "video/quicktime")
    with pytest.raises(NotImplementedError):
        await stub.status(("catdv", "1"))
    with pytest.raises(NotImplementedError):
        await stub.evict(("catdv", "1"))
    with pytest.raises(NotImplementedError):
        await stub.health()
```

- [ ] **Step 2: Run to confirm failure**

Run:
```bash
.venv/bin/pytest tests/unit/test_ai_store_protocol.py -v
```
Expected: FAIL — module not found.

- [ ] **Step 3: Create the package files**

Create `backend/app/archive/ai_stores/gemini_files/__init__.py`:

```python
"""Gemini Files API AI input store adapter (stub in PR 2)."""

from backend.app.archive.ai_stores.gemini_files.adapter import (
    GeminiFilesInputStore,
)

__all__ = ["GeminiFilesInputStore"]
```

- [ ] **Step 4: Implement the stub**

Create `backend/app/archive/ai_stores/gemini_files/adapter.py`:

```python
from __future__ import annotations

from pathlib import Path

from backend.app.archive.ai_store_model import (
    AIStoreCapabilities,
    StoreHealth,
    UploadedRef,
)
from backend.app.archive.model import ClipKey


class GeminiFilesInputStore:
    """Stub adapter for Google Gemini Files API.

    Defined here to prove the AIInputStore Protocol compiles against a
    non-GCS shape. Not wired up in PR 2; methods raise NotImplementedError.
    A later PR will implement this for installs that prefer not to manage
    a GCS bucket.
    """

    id = "gemini-files"
    capabilities = AIStoreCapabilities(
        persistent=False,
        dedup_by_sha256=False,
        max_file_bytes=2 * 1024 * 1024 * 1024,  # 2 GB, per Files API docs
    )

    async def ensure_uploaded(
        self, clip_key: ClipKey, local_path: Path, mime: str
    ) -> UploadedRef:
        raise NotImplementedError(
            "GeminiFilesInputStore is a stub; wire it in a follow-on PR."
        )

    async def status(self, clip_key: ClipKey) -> UploadedRef | None:
        raise NotImplementedError

    async def evict(self, clip_key: ClipKey) -> None:
        raise NotImplementedError

    async def health(self) -> StoreHealth:
        raise NotImplementedError

    async def reference_for_gemini(self, ref: UploadedRef) -> dict:
        return {"file_data": {"file_id": ref.handle}}
```

- [ ] **Step 5: Run tests, verify pass**

Run:
```bash
.venv/bin/pytest tests/unit/test_ai_store_protocol.py -v
```
Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
git add backend/app/archive/ai_stores/gemini_files/__init__.py \
        backend/app/archive/ai_stores/gemini_files/adapter.py \
        tests/unit/test_ai_store_protocol.py
git commit -m "feat(archive/gemini-files): stub AIInputStore adapter (NotImplementedError)"
```

---

### Task 7: AI store registry + settings knob

**Files:**
- Create: `backend/app/archive/ai_stores/registry.py`
- Modify: `backend/app/settings.py`
- Test: `tests/unit/test_ai_store_registry.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_ai_store_registry.py`:

```python
import pytest

from backend.app.archive.ai_stores.gcs.adapter import GcsInputStore
from backend.app.archive.ai_stores.gemini_files.adapter import (
    GeminiFilesInputStore,
)
from backend.app.archive.ai_stores.registry import build_ai_input_store


class FakeGcs:
    def __init__(self, bucket_name: str = "b"):
        self.bucket_name = bucket_name


class FakeRepo:
    pass


def _settings(name: str):
    class S:
        ai_input_store = name

    return S()


def test_build_returns_gcs_adapter_when_settings_says_gcs():
    store = build_ai_input_store(
        _settings("gcs"),
        gcs_service=FakeGcs(),
        files_repo=FakeRepo(),
        db_provider=lambda: None,
    )
    assert isinstance(store, GcsInputStore)
    assert store.id == "gcs:b"


def test_build_returns_gemini_files_stub_when_settings_says_gemini_files():
    store = build_ai_input_store(
        _settings("gemini-files"),
        gcs_service=None,
        files_repo=FakeRepo(),
        db_provider=lambda: None,
    )
    assert isinstance(store, GeminiFilesInputStore)
    assert store.id == "gemini-files"


def test_build_raises_on_unknown_store():
    with pytest.raises(ValueError, match="unknown"):
        build_ai_input_store(
            _settings("nope"),
            gcs_service=FakeGcs(),
            files_repo=FakeRepo(),
            db_provider=lambda: None,
        )


def test_build_raises_when_gcs_service_missing_for_gcs():
    with pytest.raises(ValueError, match="gcs_service"):
        build_ai_input_store(
            _settings("gcs"),
            gcs_service=None,
            files_repo=FakeRepo(),
            db_provider=lambda: None,
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
.venv/bin/pytest tests/unit/test_ai_store_registry.py -v
```
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement `registry.py`**

Create `backend/app/archive/ai_stores/registry.py`:

```python
from __future__ import annotations

from typing import Any, Callable

from backend.app.archive.ai_store import AIInputStore
from backend.app.archive.ai_stores.gcs.adapter import GcsInputStore
from backend.app.archive.ai_stores.gemini_files.adapter import (
    GeminiFilesInputStore,
)


def build_ai_input_store(
    settings: Any,
    *,
    gcs_service: Any,
    files_repo: Any,
    db_provider: Callable[[], Any],
) -> AIInputStore:
    """Construct the active AIInputStore from settings.

    `settings` is duck-typed (only `ai_input_store` is read).
    """
    name = getattr(settings, "ai_input_store", "gcs")
    if name == "gcs":
        if gcs_service is None:
            raise ValueError("ai_input_store=gcs requires a gcs_service")
        return GcsInputStore(
            gcs=gcs_service, files_repo=files_repo, db_provider=db_provider
        )
    if name == "gemini-files":
        return GeminiFilesInputStore()
    raise ValueError(f"unknown ai_input_store: {name!r}")
```

- [ ] **Step 4: Add `ai_input_store` to Settings**

In `backend/app/settings.py`, add a field next to `archive_provider`:

```python
    ai_input_store: str = "gcs"
```

- [ ] **Step 5: Run all unit tests, verify pass**

Run:
```bash
.venv/bin/pytest tests/unit -v
```
Expected: all green, including the 4 new registry tests.

- [ ] **Step 6: Commit**

```bash
git add backend/app/archive/ai_stores/registry.py \
        backend/app/settings.py \
        tests/unit/test_ai_store_registry.py
git commit -m "feat(archive): AI input store registry + settings.ai_input_store knob"
```

---

### Task 8: Refactor `GeminiService.annotate` to take `file_ref`

**Files:**
- Modify: `backend/app/services/gemini.py`
- Modify: `tests/unit/test_gemini.py`
- Modify: `tests/unit/test_gemini_retry.py`

- [ ] **Step 1: Read the existing tests**

Read both files top to bottom — they're short — so the rewrite preserves intent.

```bash
.venv/bin/pytest tests/unit/test_gemini.py tests/unit/test_gemini_retry.py -v
```
Confirm they currently pass.

- [ ] **Step 2: Update `tests/unit/test_gemini.py` to the new signature**

In `tests/unit/test_gemini.py`, replace every call site that uses `gcs_uri=...` and `mime=...` with a `file_ref=...` kwarg. The fragment Gemini expects is `{"file_data": {"file_uri": "...", "mime_type": "..."}}`. Concretely, change call sites that look like:

```python
service.annotate(
    gcs_uri="gs://b/clips/1.mov",
    mime="video/quicktime",
    prompt="p",
    schema={},
    model="m",
)
```

to:

```python
service.annotate(
    file_ref={
        "file_data": {
            "file_uri": "gs://b/clips/1.mov",
            "mime_type": "video/quicktime",
        }
    },
    prompt="p",
    schema={},
    model="m",
)
```

Apply the same change to every `service.annotate(...)` call in the file. Inside the test that inspects `FakeModels.calls`, the assertion about `contents[1]` should now check the equal dict shape rather than reconstructing the file_data fragment.

- [ ] **Step 3: Update `tests/unit/test_gemini_retry.py` similarly**

Replace every `gcs_uri=...`, `mime=...` pair in calls to `annotate_with_retry(...)` and `service.annotate(...)` with the equivalent `file_ref=...` kwarg.

- [ ] **Step 4: Run tests — expect failure (signature mismatch)**

Run:
```bash
.venv/bin/pytest tests/unit/test_gemini.py tests/unit/test_gemini_retry.py -v
```
Expected: FAIL — `TypeError: unexpected keyword argument 'file_ref'` (because the implementation still takes `gcs_uri`/`mime`).

- [ ] **Step 5: Update `backend/app/services/gemini.py`**

Replace `GeminiService.annotate` signature so it takes the file_ref dict directly and forwards it as the second `contents` entry. Likewise update `annotate_with_retry`. The full new file:

```python
import asyncio
from typing import Any

from google import genai  # type: ignore[import-not-found]


class GeminiError(RuntimeError):
    pass


class GeminiQuotaError(GeminiError):
    """Rate / quota exceeded; retryable with backoff."""


class GeminiSafetyError(GeminiError):
    """Response blocked by safety policy; do not retry."""


class GeminiPermissionError(GeminiError):
    """Service account lacks required IAM; operator must fix."""


def _classify(exc: Exception) -> Exception:
    msg = str(exc).lower()
    if "quota" in msg or "resource exhausted" in msg or "rate" in msg:
        return GeminiQuotaError(str(exc))
    if "safety" in msg or "content policy" in msg or "blocked" in msg:
        return GeminiSafetyError(str(exc))
    if "permission" in msg or "access denied" in msg or "forbidden" in msg:
        return GeminiPermissionError(str(exc))
    return GeminiError(str(exc))


class GeminiService:
    def __init__(self, project: str, location: str) -> None:
        self._client = genai.Client(vertexai=True, project=project, location=location)

    def annotate(
        self,
        *,
        file_ref: dict[str, Any],
        prompt: str,
        schema: dict[str, Any],
        model: str,
    ) -> dict[str, Any]:
        try:
            response = self._client.models.generate_content(
                model=model,
                contents=[
                    {"text": prompt},
                    file_ref,
                ],
                config={
                    "response_mime_type": "application/json",
                    "response_schema": schema,
                },
            )
        except Exception as exc:  # noqa: BLE001
            raise _classify(exc) from exc

        text = getattr(response, "text", "")
        raw = response.model_dump() if hasattr(response, "model_dump") else {}
        return {"text": text, "raw": raw}


async def annotate_with_retry(
    service: "GeminiService",
    *,
    file_ref: dict[str, Any],
    prompt: str,
    schema: dict[str, Any],
    model: str,
    max_attempts: int = 5,
    base_delay_secs: float = 1.0,
) -> dict[str, Any]:
    """Call service.annotate retrying only GeminiQuotaError with exponential backoff."""
    delay = base_delay_secs
    for attempt in range(1, max_attempts + 1):
        try:
            return service.annotate(
                file_ref=file_ref,
                prompt=prompt,
                schema=schema,
                model=model,
            )
        except GeminiQuotaError:
            if attempt >= max_attempts:
                raise
            await asyncio.sleep(delay)
            delay *= 2
    raise RuntimeError("unreachable")
```

- [ ] **Step 6: Run gemini tests, verify pass**

Run:
```bash
.venv/bin/pytest tests/unit/test_gemini.py tests/unit/test_gemini_retry.py -v
```
Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/gemini.py tests/unit/test_gemini.py tests/unit/test_gemini_retry.py
git commit -m "refactor(gemini): annotate takes file_ref dict, not gcs_uri+mime"
```

---

### Task 9: Wire `AppContext.ai_store` and `ai_store_files_repo`

**Files:**
- Modify: `backend/app/context.py`
- Modify: `tests/integration/test_context.py`

- [ ] **Step 1: Update the existing test assertions**

In `tests/integration/test_context.py`, the second test (`test_context_exposes_archive_provider_when_external_initialized`) already stubs `GcsService` with `_StubGcs`. Extend `_StubGcs` so it has the attributes `GcsInputStore` needs at construction time:

```python
    class _StubGcs:
        def __init__(self, *args, **kwargs):
            self.bucket_name = "b"
            self._bucket = type("FakeBucket", (), {"exists": staticmethod(lambda: True)})()
```

Then after the existing `assert isinstance(ctx.archive, CatdvArchiveAdapter)` line, add:

```python
        from backend.app.archive.ai_stores.gcs.adapter import GcsInputStore

        assert isinstance(ctx.ai_store, GcsInputStore)
        assert ctx.ai_store.id == "gcs:b"
        # The old `ctx.gcs` attribute is gone.
        assert not hasattr(ctx, "gcs") or ctx.gcs is None
```

- [ ] **Step 2: Run to confirm failure**

```bash
.venv/bin/pytest tests/integration/test_context.py -v
```
Expected: FAIL — `ctx.ai_store` doesn't exist yet.

- [ ] **Step 3: Update `context.py`**

Modify `backend/app/context.py`:

1. Add imports at top:

```python
from backend.app.archive.ai_store import AIInputStore
from backend.app.archive.ai_stores.registry import build_ai_input_store
from backend.app.repositories.ai_store_files import AIStoreFilesRepo
```

2. Remove this import:

```python
from backend.app.repositories.gcs_files import GcsFilesRepo
```

3. In the dataclass, **replace**:

```python
    gcs_files_repo: GcsFilesRepo = field(default_factory=GcsFilesRepo)
```

with:

```python
    ai_store_files_repo: AIStoreFilesRepo = field(default_factory=AIStoreFilesRepo)
```

4. **Replace**:

```python
    catdv = None
    archive: ArchiveProvider | None = None
    gcs = None
    gemini = None
    proxy_resolver = None
```

with:

```python
    catdv = None
    archive: ArchiveProvider | None = None
    ai_store: AIInputStore | None = None
    gemini = None
    proxy_resolver = None
    _gcs_service = None   # low-level GcsService kept only as a wiring detail
```

5. Inside the `if init_external:` block, **replace**:

```python
            ctx.gcs = GcsService(settings.gcs_bucket_name)
```

with:

```python
            ctx._gcs_service = GcsService(settings.gcs_bucket_name)
            ctx.ai_store = build_ai_input_store(
                settings,
                gcs_service=ctx._gcs_service,
                files_repo=ctx.ai_store_files_repo,
                db_provider=lambda c=ctx: c.db,
            )
```

(The `db_provider` is a closure over the context so the adapter uses the live connection at call time.)

- [ ] **Step 4: Run the context test**

```bash
.venv/bin/pytest tests/integration/test_context.py -v
```
Expected: pass.

- [ ] **Step 5: Run full suite — note expected failures elsewhere**

```bash
.venv/bin/pytest -q
```
Expected: tests in `routes/jobs.py`, `services/annotator.py`, and `startup.py` paths will fail because they still reference `ctx.gcs` and `gcs_files_repo`. That's expected — we fix them in tasks 10–12.

- [ ] **Step 6: Commit**

```bash
git add backend/app/context.py tests/integration/test_context.py
git commit -m "feat(context): wire ctx.ai_store + ai_store_files_repo; drop ctx.gcs"
```

---

### Task 10: Switch `annotator` to use `ai_store`

**Files:**
- Modify: `backend/app/services/annotator.py`
- Modify: `tests/integration/test_annotator_worker.py`

- [ ] **Step 1: Rewrite the test's `FakeGcs` into a `FakeAIStore`**

In `tests/integration/test_annotator_worker.py`, **replace** the `FakeGcs` class with:

```python
class FakeAIStore:
    """Implements just enough of AIInputStore for the worker test."""

    id = "gcs:bucket"

    def __init__(self) -> None:
        self.uploads: list[tuple[int, Path]] = []

    async def ensure_uploaded(self, clip_key, local_path, mime):
        from datetime import datetime, timezone

        from backend.app.archive.ai_store_model import UploadedRef

        self.uploads.append((int(clip_key[1]), local_path))
        return UploadedRef(
            handle=f"gs://bucket/clips/{clip_key[1]}.mov",
            mime_type=mime,
            size_bytes=local_path.stat().st_size,
            sha256="fakesha",
            uploaded_at=datetime.now(timezone.utc),
            expires_at=None,
        )

    async def reference_for_gemini(self, ref):
        return {"file_data": {"file_uri": ref.handle, "mime_type": ref.mime_type}}

    async def status(self, clip_key):
        return None

    async def evict(self, clip_key):
        return None

    async def health(self):
        from backend.app.archive.ai_store_model import StoreHealth

        return StoreHealth(ok=True)
```

Update the import block at the top of the file to remove `from backend.app.repositories.gcs_files import GcsFilesRepo` and add:

```python
from backend.app.repositories.ai_store_files import AIStoreFilesRepo
```

Update both call sites of `run_job(...)` in the test file: replace

```python
        gcs=gcs,
        ...
        gcs_files_repo=GcsFilesRepo(),
```

with

```python
        ai_store=ai_store,
        ...
        ai_store_files_repo=AIStoreFilesRepo(),
```

And in each test body, replace the line that builds the gcs fake (`gcs = FakeGcs("bucket")`) with `ai_store = FakeAIStore()`.

The `FakeGeminiStructured.annotate` signature in the test changes from `(self, *, gcs_uri, mime, prompt, schema, model)` to `(self, *, file_ref, prompt, schema, model)`. Update both definitions.

- [ ] **Step 2: Run the worker test to confirm failures**

```bash
.venv/bin/pytest tests/integration/test_annotator_worker.py -v
```
Expected: FAIL — `run_job` doesn't accept `ai_store`/`ai_store_files_repo` keyword args yet.

- [ ] **Step 3: Refactor `backend/app/services/annotator.py`**

Update imports:

```python
from backend.app.archive.ai_store import AIInputStore
from backend.app.repositories.ai_store_files import AIStoreFilesRepo
```

Remove:

```python
from backend.app.repositories.gcs_files import GcsFilesRepo
```

In `run_job(...)`, change the parameter list. Replace:

```python
    gcs,
    gemini,
    event_bus: EventBus,
    gcs_files_repo: GcsFilesRepo,
```

with:

```python
    ai_store: AIInputStore,
    gemini,
    event_bus: EventBus,
    ai_store_files_repo: AIStoreFilesRepo,
```

And update the inner call to `_process_item(...)`: replace the two kwargs

```python
                gcs=gcs,
                ...
                gcs_files_repo=gcs_files_repo,
```

with

```python
                ai_store=ai_store,
                ...
                ai_store_files_repo=ai_store_files_repo,
```

In `_process_item(...)`, replace the parameter list

```python
    gcs,
    gemini,
    gcs_files_repo,
```

with

```python
    ai_store,
    gemini,
    ai_store_files_repo,
```

Then **replace** the entire upload/dedup block (lines 111–133 of the current file — from `await jobs_repo.update_item_status(db, item.id, "uploading")` through the end of the `else:` branch with `await gcs_files_repo.upsert(...)`) and the gemini call (currently passing `gcs_uri=gcs_uri, mime=mime`) with:

```python
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
        prompt=template.prompt,
        schema=template.output_schema,
        model=template.model,
    )
```

Then **delete** the now-unused imports and helpers: remove `import hashlib`, the `_sha256` helper at the bottom of the file, and the import of `GcsFilesRepo`. (The `_sha256` computation moves into the adapter.)

The `ai_store_files_repo` parameter is now part of the `run_job` signature but is no longer used directly inside the worker — that's fine: routes/jobs.py still passes it (the adapter consumes it via the `db_provider` closure). We keep the parameter in `run_job` because future PRs (workspaces, cache inspector) will want explicit access.

Actually no — drop the `ai_store_files_repo` param too if nothing inside `run_job` references it. The adapter has it through the context closure. Simpler. **Remove** the `ai_store_files_repo` parameter from both `run_job` and `_process_item`, and remove it from the kwargs in the test calls (Step 1 above included it; update the test to also drop that kwarg now).

(If you already wrote the test with that kwarg in Step 1, edit the test now to drop `ai_store_files_repo=AIStoreFilesRepo()` from both `run_job(...)` calls. Run the tests after this step to confirm cleanly.)

- [ ] **Step 4: Run worker tests, verify pass**

```bash
.venv/bin/pytest tests/integration/test_annotator_worker.py -v
```
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/annotator.py tests/integration/test_annotator_worker.py
git commit -m "refactor(annotator): consume AIInputStore via ai_store.ensure_uploaded + reference_for_gemini"
```

---

### Task 11: Switch `routes/jobs.py` and `startup.py` to use `ai_store`

**Files:**
- Modify: `backend/app/routes/jobs.py`
- Modify: `backend/app/startup.py`
- Modify: `tests/integration/test_startup_check.py`

- [ ] **Step 1: Update `routes/jobs.py`**

In `backend/app/routes/jobs.py`, replace the readiness check inside `create_job` from:

```python
    if body.auto_start and ctx.archive and ctx.gcs and ctx.gemini and ctx.proxy_resolver:
```

to:

```python
    if body.auto_start and ctx.archive and ctx.ai_store and ctx.gemini and ctx.proxy_resolver:
```

Inside `_run_in_bg`, replace the `run_job(...)` call. Change:

```python
            gcs=ctx.gcs,
            gemini=ctx.gemini,
            event_bus=ctx.event_bus,
            gcs_files_repo=ctx.gcs_files_repo,
```

to:

```python
            ai_store=ctx.ai_store,
            gemini=ctx.gemini,
            event_bus=ctx.event_bus,
```

(Dropping the `gcs_files_repo` kwarg entirely, matching Task 10.)

- [ ] **Step 2: Rewrite `tests/integration/test_startup_check.py`**

Replace the file's `FakeGcs`/`FakeBucket` classes with a `FakeAIStore` that exposes an async `health()` returning a `StoreHealth`. The full new file body (replacing existing classes and calls — keep `FakeCatdv` as-is):

```python
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from backend.app.archive.ai_store_model import StoreHealth
from backend.app.startup import StartupCheckResult, run_checks


class FakeCatdv:
    def __init__(self, ok: bool):
        self._ok = ok

    async def get_clip(self, clip_id):
        if not self._ok:
            raise RuntimeError("connection refused")
        return {"ID": clip_id, "name": "x"}


class FakeAIStore:
    def __init__(self, ok: bool, detail: str | None = None):
        self._ok = ok
        self._detail = detail

    async def health(self) -> StoreHealth:
        return StoreHealth(ok=self._ok, detail=self._detail)


@pytest.mark.asyncio
async def test_all_checks_pass():
    result = await run_checks(
        catdv=FakeCatdv(True),
        ai_store=FakeAIStore(True),
        proxy_resolver=MagicMock(path_for_clip_id=MagicMock()),
        catalog_id=881507,
        sample_clip_id=1,
        verify_proxy=False,
    )
    assert result.ok
    assert result.failures == []


@pytest.mark.asyncio
async def test_catdv_failure_is_reported():
    result = await run_checks(
        catdv=FakeCatdv(False),
        ai_store=FakeAIStore(True),
        proxy_resolver=MagicMock(),
        catalog_id=881507,
        sample_clip_id=1,
        verify_proxy=False,
    )
    assert not result.ok
    assert any("CatDV" in f for f in result.failures)


@pytest.mark.asyncio
async def test_ai_store_failure_is_reported():
    result = await run_checks(
        catdv=FakeCatdv(True),
        ai_store=FakeAIStore(False, detail="bucket not found: b"),
        proxy_resolver=MagicMock(),
        catalog_id=881507,
        sample_clip_id=1,
        verify_proxy=False,
    )
    assert not result.ok
    assert any("AI input store" in f for f in result.failures)
```

- [ ] **Step 3: Run startup tests to confirm failure**

```bash
.venv/bin/pytest tests/integration/test_startup_check.py -v
```
Expected: FAIL — `run_checks` still takes a `gcs` parameter, not `ai_store`.

- [ ] **Step 4: Update `backend/app/startup.py`**

Replace the whole file with:

```python
from dataclasses import dataclass, field


@dataclass
class StartupCheckResult:
    failures: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failures


async def run_checks(
    *,
    catdv,
    ai_store,
    proxy_resolver,
    catalog_id: int,
    sample_clip_id: int | None = None,
    verify_proxy: bool = False,
) -> StartupCheckResult:
    """Verify that external dependencies are reachable. Returns failures, never raises."""
    result = StartupCheckResult()

    try:
        if sample_clip_id is not None:
            await catdv.get_clip(sample_clip_id)
    except Exception as exc:  # noqa: BLE001
        result.failures.append(f"CatDV unreachable or sample clip missing: {exc}")

    try:
        health = await ai_store.health()
        if not health.ok:
            detail = health.detail or "unknown reason"
            result.failures.append(f"AI input store not healthy: {detail}")
    except Exception as exc:  # noqa: BLE001
        result.failures.append(f"AI input store check failed: {exc}")

    if verify_proxy and sample_clip_id is not None:
        try:
            await proxy_resolver.path_for_clip_id(sample_clip_id)
        except Exception as exc:  # noqa: BLE001
            result.failures.append(f"Proxy resolver failed for clip {sample_clip_id}: {exc}")

    return result
```

- [ ] **Step 5: Run startup tests, verify pass**

```bash
.venv/bin/pytest tests/integration/test_startup_check.py -v
```
Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
git add backend/app/routes/jobs.py backend/app/startup.py tests/integration/test_startup_check.py
git commit -m "refactor(routes,startup): consume ctx.ai_store; drop direct GCS coupling"
```

---

### Task 12: Delete `GcsFilesRepo`

**Files:**
- Delete: `backend/app/repositories/gcs_files.py`
- Delete: `tests/integration/test_gcs_files_repo.py`

- [ ] **Step 1: Confirm no remaining imports**

Run:
```bash
grep -rn "GcsFilesRepo\|repositories.gcs_files\|gcs_files_repo" \
  backend/ tests/ \
  --include="*.py"
```
Expected output: no matches. If any remain, fix them before deleting.

- [ ] **Step 2: Delete the files**

```bash
git rm backend/app/repositories/gcs_files.py tests/integration/test_gcs_files_repo.py
```

- [ ] **Step 3: Run the full test suite**

```bash
.venv/bin/pytest -q
```
Expected: all green.

- [ ] **Step 4: Commit**

```bash
git commit -m "chore(archive): remove GcsFilesRepo; superseded by AIStoreFilesRepo"
```

---

### Task 13: Sanity-check the full app shape

Programmatic-only — exercises the import graph and the lifespan wiring without bringing up real CatDV/GCS.

- [ ] **Step 1: Run the entire test suite**

```bash
.venv/bin/pytest -q
```
Expected: all green, zero collection warnings.

- [ ] **Step 2: Static check that no app code touches `GcsService` directly**

Run:
```bash
grep -rn "from backend.app.services.gcs" backend/ --include="*.py" \
  | grep -v "backend/app/archive/ai_stores/"  \
  | grep -v "backend/app/context.py"
```
Expected: empty. Only the adapter and the AppContext wire-up are allowed to import `GcsService`.

If the grep returns anything, fix the offending file: it should be using `ctx.ai_store` instead.

- [ ] **Step 3: Static check that no app code constructs `gs://` URIs directly**

Run:
```bash
grep -rn 'f"gs://\|"gs://' backend/app/services backend/app/routes \
  --include="*.py" | grep -v archive
```
Expected: empty.

- [ ] **Step 4: Static check no module imports `repositories.gcs_files`**

Run:
```bash
grep -rn "repositories.gcs_files" backend/ tests/ --include="*.py"
```
Expected: empty.

- [ ] **Step 5: Commit (nothing to commit; verification only)**

If everything is clean, no commit. If grep found anything, fix it inline, commit as `fix(archive): remove residual direct-GCS reference`.

---

### Task 14: Record the architectural decision

**Files:**
- Modify: `docs/decisions.md`

- [ ] **Step 1: Append a decision entry**

Append to `docs/decisions.md` (under existing entries, do not modify existing entries):

```markdown
## 2026-05-19: AIInputStore port distinct from ArchiveProvider

**Context:** Vertex AI Gemini needs media bytes available at a URI it can
read (today: GCS). The same clip's bytes can live on a CatDV server
(archive), on the annotator host's disk (proxy cache), and in a GCS bucket
(AI input). Conflating "where the archive is" and "where Gemini reads from"
would force a CatDV install and a filesystem-archive install to share the
same upload code, and would make adding the Gemini Files API a rewrite of
the annotator rather than a new adapter.

**Alternatives:** Merge AI upload into ArchiveProvider; rename
`GcsService` to a more abstract `MediaCdn` without a Protocol.

**Choice:** Introduce `AIInputStore` Protocol parallel to `ArchiveProvider`,
with adapter packages under `backend/app/archive/ai_stores/`. The GCS
adapter ships today; a Gemini Files API stub proves the Protocol shape.

**Why:** Two ports with one responsibility each beats one port with two
responsibilities. Switching the AI store is one adapter swap; switching
the archive is another; neither cascades into the worker.
```

- [ ] **Step 2: Commit**

```bash
git add docs/decisions.md
git commit -m "docs(decisions): record AIInputStore-vs-ArchiveProvider split"
```

---

### Task 15: Smoke-check the running app (manual)

This task verifies the app boots and the annotate→apply pipeline still works end-to-end after the refactor. The user runs this; no code change.

- [ ] **Step 1: Bring up the dev server**

```bash
./run.sh
```

Expected: server starts at `localhost:8765`, no traceback referring to `gcs_files`, `GcsFilesRepo`, or `ctx.gcs`.

- [ ] **Step 2: Sanity-check endpoints**

In a second shell:

```bash
curl -s http://localhost:8765/api/health
curl -s 'http://localhost:8765/api/catdv/clips?limit=2' | head -c 500
```

Expected: `{"status":"ok"}`, then a JSON page of clips. The behaviour is unchanged from PR 1 — the AI input store doesn't touch this path.

- [ ] **Step 3: Submit a small annotation job via the UI**

In the browser at `localhost:8765`:

1. Pick one clip.
2. Pick the default template.
3. Click "Annotate selected".
4. Wait for the job to reach `review_ready`.

Expected:
- Server logs show `ensure_uploaded` activity but no `upload_if_absent` line outside the adapter.
- `ai_store_files` table has exactly one row with `store_id='gcs:<bucket>'`.

Verify with:

```bash
sqlite3 data/app.db "SELECT store_id, catdv_clip_id, gcs_uri FROM ai_store_files LIMIT 5;"
```

Expected: one or more rows; `gcs_files` table no longer exists:

```bash
sqlite3 data/app.db ".tables" | grep -E "gcs_files|ai_store_files"
```

Expected: only `ai_store_files`.

- [ ] **Step 4: Apply a review item**

Open the clip in the Review pane; accept one suggestion; click Apply. Confirm the CatDV PUT succeeds (server logs show `write_log` row inserted with `status='ok'`).

- [ ] **Step 5: Tear down**

Ctrl-C the server.

- [ ] **Step 6: Commit nothing**

Verification only. No commit.

---

## Self-review checklist (run after writing the plan, fix inline)

1. **Spec coverage** — Does this plan implement every bullet of spec §13 PR 2?
   - [x] `archive/AIInputStore` Protocol → Task 2
   - [x] `ai_stores/gcs/` adapter wrapping current `services/gcs.py` → Task 5
   - [x] `AppContext.ai_store` wired at startup → Task 9
   - [x] `annotator` calls `ai_store.ensure_uploaded()` + `reference_for_gemini()`, no `gs://` or `GcsService` references → Task 10 + Task 13 grep checks
   - [x] Migration: rename `gcs_files` → `ai_store_files`; add `store_id`; backfill `'gcs:<bucket>'` → Task 3
   - [x] `GeminiFilesInputStore` is a `NotImplementedError` stub that proves the port shape compiles → Task 6
   - [x] No user-visible change → annotate/apply flow unchanged (Task 15 manual check)

2. **Placeholder scan** — searched for "TBD", "TODO", "fill in", "appropriate error handling": none. Every step shows exact code and exact commands.

3. **Type consistency**
   - `AIInputStore.ensure_uploaded(clip_key: ClipKey, ...)` — `ClipKey = tuple[str, str]`. Annotator builds it as `("catdv", str(item.catdv_clip_id))`. GcsInputStore casts `int(clip_key[1])` for the int-keyed registry. Consistent.
   - `UploadedRef.handle: str` — GCS adapter sets it to `gs://...`; Gemini Files stub sets it to a Files API handle. Both Protocol-conformant.
   - `reference_for_gemini(ref) -> dict` — GCS returns `{"file_data": {"file_uri": ..., "mime_type": ...}}`; Gemini Files stub returns `{"file_data": {"file_id": ...}}`. Both shapes accepted by the Gemini SDK's `generate_content()`.
   - `AIStoreFilesRepo` methods take `store_id: str, clip_id: int` consistently (keyword-only). Adapter calls match. Test calls match.
   - `gemini.annotate(file_ref=...)` — adapter produces dict, annotator passes it, gemini puts it as `contents[1]`. Consistent.
   - `StoreHealth(ok: bool, detail: str | None)` — `health()` returns it in both adapters; `startup.run_checks` consumes both fields. Consistent.

4. **Scope check** — One subsystem (the AI input store boundary). Each task is independently shippable and testable; the suite stays green across tasks 1–7 (additive); tasks 8–11 are co-dependent (the gemini signature change ripples into the annotator and tests in one logical step but spread across three commits for review granularity). Tasks 12–15 are clean-up + verification.

5. **Migration safety** — The `gcs_files` → `ai_store_files` migration backfills `store_id` by parsing the existing `gs://` URI in SQL. Edge case: if a row has `gcs_uri` that doesn't start with `gs://bucket/...`, `instr(substr(gcs_uri, 6), '/')` returns 0 and `substr(..., 0)` returns an empty string, yielding `store_id='gcs:'`. The seed test in Task 3 covers the happy path; rows produced by `GcsService.upload_if_absent` always match the expected shape. Acceptable.

---

## After this PR ships

PR 3 (ID columns: `provider_id`/`provider_clip_id` on every clip-keyed table, plus `clip_cache` + `field_def_cache`) is the next plan to write. With PR 2 done, the registry-key shape change in PR 3 only touches `ai_store_files.catdv_clip_id` (plus the other tables); none of the AI input store code paths have to change because `ClipKey` already flows through the adapter. Start the next plan only after PR 2 is merged.
