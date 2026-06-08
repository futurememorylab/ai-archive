# Prompt Studio Uploaded Clips (Spec B) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Uploaded tab real — upload a web-safe video, store it locally, list it, thumbnail it, play it, and run a Gemini prompt on it — by giving uploaded clips a synthetic `clip_id` that flows through the existing proxy / thumbnail / AI-store / run pipeline.

**Architecture:** Uploaded clips get a positive synthetic `clip_id = 1_000_000_000 + uploaded_clip.id`, recorded in a new `uploaded_clip` table. Ingest writes bytes to `data_dir/cache/uploads/`, pre-seeds a `proxy_cache` row, and stores a client-captured poster JPEG into the thumb cache — so the existing serving routes (`/media/{clip_id}`, `/media/{clip_id}/thumb`), the `ai_store` (`ClipKey=("uploaded", id)`), and the run engine work unchanged. Source-awareness is confined to ~4 guard points keyed on `is_uploaded(clip_id)`.

**Tech Stack:** FastAPI, aiosqlite (SQLite + migrations), Jinja2 partials, Alpine.js stores + HTMX, pytest (Python-only repo — JS is verified by source-scan tests + manual flows).

**Spec:** `docs/specs/2026-06-08-prompt-studio-uploads-spec-b-design.md`

---

## File Structure

**Create:**
- `backend/app/uploaded_ids.py` — synthetic-id constant + helpers (`UPLOAD_ID_BASE`, `is_uploaded`, `to_clip_id`, `to_pk`).
- `backend/migrations/0018_uploaded_clips.sql` — `uploaded_clip` table.
- `backend/app/repositories/uploaded_clips.py` — `UploadedClipsRepo`.
- `tests/unit/test_uploaded_ids.py`, `tests/unit/test_migration_0018_uploaded_clips.py`, `tests/unit/test_uploaded_clips_repo.py`, `tests/unit/test_upload_aware_resolver.py`, `tests/unit/test_thumbnail_uploaded_guard.py`, `tests/unit/test_resolve_clip_meta.py`, `tests/unit/test_uploaded_clip_card_render.py`, `tests/unit/test_studio_uploads_js.py`
- `tests/integration/test_studio_uploads_api.py`, `tests/integration/test_studio_uploaded_page.py`

**Modify:**
- `backend/app/settings.py` — upload limits.
- `backend/app/repositories/studio_sets.py` — `get_or_create_default_uploaded_set`.
- `backend/app/services/proxy_resolver.py` — `UploadAwareResolver` wrapper + wrap in `build_resolver`.
- `backend/app/services/thumbnail_service.py` — uploaded guard in `get_or_fetch`.
- `backend/app/context.py` — register `uploaded_clips_repo`; fix prefetcher `isinstance` check.
- `backend/app/services/annotator.py` — `_resolve_clip_meta` helper + `_process_item`/`run_job` thread `uploaded_clips_repo`.
- `backend/app/routes/studio.py` — `POST /api/studio/uploads`; `_run_in_bg` passes `uploaded_clips_repo`.
- `backend/app/routes/pages/studio.py` — `_studio_set` metadata branch for uploaded clips.
- `backend/app/templates/pages/_studio_set_clip_card.html` — uploaded branch (filename, no id tag, `<img>` poster + placeholder).
- `backend/app/templates/pages/_studio_set_card.html` — rename affordance.
- `backend/app/templates/pages/_studio_nav.html` — uploaded badge count + real body.
- `backend/app/templates/pages/_studio_uploaded_stub.html` — becomes the dropzone + set list host.
- `backend/app/static/studio.js` — `switchSource('uploaded')` loads the real list; `studioSets.renameSet`; new `uploadClips` form logic with poster capture.
- `backend/app/static/app.css` — dropzone, rename, uploaded-card placeholder styles.

---

## Task 1: Synthetic-id helpers

**Files:**
- Create: `backend/app/uploaded_ids.py`
- Test: `tests/unit/test_uploaded_ids.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_uploaded_ids.py
from backend.app.uploaded_ids import (
    UPLOAD_ID_BASE,
    is_uploaded,
    to_clip_id,
    to_pk,
)


def test_base_is_one_billion():
    assert UPLOAD_ID_BASE == 1_000_000_000


def test_roundtrip_pk_to_clip_id_and_back():
    assert to_clip_id(1) == 1_000_000_001
    assert to_pk(1_000_000_001) == 1


def test_is_uploaded_predicate():
    assert is_uploaded(1_000_000_000) is True
    assert is_uploaded(1_000_000_999) is True
    assert is_uploaded(999_999_999) is False   # plausible CatDV id
    assert is_uploaded(42) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_uploaded_ids.py -q`
Expected: FAIL — `ModuleNotFoundError: backend.app.uploaded_ids`

- [ ] **Step 3: Write minimal implementation**

```python
# backend/app/uploaded_ids.py
"""Synthetic clip-id scheme for uploaded studio clips.

Uploaded clips have no CatDV id but must flow through the source-blind
integer `clip_id` pipeline (set membership, runs, proxy/thumbnail
resolution, the `/media/{clip_id}` routes). We give each uploaded clip a
positive synthetic id `UPLOAD_ID_BASE + uploaded_clip.id`. Positive (not
negative) because FastAPI's `int` path converter regex is `[0-9]+` —
negative ids would 404 `/media/-5`. The range is disjoint from this
deployment's CatDV ids, so `is_uploaded` is an O(1) predicate.
"""

UPLOAD_ID_BASE = 1_000_000_000


def is_uploaded(clip_id: int) -> bool:
    return clip_id >= UPLOAD_ID_BASE


def to_clip_id(pk: int) -> int:
    return UPLOAD_ID_BASE + pk


def to_pk(clip_id: int) -> int:
    return clip_id - UPLOAD_ID_BASE
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/test_uploaded_ids.py -q`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/app/uploaded_ids.py tests/unit/test_uploaded_ids.py
git commit -m "feat(studio): synthetic uploaded-clip id helpers"
```

---

## Task 2: Migration 0018 — `uploaded_clip` table

**Files:**
- Create: `backend/migrations/0018_uploaded_clips.sql`
- Test: `tests/unit/test_migration_0018_uploaded_clips.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_migration_0018_uploaded_clips.py
"""0018 adds the uploaded_clip table without touching existing tables."""

from pathlib import Path

import pytest

from backend.app.db import open_db
from backend.app.migrations_runner import apply_migrations


@pytest.fixture
async def conn(tmp_path: Path):
    cm = open_db(tmp_path / "t.db")
    c = await cm.__aenter__()
    yield c
    await cm.__aexit__(None, None, None)


@pytest.mark.asyncio
async def test_uploaded_clip_table_columns(conn):
    await apply_migrations(conn, Path("backend/migrations"))
    cur = await conn.execute("PRAGMA table_info(uploaded_clip)")
    cols = {row[1] for row in await cur.fetchall()}
    assert {
        "id", "original_filename", "stored_filename", "mime",
        "size_bytes", "duration_secs", "width", "height", "created_at",
    } <= cols


@pytest.mark.asyncio
async def test_autoincrement_never_reuses_ids(conn):
    await apply_migrations(conn, Path("backend/migrations"))
    await conn.execute(
        "INSERT INTO uploaded_clip(original_filename, stored_filename, mime, "
        "size_bytes, created_at) VALUES ('a.mp4','x','video/mp4',1,'t')"
    )
    await conn.execute("DELETE FROM uploaded_clip")
    await conn.execute(
        "INSERT INTO uploaded_clip(original_filename, stored_filename, mime, "
        "size_bytes, created_at) VALUES ('b.mp4','y','video/mp4',1,'t')"
    )
    await conn.commit()
    cur = await conn.execute("SELECT id FROM uploaded_clip")
    # AUTOINCREMENT → second row gets id 2, not a reused 1.
    assert (await cur.fetchone())[0] == 2


@pytest.mark.asyncio
async def test_studio_set_untouched(conn):
    await apply_migrations(conn, Path("backend/migrations"))
    cur = await conn.execute("PRAGMA table_info(studio_set)")
    assert {row[1] for row in await cur.fetchall()} >= {"id", "name", "source"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_migration_0018_uploaded_clips.py -q`
Expected: FAIL — `no such table: uploaded_clip`

- [ ] **Step 3: Write the migration**

```sql
-- backend/migrations/0018_uploaded_clips.sql
-- 0018: uploaded studio clips. A row here is a user-uploaded video that
-- participates in the studio pipeline via a synthetic clip_id
-- (UPLOAD_ID_BASE + id; see backend/app/uploaded_ids.py). Set
-- membership lives in studio_set_clip exactly as for archive clips; this
-- table holds only the per-upload metadata the navigator + run path need.
-- AUTOINCREMENT guarantees ids are never reused, so a deleted upload's
-- synthetic clip_id can't later collide with a different file.
CREATE TABLE uploaded_clip (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  original_filename TEXT    NOT NULL,
  stored_filename   TEXT    NOT NULL,
  mime              TEXT    NOT NULL,
  size_bytes        INTEGER NOT NULL,
  duration_secs     REAL,
  width             INTEGER,
  height            INTEGER,
  created_at        TEXT    NOT NULL
);
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/test_migration_0018_uploaded_clips.py -q`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/migrations/0018_uploaded_clips.sql tests/unit/test_migration_0018_uploaded_clips.py
git commit -m "feat(studio): migration 0018 — uploaded_clip table"
```

---

## Task 3: `UploadedClipsRepo`

**Files:**
- Create: `backend/app/repositories/uploaded_clips.py`
- Test: `tests/unit/test_uploaded_clips_repo.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_uploaded_clips_repo.py
from pathlib import Path

import pytest

from backend.app.db import open_db
from backend.app.migrations_runner import apply_migrations
from backend.app.repositories.uploaded_clips import UploadedClipsRepo
from backend.app.uploaded_ids import to_clip_id


@pytest.fixture
async def conn(tmp_path: Path):
    cm = open_db(tmp_path / "t.db")
    c = await cm.__aenter__()
    await apply_migrations(c, Path("backend/migrations"))
    yield c
    await cm.__aexit__(None, None, None)


@pytest.mark.asyncio
async def test_create_sets_stored_filename_from_clip_id(conn):
    repo = UploadedClipsRepo()
    pk = await repo.create(
        conn, original_filename="My Clip.mp4", mime="video/mp4",
        size_bytes=123, ext=".mp4", duration_secs=12.5, width=1920, height=1080,
    )
    clip_id = to_clip_id(pk)
    row = await repo.get(conn, clip_id)
    assert row is not None
    assert row["original_filename"] == "My Clip.mp4"
    assert row["stored_filename"] == f"{clip_id}.mp4"
    assert row["mime"] == "video/mp4"
    assert row["duration_secs"] == 12.5


@pytest.mark.asyncio
async def test_get_missing_returns_none(conn):
    repo = UploadedClipsRepo()
    assert await repo.get(conn, 1_000_000_999) is None


@pytest.mark.asyncio
async def test_get_many_keyed_by_clip_id(conn):
    repo = UploadedClipsRepo()
    pk1 = await repo.create(conn, original_filename="a.mp4", mime="video/mp4",
                            size_bytes=1, ext=".mp4")
    pk2 = await repo.create(conn, original_filename="b.webm", mime="video/webm",
                            size_bytes=2, ext=".webm")
    got = await repo.get_many(conn, [to_clip_id(pk1), to_clip_id(pk2), 1_000_009_999])
    assert set(got) == {to_clip_id(pk1), to_clip_id(pk2)}
    assert got[to_clip_id(pk2)]["original_filename"] == "b.webm"


@pytest.mark.asyncio
async def test_delete(conn):
    repo = UploadedClipsRepo()
    pk = await repo.create(conn, original_filename="a.mp4", mime="video/mp4",
                           size_bytes=1, ext=".mp4")
    cid = to_clip_id(pk)
    await repo.delete(conn, cid)
    assert await repo.get(conn, cid) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_uploaded_clips_repo.py -q`
Expected: FAIL — `ModuleNotFoundError: backend.app.repositories.uploaded_clips`

- [ ] **Step 3: Write minimal implementation**

```python
# backend/app/repositories/uploaded_clips.py
"""UploadedClipsRepo — per-upload metadata for studio uploaded clips.

Keyed externally by the synthetic `clip_id` (UPLOAD_ID_BASE + row id);
internally the table PK is the small positive `id`. `create` owns the
identity: it inserts, derives `stored_filename = f"{clip_id}{ext}"`, and
returns the PK so the caller can compute the clip_id via uploaded_ids.
"""

from datetime import UTC, datetime
from typing import Any

import aiosqlite

from backend.app.repositories._batch import chunked_in_clause
from backend.app.uploaded_ids import to_clip_id, to_pk

_COLS = (
    "id", "original_filename", "stored_filename", "mime",
    "size_bytes", "duration_secs", "width", "height", "created_at",
)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _row_to_dict(row: tuple) -> dict[str, Any]:
    d = dict(zip(_COLS, row, strict=True))
    d["clip_id"] = to_clip_id(int(d["id"]))
    return d


class UploadedClipsRepo:
    async def create(
        self,
        conn: aiosqlite.Connection,
        *,
        original_filename: str,
        mime: str,
        size_bytes: int,
        ext: str,
        duration_secs: float | None = None,
        width: int | None = None,
        height: int | None = None,
    ) -> int:
        cur = await conn.execute(
            "INSERT INTO uploaded_clip(original_filename, stored_filename, mime, "
            "size_bytes, duration_secs, width, height, created_at) "
            "VALUES (?, '', ?, ?, ?, ?, ?, ?)",
            (original_filename, mime, size_bytes, duration_secs, width, height, _now_iso()),
        )
        pk = cur.lastrowid
        assert pk is not None
        stored = f"{to_clip_id(pk)}{ext}"
        await conn.execute(
            "UPDATE uploaded_clip SET stored_filename = ? WHERE id = ?", (stored, pk)
        )
        await conn.commit()
        return pk

    async def get(self, conn: aiosqlite.Connection, clip_id: int) -> dict[str, Any] | None:
        cur = await conn.execute(
            f"SELECT {', '.join(_COLS)} FROM uploaded_clip WHERE id = ?",
            (to_pk(clip_id),),
        )
        row = await cur.fetchone()
        return _row_to_dict(row) if row is not None else None

    async def get_many(
        self, conn: aiosqlite.Connection, clip_ids: list[int]
    ) -> dict[int, dict[str, Any]]:
        out: dict[int, dict[str, Any]] = {}
        pks = [(to_pk(cid),) for cid in clip_ids]
        for fragment, params in chunked_in_clause(pks):
            cur = await conn.execute(
                f"SELECT {', '.join(_COLS)} FROM uploaded_clip WHERE id IN ({fragment})",
                params,
            )
            for row in await cur.fetchall():
                d = _row_to_dict(row)
                out[d["clip_id"]] = d
        return out

    async def delete(self, conn: aiosqlite.Connection, clip_id: int) -> None:
        await conn.execute("DELETE FROM uploaded_clip WHERE id = ?", (to_pk(clip_id),))
        await conn.commit()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/test_uploaded_clips_repo.py -q`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/app/repositories/uploaded_clips.py tests/unit/test_uploaded_clips_repo.py
git commit -m "feat(studio): UploadedClipsRepo (create/get/get_many/delete)"
```

---

## Task 4: Register `uploaded_clips_repo` on the context

`CoreCtx` field + the matching `LiveCtx` delegator property. The drift guard `tests/unit/test_context_delegation.py` requires every `CoreCtx` repo field to have a `LiveCtx` accessor.

**Files:**
- Modify: `backend/app/context.py:101` (add field) and `backend/app/context.py:296` (add delegator)
- Test: `tests/unit/test_context_delegation.py` (existing — must stay green)

- [ ] **Step 1: Run the existing guard to confirm baseline**

Run: `.venv/bin/python -m pytest tests/unit/test_context_delegation.py -q`
Expected: PASS

- [ ] **Step 2: Add the import and CoreCtx field**

In `backend/app/context.py`, add the import near the other repo imports (after line 62, `StudioSetsRepo` import):

```python
from backend.app.repositories.uploaded_clips import UploadedClipsRepo
```

Add the field in `CoreCtx` immediately after the `studio_sets_repo` field (line 101):

```python
    uploaded_clips_repo: UploadedClipsRepo = field(default_factory=UploadedClipsRepo)
```

- [ ] **Step 3: Add the LiveCtx delegator**

In `backend/app/context.py`, immediately after the `studio_sets_repo` property (line 296), add:

```python
    @property
    def uploaded_clips_repo(self) -> UploadedClipsRepo:
        return self.core.uploaded_clips_repo
```

- [ ] **Step 4: Run the guard + a smoke import**

Run: `.venv/bin/python -m pytest tests/unit/test_context_delegation.py tests/unit/test_context_split.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/context.py
git commit -m "feat(studio): register uploaded_clips_repo on Core/Live ctx"
```

---

## Task 5: `UploadAwareResolver` — serve uploaded proxies, never hit CatDV

The wrapper intercepts uploaded `clip_id`s: it serves the pre-seeded local file from `proxy_cache` and raises `ProxyNotFound` on a miss — never delegating to the inner resolver (which would CatDV-download or `get_clip`). It wraps only the `rest` / `filesystem` resolvers; `cache-only` (`LocalCacheOnlyResolver`) already has the exact cache-only semantics, so it stays unwrapped — preserving the `isinstance(..., LocalCacheOnlyResolver)` prefetcher gate.

**Files:**
- Modify: `backend/app/services/proxy_resolver.py`
- Modify: `backend/app/context.py:681` (prefetcher `isinstance` check)
- Test: `tests/unit/test_upload_aware_resolver.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_upload_aware_resolver.py
from pathlib import Path

import pytest

from backend.app.db import open_db
from backend.app.migrations_runner import apply_migrations
from backend.app.repositories.proxy_cache import ProxyCacheRepo
from backend.app.services.proxy_resolver import ProxyNotFound, UploadAwareResolver
from backend.app.uploaded_ids import to_clip_id


class _Inner:
    is_host_local = False

    def __init__(self):
        self.calls = []

    async def path_for_clip_id(self, clip_id: int) -> Path:
        self.calls.append(clip_id)
        return Path("/archive/served.mov")

    def is_managed(self, path: Path) -> bool:
        return True


@pytest.fixture
async def conn(tmp_path):
    cm = open_db(tmp_path / "t.db")
    c = await cm.__aenter__()
    await apply_migrations(c, Path("backend/migrations"))
    yield c
    await cm.__aexit__(None, None, None)


@pytest.mark.asyncio
async def test_archive_id_delegates_to_inner(conn):
    inner = _Inner()
    r = UploadAwareResolver(inner, repo=ProxyCacheRepo(), db_provider=lambda: conn)
    assert await r.path_for_clip_id(42) == Path("/archive/served.mov")
    assert inner.calls == [42]


@pytest.mark.asyncio
async def test_uploaded_hit_serves_local_file_without_inner(conn, tmp_path):
    f = tmp_path / "up.mp4"
    f.write_bytes(b"video-bytes")
    clip_id = to_clip_id(1)
    repo = ProxyCacheRepo()
    await repo.record(conn, clip_id=clip_id, file_path=str(f), size_bytes=11,
                      etag=None, provider_id="uploaded", provider_clip_id=str(clip_id))
    inner = _Inner()
    r = UploadAwareResolver(inner, repo=repo, db_provider=lambda: conn)
    assert await r.path_for_clip_id(clip_id) == f
    assert inner.calls == []  # never touched the CatDV path


@pytest.mark.asyncio
async def test_uploaded_miss_raises_without_inner(conn):
    inner = _Inner()
    r = UploadAwareResolver(inner, repo=ProxyCacheRepo(), db_provider=lambda: conn)
    with pytest.raises(ProxyNotFound):
        await r.path_for_clip_id(to_clip_id(999))
    assert inner.calls == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_upload_aware_resolver.py -q`
Expected: FAIL — `ImportError: cannot import name 'UploadAwareResolver'`

- [ ] **Step 3: Add the wrapper class + wrap in `build_resolver`**

In `backend/app/services/proxy_resolver.py`, add the import at the top (after line 13):

```python
from backend.app.uploaded_ids import is_uploaded
```

Add the class after `LocalCacheOnlyResolver` (after line 209):

```python
class UploadAwareResolver:
    """Wraps an inner resolver, serving uploaded clips from the local
    proxy cache and never delegating uploaded ids to the inner (which
    would CatDV-download or get_clip). Archive ids pass straight through.
    """

    def __init__(
        self,
        inner: ProxyResolver,
        *,
        repo: ProxyCacheRepo,
        db_provider: Callable[[], aiosqlite.Connection],
    ) -> None:
        self._inner = inner
        self._repo = repo
        self._db_provider = db_provider
        self.is_host_local = getattr(inner, "is_host_local", False)

    @property
    def inner(self) -> ProxyResolver:
        return self._inner

    async def path_for_clip_id(self, clip_id: int) -> Path:
        if not is_uploaded(clip_id):
            return await self._inner.path_for_clip_id(clip_id)
        row = await self._repo.get(self._db_provider(), clip_id)
        if row is None:
            raise ProxyNotFound(f"uploaded clip {clip_id} not in local cache")
        p = Path(row["file_path"])
        if not p.exists() or p.stat().st_size == 0:  # sync-io-ok: pre-existing pattern, tier-4 async-io pass
            raise ProxyNotFound(f"uploaded clip {clip_id} cache row present but file missing: {p}")
        return p

    def is_managed(self, path: Path) -> bool:
        return self._inner.is_managed(path)
```

In `build_resolver`, wrap the `rest` and `filesystem` results. Change the `rest` branch (lines 230-239) so the constructed `RestProxyResolver` is wrapped before return:

```python
    if source == "rest":
        if cache_dir is None or catdv_client is None:
            raise ValueError("rest source requires catdv_client and cache_dir")
        inner = RestProxyResolver(
            catdv=catdv_client,
            cache_dir=cache_dir,
            proxy_cache_repo=proxy_cache_repo,
            db_provider=db_provider,
            archive=archive,
        )
        if proxy_cache_repo is not None and db_provider is not None:
            return UploadAwareResolver(inner, repo=proxy_cache_repo, db_provider=db_provider)
        return inner
    if source == "filesystem":
        if archive is None or media_store_map is None:
            raise ValueError("filesystem source requires archive provider and media_store_map")
        inner = FilesystemProxyResolver(archive=archive, media_store_map=media_store_map)
        if proxy_cache_repo is not None and db_provider is not None:
            return UploadAwareResolver(inner, repo=proxy_cache_repo, db_provider=db_provider)
        return inner
```

- [ ] **Step 4: Fix the prefetcher `isinstance` gate in context.py**

In `backend/app/context.py`, the media-prefetcher gate (line 681) must look through the wrapper. Change:

```python
    if arch.proxy_resolver is not None and not isinstance(
        arch.proxy_resolver, LocalCacheOnlyResolver
    ):
```

to:

```python
    _inner_resolver = getattr(arch.proxy_resolver, "inner", arch.proxy_resolver)
    if arch.proxy_resolver is not None and not isinstance(
        _inner_resolver, LocalCacheOnlyResolver
    ):
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_upload_aware_resolver.py tests/unit/test_context_split.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/proxy_resolver.py backend/app/context.py tests/unit/test_upload_aware_resolver.py
git commit -m "feat(studio): UploadAwareResolver — serve uploaded proxies, never CatDV"
```

---

## Task 6: Thumbnail-service uploaded guard

For uploaded clips, the poster is pre-stored at `path_for(clip_id)`. On a cache hit `get_or_fetch` already returns it; the guard ensures a *miss* returns `None` (→ placeholder) without ever calling `archive.get_clip` / CatDV.

**Files:**
- Modify: `backend/app/services/thumbnail_service.py:59-67`
- Test: `tests/unit/test_thumbnail_uploaded_guard.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_thumbnail_uploaded_guard.py
import pytest

from backend.app.services.thumbnail_service import ThumbnailService
from backend.app.uploaded_ids import to_clip_id


class _ExplodingArchive:
    async def get_clip(self, clip_id):
        raise AssertionError("archive.get_clip must NOT be called for uploaded clips")


class _ExplodingCatdv:
    async def download_thumbnail(self, *a, **k):
        raise AssertionError("download_thumbnail must NOT be called for uploaded clips")


@pytest.mark.asyncio
async def test_uploaded_hit_returns_poster(tmp_path):
    svc = ThumbnailService(cache_dir=tmp_path, archive=_ExplodingArchive(),
                           catdv=_ExplodingCatdv(), is_online_provider=lambda: True)
    cid = to_clip_id(1)
    poster = svc.path_for(cid)
    poster.write_bytes(b"jpeg")
    assert await svc.get_or_fetch(cid) == poster


@pytest.mark.asyncio
async def test_uploaded_miss_returns_none_without_network(tmp_path):
    svc = ThumbnailService(cache_dir=tmp_path, archive=_ExplodingArchive(),
                           catdv=_ExplodingCatdv(), is_online_provider=lambda: True)
    assert await svc.get_or_fetch(to_clip_id(2)) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_thumbnail_uploaded_guard.py -q`
Expected: FAIL — `AssertionError: archive.get_clip must NOT be called for uploaded clips`

- [ ] **Step 3: Add the guard**

In `backend/app/services/thumbnail_service.py`, add the import after line 18:

```python
from backend.app.uploaded_ids import is_uploaded
```

In `get_or_fetch`, insert the guard right after the cache-hit return (after line 62, before `if self._catdv is None:`):

```python
        if is_uploaded(clip_id):
            # Uploaded posters are pre-stored at path_for(clip_id) during
            # ingest. A miss is terminal — render the placeholder, never
            # consult CatDV (uploaded clips have no archive record).
            return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/test_thumbnail_uploaded_guard.py -q`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/thumbnail_service.py tests/unit/test_thumbnail_uploaded_guard.py
git commit -m "feat(studio): thumbnail service skips CatDV for uploaded clips"
```

---

## Task 7: Settings — upload limits

**Files:**
- Modify: `backend/app/settings.py:43`
- Test: `tests/unit/test_settings_upload.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_settings_upload.py
import os

import pytest


def _mk(monkeypatch):
    monkeypatch.setenv("CATDV_BASE_URL", "http://x")
    monkeypatch.setenv("CATDV_CATALOG_ID", "1")
    monkeypatch.setenv("GCP_PROJECT_ID", "p")
    monkeypatch.setenv("GCS_BUCKET_NAME", "b")
    from backend.app.settings import Settings
    return Settings()


def test_upload_defaults(monkeypatch):
    s = _mk(monkeypatch)
    assert s.studio_upload_max_mb == 500
    assert "video/mp4" in s.studio_upload_allowed_mimes
    assert "video/webm" in s.studio_upload_allowed_mimes
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_settings_upload.py -q`
Expected: FAIL — `AttributeError: 'Settings' object has no attribute 'studio_upload_max_mb'`

- [ ] **Step 3: Add the settings**

In `backend/app/settings.py`, after the `clip_list_cache_ttl_minutes` line (line 42), add:

```python

    # Prompt Studio uploads (Spec B). Web-safe only; no server-side
    # transcode, so the allowlist is browser-playable container/codecs.
    studio_upload_max_mb: int = 500
    studio_upload_allowed_mimes: str = "video/mp4,video/webm"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/test_settings_upload.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/settings.py tests/unit/test_settings_upload.py
git commit -m "feat(studio): upload size + format-allowlist settings"
```

---

## Task 8: Default uploaded set (get-or-create)

**Files:**
- Modify: `backend/app/repositories/studio_sets.py`
- Test: `tests/unit/test_studio_sets_repo.py` (append)

- [ ] **Step 1: Write the failing test (append to the existing repo test)**

```python
# tests/unit/test_studio_sets_repo.py  (append)
from backend.app.repositories.studio_sets import DEFAULT_UPLOADED_SET_NAME


@pytest.mark.asyncio
async def test_get_or_create_default_uploaded_set_is_idempotent(conn):
    repo = StudioSetsRepo()
    a = await repo.get_or_create_default_uploaded_set(conn)
    b = await repo.get_or_create_default_uploaded_set(conn)
    assert a == b
    sets = await repo.list_sets_with_counts(conn, source="uploaded")
    assert [s["name"] for s in sets] == [DEFAULT_UPLOADED_SET_NAME]
```

> Note: confirm the existing `conn` fixture and `StudioSetsRepo`/`pytest` imports are already present at the top of `tests/unit/test_studio_sets_repo.py` (they are — the file tests the same repo). Only add the import line and the test function.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_studio_sets_repo.py::test_get_or_create_default_uploaded_set_is_idempotent -q`
Expected: FAIL — `ImportError: cannot import name 'DEFAULT_UPLOADED_SET_NAME'`

- [ ] **Step 3: Add the constant + method**

In `backend/app/repositories/studio_sets.py`, add the constant after the imports (after line 12, before `def _now_iso`):

```python
DEFAULT_UPLOADED_SET_NAME = "Uploads"
```

Add the method to `StudioSetsRepo` (after `create_set`):

```python
    async def get_or_create_default_uploaded_set(
        self, conn: aiosqlite.Connection
    ) -> int:
        """Return the id of the well-known uploaded 'Uploads' set, creating
        it on first use. Lets a user drop a file before making a set."""
        cur = await conn.execute(
            "SELECT id FROM studio_set WHERE source='uploaded' AND name=? LIMIT 1",
            (DEFAULT_UPLOADED_SET_NAME,),
        )
        row = await cur.fetchone()
        if row is not None:
            return int(row[0])
        return await self.create_set(
            conn, name=DEFAULT_UPLOADED_SET_NAME, source="uploaded"
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/test_studio_sets_repo.py -q`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
git add backend/app/repositories/studio_sets.py tests/unit/test_studio_sets_repo.py
git commit -m "feat(studio): get-or-create default 'Uploads' set"
```

---

## Task 9: `POST /api/studio/uploads` ingest route

**Files:**
- Modify: `backend/app/routes/studio.py`
- Test: `tests/integration/test_studio_uploads_api.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_studio_uploads_api.py
"""Ingest route: store + index + thumbnail + set membership for uploads."""

import importlib

import pytest
from fastapi.testclient import TestClient

from backend.app.uploaded_ids import is_uploaded


def _make_app(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.delenv("CATDV_PASSWORD", raising=False)
    from backend.app import main as main_mod
    importlib.reload(main_mod)
    return main_mod.app, tmp_path


@pytest.fixture
def ctx(monkeypatch, tmp_path):
    app, data_dir = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as c:
        yield c, data_dir


def test_upload_creates_clip_and_membership(ctx):
    client, data_dir = ctx
    r = client.post(
        "/api/studio/uploads",
        files={
            "file": ("My Clip.mp4", b"fake-mp4-bytes", "video/mp4"),
            "poster": ("p.jpg", b"jpeg-bytes", "image/jpeg"),
        },
        data={"duration_secs": "12.5"},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    clip_id = body["clip_id"]
    set_id = body["set_id"]
    assert is_uploaded(clip_id)

    # File on disk + thumbnail stored.
    assert (data_dir / "cache" / "uploads" / f"{clip_id}.mp4").read_bytes() == b"fake-mp4-bytes"
    assert (data_dir / "cache" / "thumbs" / f"{clip_id}.jpg").read_bytes() == b"jpeg-bytes"

    # Membership in an uploaded set + landed in the default 'Uploads' set.
    clips = client.get(f"/api/studio/sets/{set_id}/clips").json()
    assert [c["clip_id"] for c in clips] == [clip_id]
    uploaded_sets = client.get("/api/studio/sets?source=uploaded").json()
    assert uploaded_sets[0]["name"] == "Uploads"

    # The /media route resolves the proxy from the pre-seeded cache.
    assert client.get(f"/api/media/{clip_id}").status_code == 200


def test_upload_into_explicit_set(ctx):
    client, _ = ctx
    sid = client.post("/api/studio/sets?source=uploaded", json={"name": "B-roll"}).json()["id"]
    r = client.post(
        "/api/studio/uploads",
        files={"file": ("a.webm", b"x", "video/webm")},
        data={"set_id": str(sid)},
    )
    assert r.status_code == 201
    assert r.json()["set_id"] == sid


def test_rejects_non_web_safe_format(ctx):
    client, _ = ctx
    r = client.post(
        "/api/studio/uploads",
        files={"file": ("a.mov", b"x", "video/quicktime")},
    )
    assert r.status_code == 415


def test_hx_request_returns_card_partial(ctx):
    client, _ = ctx
    r = client.post(
        "/api/studio/uploads",
        files={"file": ("nice.mp4", b"x", "video/mp4")},
        headers={"HX-Request": "true"},
    )
    assert r.status_code == 201
    assert "studio-clip-card" in r.text
    assert "nice.mp4" in r.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/integration/test_studio_uploads_api.py -q`
Expected: FAIL — 404/405 (route not defined)

- [ ] **Step 3: Implement the route**

In `backend/app/routes/studio.py`, update the imports at the top:

```python
import asyncio

import aiosqlite
from fastapi import (
    APIRouter,
    File,
    Form,
    Header,
    HTTPException,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import Response
from pydantic import BaseModel

from backend.app.deps import get_core_ctx
from backend.app.routes.pages.templates import templates
from backend.app.services.annotator import run_job
from backend.app.uploaded_ids import to_clip_id
```

Add the route after `remove_set_clip` (after line 122, before the `# ── runs ──` section):

```python
# ── uploads (Spec B) ──────────────────────────────────────────────────────────

_EXT_BY_MIME = {"video/mp4": ".mp4", "video/webm": ".webm"}


@router.post("/uploads", status_code=status.HTTP_201_CREATED)
async def upload_clip(
    request: Request,
    file: UploadFile = File(...),
    poster: UploadFile | None = File(None),
    set_id: int | None = Form(None),
    duration_secs: float | None = Form(None),
    width: int | None = Form(None),
    height: int | None = Form(None),
    hx_request: str | None = Header(None, alias="HX-Request"),
):
    ctx = get_core_ctx(request)
    s = ctx.settings

    mime = (file.content_type or "").split(";")[0].strip()
    allowed = {m.strip() for m in s.studio_upload_allowed_mimes.split(",") if m.strip()}
    ext = _EXT_BY_MIME.get(mime)
    if mime not in allowed or ext is None:
        raise HTTPException(
            415, f"Unsupported format {mime or 'unknown'!r}; allowed: {sorted(allowed)}"
        )

    data = await file.read()
    max_bytes = int(s.studio_upload_max_mb) * 1024 * 1024
    if len(data) > max_bytes:
        raise HTTPException(
            413, f"File too large ({len(data)} bytes); max {s.studio_upload_max_mb} MB"
        )

    if set_id is None:
        set_id = await ctx.studio_sets_repo.get_or_create_default_uploaded_set(ctx.db)

    pk = await ctx.uploaded_clips_repo.create(
        ctx.db,
        original_filename=file.filename or "upload",
        mime=mime,
        size_bytes=len(data),
        ext=ext,
        duration_secs=duration_secs,
        width=width,
        height=height,
    )
    clip_id = to_clip_id(pk)

    uploads_dir = s.data_dir / "cache" / "uploads"
    dest = uploads_dir / f"{clip_id}{ext}"

    def _write_video() -> None:
        uploads_dir.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)

    await asyncio.to_thread(_write_video)

    await ctx.proxy_cache_repo.record(
        ctx.db,
        clip_id=clip_id,
        file_path=str(dest),
        size_bytes=len(data),
        etag=None,
        provider_id="uploaded",
        provider_clip_id=str(clip_id),
    )

    if poster is not None:
        poster_bytes = await poster.read()
        thumbs_dir = s.data_dir / "cache" / "thumbs"
        thumb_dest = thumbs_dir / f"{clip_id}.jpg"

        def _write_poster() -> None:
            thumbs_dir.mkdir(parents=True, exist_ok=True)
            thumb_dest.write_bytes(poster_bytes)

        await asyncio.to_thread(_write_poster)

    await ctx.studio_sets_repo.add_clips(ctx.db, set_id, clip_ids=[clip_id])

    if hx_request == "true":
        c = {
            "clip_id": clip_id,
            "name": file.filename or f"upload-{clip_id}",
            "duration_secs": duration_secs,
            "year": None,
            "fps": 25.0,
            "has_cur": False,
            "has_other": False,
            "uploaded": True,
        }
        return templates.TemplateResponse(
            request,
            "pages/_studio_set_clip_card.html",
            {"c": c, "set_id": set_id, "focused_clip_id": None},
        )
    return {"clip_id": clip_id, "set_id": set_id}
```

> The `_studio_set_clip_card.html` `uploaded` branch is added in Task 12; the HX assertion in this task's test only needs `studio-clip-card` + the filename, both already rendered by the current template. Run the full card test in Task 12.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/integration/test_studio_uploads_api.py -q`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/app/routes/studio.py tests/integration/test_studio_uploads_api.py
git commit -m "feat(studio): POST /api/studio/uploads ingest route"
```

---

## Task 10: Annotator — run uploaded clips through Gemini

Extract `_resolve_clip_meta` so `_process_item` resolves metadata from `archive.get_clip` (archive) or `UploadedClipsRepo` (uploaded), and key the AI store as `("uploaded", id)`. Thread `uploaded_clips_repo` through `run_job` → `_process_item`, and pass it from `_run_in_bg`.

**Files:**
- Modify: `backend/app/services/annotator.py`
- Modify: `backend/app/routes/studio.py:161-180` (`_run_in_bg`)
- Test: `tests/unit/test_resolve_clip_meta.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_resolve_clip_meta.py
from pathlib import Path

import pytest

from backend.app.db import open_db
from backend.app.migrations_runner import apply_migrations
from backend.app.repositories.uploaded_clips import UploadedClipsRepo
from backend.app.services.annotator import _resolve_clip_meta
from backend.app.uploaded_ids import to_clip_id


class _FakeClip:
    name = "Archive Clip"
    duration_secs = 30.0
    fps = 25.0
    provider_data = {"media": {"filePath": "/x/clip.mov"}}

    class media:  # noqa: N801
        cached_path = "/x/clip.mov"
        upstream_handle = None
        size_bytes = 9999


class _FakeArchive:
    async def get_clip(self, clip_id):
        return _FakeClip()


@pytest.fixture
async def conn(tmp_path):
    cm = open_db(tmp_path / "t.db")
    c = await cm.__aenter__()
    await apply_migrations(c, Path("backend/migrations"))
    yield c
    await cm.__aexit__(None, None, None)


@pytest.mark.asyncio
async def test_archive_branch(conn):
    meta = await _resolve_clip_meta(
        conn, clip_id=42, archive=_FakeArchive(), uploaded_clips_repo=UploadedClipsRepo()
    )
    assert meta.clip_key == ("catdv", "42")
    assert meta.duration_secs == 30.0
    assert meta.clip_name == "Archive Clip"


@pytest.mark.asyncio
async def test_uploaded_branch(conn):
    repo = UploadedClipsRepo()
    pk = await repo.create(conn, original_filename="up.mp4", mime="video/mp4",
                           size_bytes=10, ext=".mp4", duration_secs=8.0)
    cid = to_clip_id(pk)
    meta = await _resolve_clip_meta(
        conn, clip_id=cid, archive=_FakeArchive(), uploaded_clips_repo=repo
    )
    assert meta.clip_key == ("uploaded", str(cid))
    assert meta.duration_secs == 8.0
    assert meta.media_kind == "video"
    assert meta.clip_name == "up.mp4"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_resolve_clip_meta.py -q`
Expected: FAIL — `ImportError: cannot import name '_resolve_clip_meta'`

- [ ] **Step 3: Add the helper + branch `_process_item`**

In `backend/app/services/annotator.py`, add to the imports near the top (with the other `backend.app` imports):

```python
from dataclasses import dataclass

from backend.app.uploaded_ids import is_uploaded
```

Add the dataclass + helper just above `async def _process_item` (line 297):

```python
@dataclass
class _ClipMeta:
    clip_key: tuple[str, str]
    duration_secs: float
    media_kind: str
    clip_name: str | None
    media_fps: float | None
    media_bytes: int | None
    media_ext: str | None
    clip_snapshot: dict[str, Any]


async def _resolve_clip_meta(db, *, clip_id, archive, uploaded_clips_repo) -> _ClipMeta:
    """Resolve the per-clip metadata + AI-store key for one run item,
    branching on whether `clip_id` is an uploaded synthetic id."""
    if is_uploaded(clip_id):
        row = await uploaded_clips_repo.get(db, clip_id)
        stored = (row or {}).get("stored_filename") or ""
        return _ClipMeta(
            clip_key=("uploaded", str(clip_id)),
            duration_secs=float((row or {}).get("duration_secs") or 0.0),
            media_kind="video",  # web-safe constraint guarantees video
            clip_name=(row or {}).get("original_filename"),
            media_fps=None,
            media_bytes=(row or {}).get("size_bytes"),
            media_ext=(Path(stored).suffix.lower() or None) if stored else None,
            clip_snapshot={},
        )
    canonical = await archive.get_clip(str(clip_id))
    media_path = str((canonical.media.cached_path or canonical.media.upstream_handle) or "")
    return _ClipMeta(
        clip_key=("catdv", str(clip_id)),
        duration_secs=float(canonical.duration_secs or 0.0),
        media_kind=classify_media_kind(media_path or None),
        clip_name=canonical.name or None,
        media_fps=canonical.fps or None,
        media_bytes=canonical.media.size_bytes,
        media_ext=(Path(media_path).suffix.lower() or None) if media_path else None,
        clip_snapshot=dict(canonical.provider_data),
    )
```

Now rewire `_process_item`. Add `uploaded_clips_repo` to its signature (after `studio_runs_repo`, near line 310):

```python
    studio_runs_repo: StudioRunsRepo,
    uploaded_clips_repo,
    run_telemetry_repo: RunTelemetryRepo,
```

Replace the hardcoded `clip_key` line (line 316):

```python
    clip_key = ("catdv", str(item.catdv_clip_id))
```

with:

```python
    meta = await _resolve_clip_meta(
        db,
        clip_id=item.catdv_clip_id,
        archive=archive,
        uploaded_clips_repo=uploaded_clips_repo,
    )
    clip_key = meta.clip_key
```

Replace the metadata block (lines 352-358, from `canonical = await archive.get_clip(...)` through `media_kind = classify_media_kind(...)`):

```python
    clip_snapshot: dict[str, Any] = meta.clip_snapshot
    duration_secs = meta.duration_secs
    media_kind = meta.media_kind
```

Update the `CaptureMeta(...)` construction (lines 383-391) to use `meta.*` instead of `canonical.*`:

```python
    capture = CaptureMeta(
        media_kind=media_kind,
        media_duration_secs=duration_secs or None,
        media_fps=meta.media_fps,
        media_bytes=meta.media_bytes,
        media_ext=meta.media_ext,
        clip_name=meta.clip_name,
        prompt_chars_rendered=len(rendered_body),
    )
```

In `run_job`, add `uploaded_clips_repo` to the signature (after `studio_runs_repo`, line 205) and pass it into the `_process_item(...)` call (after `studio_runs_repo=studio_runs_repo,`, line 246):

```python
    studio_runs_repo: StudioRunsRepo,
    uploaded_clips_repo,
    run_telemetry_repo: RunTelemetryRepo,
```

```python
                studio_runs_repo=studio_runs_repo,
                uploaded_clips_repo=uploaded_clips_repo,
                run_telemetry_repo=run_telemetry_repo,
```

- [ ] **Step 4: Pass it from the route's `_run_in_bg`**

In `backend/app/routes/studio.py`, inside `_run_in_bg` (line 163 `run_job(...)` call), add after `studio_runs_repo=ctx.studio_runs_repo,`:

```python
            studio_runs_repo=ctx.studio_runs_repo,
            uploaded_clips_repo=ctx.uploaded_clips_repo,
            run_telemetry_repo=ctx.run_telemetry_repo,
```

- [ ] **Step 5: Run tests — new helper + existing annotator suite stay green**

Run: `.venv/bin/python -m pytest tests/unit/test_resolve_clip_meta.py -q && .venv/bin/python -m pytest -k annotator -q`
Expected: PASS. If any existing annotator test calls `run_job`/`_process_item` directly, add `uploaded_clips_repo=UploadedClipsRepo()` to those call sites (search: `grep -rn "run_job(\|_process_item(" tests/`).

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/annotator.py backend/app/routes/studio.py tests/unit/test_resolve_clip_meta.py
git commit -m "feat(studio): run uploaded clips via Gemini (uploaded-aware metadata)"
```

---

## Task 11: Studio page — uploaded metadata in `_studio_set`

`_studio_set` must render uploaded clip cards with the filename (not `clip-{id}` and not an archive lookup). Batch-fetch uploaded rows and mark them so the card template suppresses the id tag.

**Files:**
- Modify: `backend/app/routes/pages/studio.py:128-177`
- Test: `tests/integration/test_studio_uploaded_page.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_studio_uploaded_page.py
import importlib

import pytest
from fastapi.testclient import TestClient


def _make_app(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.delenv("CATDV_PASSWORD", raising=False)
    from backend.app import main as main_mod
    importlib.reload(main_mod)
    return main_mod.app


@pytest.fixture
def client(monkeypatch, tmp_path):
    with TestClient(_make_app(monkeypatch, tmp_path)) as c:
        yield c


def test_uploaded_set_renders_filename(client):
    up = client.post(
        "/api/studio/uploads",
        files={"file": ("holiday.mp4", b"x", "video/mp4")},
    ).json()
    html = client.get(f"/studio/_set?set_id={up['set_id']}").text
    assert "holiday.mp4" in html
    assert "clip-" not in html          # no archive id-fallback name
    assert f"id:{up['clip_id']}" not in html  # uploaded cards suppress the id tag


def test_uploaded_sets_list_renders(client):
    client.post("/api/studio/uploads", files={"file": ("a.mp4", b"x", "video/mp4")})
    html = client.get("/studio/_sets?source=uploaded").text
    assert "Uploads" in html             # the default set name
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/integration/test_studio_uploaded_page.py -q`
Expected: FAIL — `assert 'id:1000000001' not in html` (the current card always renders the id tag)

- [ ] **Step 3: Branch the metadata build**

In `backend/app/routes/pages/studio.py`, add the import after line 13:

```python
from backend.app.uploaded_ids import is_uploaded
```

Replace the `_studio_set` body's enrichment loop (lines 143-177) with a version that batches uploaded metadata:

```python
    ctx = get_core_ctx(request)
    archive = _archive(request)
    clips_rows = await ctx.studio_sets_repo.list_clips(ctx.db, set_id)

    uploaded_ids = [c["clip_id"] for c in clips_rows if is_uploaded(c["clip_id"])]
    uploaded_meta = (
        await ctx.uploaded_clips_repo.get_many(ctx.db, uploaded_ids)
        if uploaded_ids
        else {}
    )

    enriched = []
    for c in clips_rows:
        cid = c["clip_id"]
        versions = await ctx.studio_runs_repo.versions_run_on_clip(ctx.db, clip_id=cid)
        has_cur = active_version_id is not None and active_version_id in versions
        has_other = any(v != active_version_id for v in versions)

        if is_uploaded(cid):
            row = uploaded_meta.get(cid)
            meta: dict = {
                "name": (row or {}).get("original_filename") or f"upload-{cid}",
                "duration_secs": (row or {}).get("duration_secs"),
                "year": None,
                "fps": 25.0,
                "uploaded": True,
            }
        else:
            meta = {
                "name": f"clip-{cid}",
                "duration_secs": None,
                "year": None,
                "fps": 25.0,
                "uploaded": False,
            }
            if archive is not None:
                try:
                    clip = await archive.get_clip(str(cid))
                    meta = {
                        "name": clip.name,
                        "duration_secs": clip.duration_secs,
                        "year": (clip.provider_data or {}).get("pragafilm.rok.natoceni"),
                        "fps": float(clip.fps or 25.0),
                        "uploaded": False,
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

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/integration/test_studio_uploaded_page.py -q`
Expected: PASS (after Task 12's card branch lands; if the `id:` assertion fails here, proceed to Task 12 then re-run — the two tasks are paired). Run Task 12 next, then re-run this test.

- [ ] **Step 5: Commit**

```bash
git add backend/app/routes/pages/studio.py tests/integration/test_studio_uploaded_page.py
git commit -m "feat(studio): uploaded clip metadata (filename) in _studio_set"
```

---

## Task 12: Clip card — uploaded branch

Uploaded cards show the filename, suppress the `id:N` tag, and use an `<img>` poster with an `onerror` placeholder (per the poster-frame-with-fallback decision).

**Files:**
- Modify: `backend/app/templates/pages/_studio_set_clip_card.html`
- Test: `tests/unit/test_uploaded_clip_card_render.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_uploaded_clip_card_render.py
from backend.app.routes.pages.templates import templates


def _render(c):
    tmpl = templates.env.get_template("pages/_studio_set_clip_card.html")
    return tmpl.render(c=c, set_id=7, focused_clip_id=None)


def test_archive_card_shows_id_tag():
    html = _render({"clip_id": 42, "name": "Archive", "duration_secs": 10.0,
                    "year": 1999, "fps": 25.0, "has_cur": False, "has_other": False,
                    "uploaded": False})
    assert "id:42" in html


def test_uploaded_card_suppresses_id_and_uses_img_poster():
    html = _render({"clip_id": 1_000_000_001, "name": "holiday.mp4",
                    "duration_secs": 12.0, "year": None, "fps": 25.0,
                    "has_cur": False, "has_other": False, "uploaded": True})
    assert "holiday.mp4" in html
    assert "id:1000000001" not in html
    assert "onerror" in html  # placeholder fallback on poster decode failure
    assert "/api/media/1000000001/thumb" in html
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_uploaded_clip_card_render.py -q`
Expected: FAIL — `assert 'id:1000000001' not in html` (current template always renders the tag) and `assert 'onerror' in html`

- [ ] **Step 3: Branch the template**

Replace `backend/app/templates/pages/_studio_set_clip_card.html` with:

```html
{# Clip card — focus on click, select via checkbox, remove on hover X,
   run-dots top-right. Vanilla onclick (HTMX-injected, no x-data). Variables:
     c       — dict: clip_id, name, duration_secs, year, fps, has_cur,
               has_other, uploaded (bool)
     set_id  — outer set id (used for the remove DELETE call) #}
<div class="studio-clip-card{% if focused_clip_id is defined and focused_clip_id == c.clip_id %} selected{% endif %}{% if c.uploaded %} uploaded{% endif %}"
     onclick="window.studio.focusClip({{ c.clip_id }})"
     data-clip-id="{{ c.clip_id }}">
  <input type="checkbox" class="clip-check"
         onclick="event.stopPropagation(); window.studio.toggleClip({{ c.clip_id }}, this.checked);" />
  {% set thumb_url = '/api/media/' ~ c.clip_id ~ '/thumb' %}
  {% if c.uploaded %}
    <div class="thumb">
      <img class="thumb-img" src="{{ thumb_url }}" alt=""
           onerror="this.closest('.thumb').classList.add('thumb-missing'); this.remove();" />
      {% if c.duration_secs %}
        <span class="tc">{{ smpte(c.duration_secs, c.fps or 25.0) }}</span>
      {% endif %}
    </div>
  {% else %}
    <div class="thumb" style="background-image:url('{{ thumb_url }}')">
      {% if c.year %}<span class="yr">{{ c.year }}</span>{% endif %}
      {% if c.duration_secs %}
        <span class="tc">{{ smpte(c.duration_secs, c.fps or 25.0) }}</span>
      {% endif %}
    </div>
  {% endif %}
  <div class="meta">
    <div class="name" title="{{ c.name }}">{{ c.name }}</div>
    {% if not c.uploaded %}
      <div class="tag">id:{{ c.clip_id }}{% if c.year %} · {{ c.year }}{% endif %}</div>
    {% endif %}
  </div>
  <div class="dots">
    {% if c.has_cur %}<span class="rundot cur" title="ran with active version"></span>{% endif %}
    {% if c.has_other %}<span class="rundot" title="ran with other version(s)"></span>{% endif %}
  </div>
  <button class="remove-x" title="remove from set"
          onclick="event.stopPropagation(); window.studio.removeClip({{ set_id }}, {{ c.clip_id }}, this);">×</button>
</div>
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_uploaded_clip_card_render.py tests/integration/test_studio_uploaded_page.py tests/integration/test_studio_uploads_api.py -q`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
git add backend/app/templates/pages/_studio_set_clip_card.html tests/unit/test_uploaded_clip_card_render.py
git commit -m "feat(studio): uploaded clip card (filename, poster img, no id tag)"
```

---

## Task 13: Set rename UI (shared card; archive + uploaded)

Backend `PATCH /api/studio/sets/{id}` already exists. Add the inline-rename affordance to the shared card and a `renameSet` method to the `studioSets` component. JS is verified by a source-scan test (Python-only repo).

**Files:**
- Modify: `backend/app/templates/pages/_studio_set_card.html`
- Modify: `backend/app/static/studio.js` (`studioSets`)
- Test: `tests/unit/test_studio_rename_ui.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_studio_rename_ui.py
from pathlib import Path

CARD = Path("backend/app/templates/pages/_studio_set_card.html").read_text()
JS = Path("backend/app/static/studio.js").read_text()


def test_card_has_rename_affordance():
    assert "renameSet(" in CARD


def test_studiosets_has_renameset_method():
    assert "renameSet(" in JS
    assert "method: 'PATCH'" in JS
    assert "/api/studio/sets/" in JS
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_studio_rename_ui.py -q`
Expected: FAIL — `assert 'renameSet(' in CARD`

- [ ] **Step 3: Add the affordance + method**

In `backend/app/templates/pages/_studio_set_card.html`, add a rename pencil to the `studio-set-row`, right after the `name` span:

```html
    <span class="name" @click="toggle({{ f.id }})">{{ f.name }}</span>
    <button class="set-rename" title="rename set"
            @click.stop="renameSet({{ f.id }}, '{{ f.name|e }}')">✎</button>
```

In `backend/app/static/studio.js`, add a `renameSet` method to the `studioSets` Alpine component (after `createSet`, before the closing `}));` near line 263):

```javascript
    async renameSet(setId, currentName) {
      const name = (window.prompt('Rename set', currentName) || '').trim();
      if (!name || name === currentName) return;
      const res = await fetch(`/api/studio/sets/${setId}`, {
        method: 'PATCH',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({name}),
      });
      if (res.ok) {
        const card = document.querySelector(`.studio-set[data-set-id="${setId}"] .name`);
        if (card) card.textContent = name;
        Alpine.store('toast').push(`Renamed to "${name}".`, { level: 'success' });
      } else if (res.status === 409) {
        Alpine.store('toast').push(`A set named "${name}" already exists.`, { level: 'error' });
      } else {
        Alpine.store('toast').push(`Rename failed (HTTP ${res.status}).`, { level: 'error' });
      }
    },
```

> `window.prompt` is acceptable here (a one-field rename, no markup) and avoids a new modal component. It is NOT a JS `alert()`/`confirm()` dialog that blocks the page lifecycle the way the browser-automation guidance warns about; it returns a value synchronously and the existing `removeClip` shim already uses `confirm()`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_studio_rename_ui.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/templates/pages/_studio_set_card.html backend/app/static/studio.js tests/unit/test_studio_rename_ui.py
git commit -m "feat(studio): inline set rename on the shared card (archive + uploaded)"
```

---

## Task 14: Uploaded tab — real navigator + upload dropzone

Replace the stub with the real uploaded set list and an upload affordance; make `switchSource('uploaded')` load the real list; add the `uploadClips` poster-capture logic. The uploaded badge count comes from the page route.

**Files:**
- Modify: `backend/app/routes/pages/studio.py:36-114` (`studio_page` — add `uploaded_clip_total`)
- Modify: `backend/app/templates/pages/_studio_nav.html`
- Modify: `backend/app/templates/pages/_studio_uploaded_stub.html` (becomes the upload host)
- Modify: `backend/app/static/studio.js` (`switchSource` + new `uploadClips` logic)
- Test: `tests/unit/test_studio_uploads_js.py`, and extend `tests/integration/test_studio_page.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_studio_uploads_js.py
from pathlib import Path

JS = Path("backend/app/static/studio.js").read_text()
NAV = Path("backend/app/templates/pages/_studio_nav.html").read_text()
STUB = Path("backend/app/templates/pages/_studio_uploaded_stub.html").read_text()


def test_switchsource_uploaded_loads_real_list():
    # The stub-injection branch is gone; uploaded now swaps in the fetched list.
    assert "Uploads coming soon" not in JS


def test_upload_captures_poster_and_posts_multipart():
    assert "capturePoster" in JS or "toBlob" in JS
    assert "FormData" in JS
    assert "/api/studio/uploads" in JS


def test_nav_uploaded_badge_uses_total():
    assert "uploaded_clip_total" in NAV


def test_stub_hosts_dropzone():
    assert "studio-dropzone" in STUB or "uploadClips" in STUB
```

```python
# tests/integration/test_studio_page.py  (append)
def test_studio_page_exposes_uploaded_total(client):
    client.post("/api/studio/uploads", files={"file": ("a.mp4", b"x", "video/mp4")})
    html = client.get("/studio").text
    assert "data-studio-nav-body" in html
```

> Confirm `tests/integration/test_studio_page.py` already has a `client` fixture (it does — Spec A added page tests there). Only append the new function.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_studio_uploads_js.py -q`
Expected: FAIL — `assert 'Uploads coming soon' not in JS`

- [ ] **Step 3: Add `uploaded_clip_total` to the page route**

In `backend/app/routes/pages/studio.py::studio_page`, after the `archive_clip_total` block (line 49-51), add:

```python
    uploaded_clip_total = await ctx.studio_sets_repo.clip_total_for_source(
        ctx.db, source="uploaded"
    )
```

and add it to the template context dict (after `"archive_clip_total": archive_clip_total,`):

```python
            "archive_clip_total": archive_clip_total,
            "uploaded_clip_total": uploaded_clip_total,
```

- [ ] **Step 4: Update the nav badge + body**

In `backend/app/templates/pages/_studio_nav.html`, change the Uploaded badge (line 18) from the hardcoded `0`:

```html
      <span class="ico">⤒</span> Uploaded
      <span class="badge">{{ uploaded_clip_total }}</span>
```

Change the nav-body default-render branch (lines 31-35) so the uploaded source renders the stub host (which itself includes the set list):

```html
  <div class="studio-nav-body" data-studio-nav-body>
    {% if nav_source == 'uploaded' %}
      {% include "pages/_studio_uploaded_stub.html" %}
    {% else %}
      {% include "pages/_studio_set_list.html" %}
    {% endif %}
  </div>
```

(unchanged structurally — the stub file is what changes in Step 5.)

- [ ] **Step 5: Make the stub the upload host**

Replace `backend/app/templates/pages/_studio_uploaded_stub.html` with:

```html
{# Uploaded source body: a dropzone + file picker that ingests web-safe
   videos, plus the uploaded set list. The dropzone JS lives in
   studio.js (uploadClips). #}
<div x-data="uploadClips()">
  <div class="studio-dropzone"
       :class="dragging && 'drag'"
       @dragover.prevent="dragging = true"
       @dragleave.prevent="dragging = false"
       @drop.prevent="onDrop($event)"
       @click="$refs.file.click()">
    <input type="file" x-ref="file" accept="video/mp4,video/webm" class="hidden"
           @change="onPick($event)" multiple />
    <div class="dz-ico">⤒</div>
    <div class="dz-title">Drop video files or click to upload</div>
    <div class="dz-sub">mp4 / webm only</div>
    <template x-if="busy">
      <div class="dz-progress" x-text="`Uploading ${doneCount}/${totalCount}…`"></div>
    </template>
  </div>
</div>
{% include "pages/_studio_set_list.html" %}
```

- [ ] **Step 6: Update `switchSource` + add `uploadClips` in studio.js**

In `backend/app/static/studio.js`, replace the `switchSource` body's uploaded branch (lines 155-164) so both tabs fetch + reinit the real list:

```javascript
    async switchSource(next, btn) {
      if (this.source === next) return;
      this.source = next;
      Alpine.store('studio').clearSelection();
      const body = document.querySelector('[data-studio-nav-body]');
      if (!body) return;
      try {
        const html = await fetch(`/studio/_sets?source=${next}`).then(r => r.text());
        body.innerHTML = html;
        window.htmxAlpine.reinit(body);
        localStorage.setItem('studio.navSource', next);
      } catch (err) {
        console.error('switchSource failed', err);
        Alpine.store('toast').push(`Could not load ${next} sets.`, { level: 'error' });
      }
    },
```

> `/studio/_sets?source=uploaded` returns `_studio_set_list.html` (the set list only). The dropzone host (`_studio_uploaded_stub.html`) is rendered on the *initial* page load via `_studio_nav.html`. When switching tabs we swap in just the set list; the dropzone persists from the initial render only if uploaded was the initial tab. To keep the dropzone present after a tab switch into Uploaded, make `_studio_sets` source-aware: when `source == 'uploaded'`, render the stub host instead of the bare list.

In `backend/app/routes/pages/studio.py::_studio_sets` (lines 117-125), branch the template:

```python
@router.get("/studio/_sets", response_class=HTMLResponse)
async def _studio_sets(request: Request, source: str = "archive"):
    ctx = get_core_ctx(request)
    sets = await ctx.studio_sets_repo.list_sets_with_counts(ctx.db, source=source)
    template = (
        "pages/_studio_uploaded_stub.html"
        if source == "uploaded"
        else "pages/_studio_set_list.html"
    )
    return templates.TemplateResponse(
        request,
        template,
        {"sets": sets, "active_version": None, "nav_source": source},
    )
```

Add the `uploadClips` Alpine component in `backend/app/static/studio.js`, inside the `alpine:init` listener (after the `studioSets` component, before `studioPromptCard`):

```javascript
  Alpine.data('uploadClips', () => ({
    dragging: false,
    busy: false,
    doneCount: 0,
    totalCount: 0,

    onDrop(evt) {
      this.dragging = false;
      this.uploadFiles([...(evt.dataTransfer?.files || [])]);
    },
    onPick(evt) {
      this.uploadFiles([...(evt.target.files || [])]);
      evt.target.value = '';  // allow re-picking the same file
    },

    async uploadFiles(files) {
      const vids = files.filter(f => f.type === 'video/mp4' || f.type === 'video/webm');
      const rejected = files.length - vids.length;
      if (rejected > 0) {
        Alpine.store('toast').push(
          `${rejected} file${rejected === 1 ? '' : 's'} skipped — mp4/webm only.`,
          { level: 'error' },
        );
      }
      if (!vids.length) return;
      this.busy = true;
      this.totalCount = vids.length;
      this.doneCount = 0;
      for (const f of vids) {
        try { await this.uploadOne(f); }
        catch (err) {
          console.error('upload failed', f.name, err);
          Alpine.store('toast').push(
            `Upload failed for ${f.name}: ${err.message || String(err)}`,
            { level: 'error' },
          );
        } finally { this.doneCount++; }
      }
      this.busy = false;
    },

    // Capture a poster frame at ~1s via an offscreen <video> + <canvas>.
    // Returns a Blob or null (decode failure → server-side placeholder).
    async capturePoster(file, meta) {
      try {
        return await new Promise((resolve) => {
          const v = document.createElement('video');
          v.preload = 'metadata';
          v.muted = true;
          v.src = URL.createObjectURL(file);
          const done = (blob) => { URL.revokeObjectURL(v.src); resolve(blob); };
          v.onloadedmetadata = () => {
            meta.duration = v.duration || null;
            meta.width = v.videoWidth || null;
            meta.height = v.videoHeight || null;
            v.currentTime = Math.min(1, (v.duration || 1) / 2);
          };
          v.onseeked = () => {
            try {
              const c = document.createElement('canvas');
              c.width = v.videoWidth; c.height = v.videoHeight;
              c.getContext('2d').drawImage(v, 0, 0);
              c.toBlob((b) => done(b), 'image/jpeg', 0.8);
            } catch (e) { done(null); }
          };
          v.onerror = () => done(null);
        });
      } catch (e) { return null; }
    },

    async uploadOne(file) {
      const meta = { duration: null, width: null, height: null };
      const poster = await this.capturePoster(file, meta);
      const fd = new FormData();
      fd.append('file', file, file.name);
      if (poster) fd.append('poster', poster, 'poster.jpg');
      if (meta.duration != null) fd.append('duration_secs', String(meta.duration));
      if (meta.width != null) fd.append('width', String(meta.width));
      if (meta.height != null) fd.append('height', String(meta.height));
      const res = await fetch('/api/studio/uploads', {
        method: 'POST', headers: {'HX-Request': 'true'}, body: fd,
      });
      if (!res.ok) {
        let detail = `HTTP ${res.status}`;
        try { detail = (await res.json()).detail || detail; } catch (e) { /* non-JSON */ }
        throw new Error(detail);
      }
      const card = await res.text();
      // Insert the card into the default 'Uploads' set if it's expanded,
      // else just toast — the set list reflects it on next expand.
      Alpine.store('toast').push(`Uploaded ${file.name}.`, { level: 'success' });
      // Refresh the uploaded set list so the new clip + counts show.
      const body = document.querySelector('[data-studio-nav-body]');
      if (body) {
        const html = await fetch('/studio/_sets?source=uploaded').then(r => r.text());
        body.innerHTML = html;
        window.htmxAlpine.reinit(body);
      }
    },
  }));
```

- [ ] **Step 6b: Make `createSet` source-aware (so "+ new set" on the Uploaded tab creates an uploaded set)**

The shared `_studio_set_list.html` "+" button calls `studioSets.createSet`, which currently POSTs `/api/studio/sets` with no `?source=`, defaulting to `archive`. On the Uploaded tab that would silently create an archive set. Read the active tab's source from the DOM and pass it. In `backend/app/static/studio.js`, change the `createSet` fetch line (line ~238):

```javascript
    async createSet() {
      const name = this.newSetName.trim();
      if (!name) return;
      const source = document.querySelector('.studio-nav-tab.active')?.dataset.navSource || 'archive';
      const res = await fetch(`/api/studio/sets?source=${source}`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json', 'HX-Request': 'true'},
        body: JSON.stringify({name}),
      });
```

(The rest of `createSet` is unchanged — it inserts the returned card into `.studio-sets-list`.) Add an assertion to `tests/unit/test_studio_uploads_js.py`:

```python
def test_createset_is_source_aware():
    assert "navSource || 'archive'" in JS
    assert "/api/studio/sets?source=" in JS
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_studio_uploads_js.py tests/integration/test_studio_page.py -q`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add backend/app/routes/pages/studio.py backend/app/templates/pages/_studio_nav.html backend/app/templates/pages/_studio_uploaded_stub.html backend/app/static/studio.js tests/unit/test_studio_uploads_js.py tests/integration/test_studio_page.py
git commit -m "feat(studio): real Uploaded tab — dropzone, poster capture, live list"
```

---

## Task 15: CSS — dropzone, rename, uploaded poster placeholder

**Files:**
- Modify: `backend/app/static/app.css`
- Test: `tests/unit/test_studio_nav_css.py` (append)

- [ ] **Step 1: Write the failing test (append)**

```python
# tests/unit/test_studio_nav_css.py  (append)
def test_dropzone_and_placeholder_styles_present():
    from pathlib import Path
    css = Path("backend/app/static/app.css").read_text()
    assert ".studio-dropzone" in css
    assert ".thumb-missing" in css
    assert ".set-rename" in css
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_studio_nav_css.py -q`
Expected: FAIL — `assert '.studio-dropzone' in css`

- [ ] **Step 3: Add the styles**

Append to `backend/app/static/app.css` (use existing design tokens — no raw hex beyond what mirrors neighbours):

```css
/* ── Studio uploads (Spec B) ─────────────────────────────────────────── */
.studio-dropzone {
  border: 1px dashed var(--border);
  border-radius: var(--radius, 8px);
  padding: 16px;
  margin: 8px;
  text-align: center;
  cursor: pointer;
  color: var(--text-muted);
  transition: border-color .15s, background .15s;
}
.studio-dropzone.drag { border-color: var(--accent); background: var(--surface-2); }
.studio-dropzone .dz-ico { font-size: 20px; }
.studio-dropzone .dz-title { font-weight: 600; color: var(--text); margin-top: 4px; }
.studio-dropzone .dz-sub { font-size: 12px; }
.studio-dropzone .dz-progress { margin-top: 6px; color: var(--accent); }
.studio-dropzone .hidden { display: none; }

.studio-clip-card .thumb-img { width: 100%; height: 100%; object-fit: cover; display: block; }
.studio-clip-card .thumb.thumb-missing {
  background: var(--surface-2);
  display: flex; align-items: center; justify-content: center;
}
.studio-clip-card .thumb.thumb-missing::after {
  content: "▶"; color: var(--text-muted); font-size: 18px;
}

.studio-set-row .set-rename {
  background: none; border: 0; cursor: pointer;
  color: var(--text-muted); opacity: 0; transition: opacity .12s;
}
.studio-set-row:hover .set-rename { opacity: 1; }
.studio-set-row .set-rename:hover { color: var(--text); }
```

> If any token name (`--border`, `--accent`, `--surface-2`, `--text-muted`, `--text`, `--radius`) doesn't exist, grep `:root` in `app.css` and substitute the nearest existing token — do not introduce raw hex.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/test_studio_nav_css.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/static/app.css tests/unit/test_studio_nav_css.py
git commit -m "style(studio): dropzone, uploaded poster placeholder, set-rename"
```

---

## Task 16: Full regression + guardrails

**Files:** none (verification only)

- [ ] **Step 1: Run the full test suite**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS (all). Triage any failure to the task that introduced it.

- [ ] **Step 2: Run the import-linter contracts**

Run: `.venv/bin/lint-imports`
Expected: contracts kept. `uploaded_ids.py` is a **top-level leaf** module (`backend/app/uploaded_ids.py`, like `media_kind.py`) with no imports of its own, so `repositories/uploaded_clips.py` importing it does **not** violate the "repositories don't import services" contract. Routes still don't import `httpx` (the upload route uses `UploadFile`/`Form` from FastAPI, not httpx).

- [ ] **Step 3: Run the structural-erosion / N+1 guards touched here**

Run: `.venv/bin/python -m pytest tests/unit/test_no_sync_fs_in_async.py tests/unit/test_context_delegation.py tests/integration/test_studio_sets_perf.py -q`
Expected: PASS. The upload route's `dest.write_bytes` / poster write run inside `asyncio.to_thread`, so the no-sync-fs-in-async guard stays green.

- [ ] **Step 4: Manual acceptance (from the spec)**

Start the server (use the `server-start` skill) and walk the 8 **Manual acceptance flows** in `docs/specs/2026-06-08-prompt-studio-uploads-spec-b-design.md`. Tick each or report the exact step that broke.

- [ ] **Step 5: Final commit (if any guard-driven tweaks were needed)**

```bash
git add -A
git commit -m "test(studio): full regression + guardrails green for Spec B uploads"
```

---

## Notes for the implementer

- **CatDV seat discipline:** the manual flows that *run* a prompt need GCS + Gemini, not necessarily CatDV. Playback/listing flows work fully offline. Start the server via the `server-start` skill and stop it via `server-stop` (SIGTERM only) — see `CLAUDE.md`.
- **ADR:** this work makes a deliberate identity-model call (synthetic high-offset ids + thin guards). Add an ADR under `docs/adr/NNNN-*.md` and index it in `docs/decisions.md` before the session ends (per `CLAUDE.md`).
- **Paired tasks:** Tasks 11 and 12 are paired (the page route marks `uploaded` and the card consumes it). If executed by separate subagents, run them in order and re-run Task 11's test after Task 12.
- **Out of scope (spec follow-ups):** deleting the file + `uploaded_clip` row when a clip leaves its last set; delete-set UI.
