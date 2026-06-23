# Cache Queue Progress Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show live download progress — a percentage on the clip-page cache button (`Caching… (45%)`) and `45%   12.3 MB` on the cache queue page — by recording `bytes_downloaded` / `bytes_total` on the `prefetch_queue` row during download and rendering it through the polling both surfaces already do.

**Architecture:** A progress callback (`ProgressCb = Callable[[int, int], Awaitable[None]]`) is threaded down the existing download path — `MediaPrefetcher` → `MediaCacheBackend.ensure_cached` → `ProxyResolver.path_for_clip_id` → `catdv_client.download_proxy/original` → `_stream_to_file`. The callback reports **absolute bytes-on-disk** per chunk. `MediaPrefetcher` owns a throttle that writes progress to the row at most once per ~750 ms. The frontend just renders two new fields already present in the existing poll payloads. No new endpoint, no SSE.

**Tech Stack:** Python 3.12/3.13 (FastAPI, aiosqlite, httpx), Jinja2 partials, Alpine.js. Tests: pytest + `pytest.mark.asyncio`.

## Global Constraints

- **Venv:** run everything via `.venv/bin/python` / `.venv/bin/pytest` (never system python).
- **TDD:** failing test first, then minimal implementation, then green, then commit.
- **No new cache layer / no bypass:** this is instrumentation of the existing download path only. Do not call `catdv.download_*` or GCS directly anywhere new (CLAUDE.md "Cache management").
- **No sync fs I/O in `async def`** unmarked: existing `# sync-io-ok` pragmas stay; add none new.
- **Offline-safe:** the new `progress_cb` parameter is **optional, default `None`** on every signature, so every non-prefetch caller (playback, AI-store ingest, offline resolvers) is unchanged.
- **`bytes_total == 0` means "unknown total"** → render no percentage (graceful fallback). Never divide by zero / emit `NaN%`.
- **Commit messages** end with:
  `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`

---

### Task 1: `prefetch_queue` — `bytes_total` column + `update_progress`

**Files:**
- Create: `backend/migrations/0024_prefetch_progress.sql`
- Modify: `backend/app/repositories/prefetch_queue.py`
- Test: `tests/integration/test_prefetch_queue_repo.py`

**Interfaces:**
- Consumes: the existing `db` test fixture (applies all migrations), `PrefetchQueueRepo`.
- Produces:
  - `prefetch_queue.bytes_total INTEGER NOT NULL DEFAULT 0` column.
  - `PrefetchQueueRepo.update_progress(conn, rid: int, bytes_downloaded: int, bytes_total: int) -> None` — single `UPDATE`.
  - `bytes_total` present in every row dict from `get`, `claim_next`, `list_active`, `list_recent`.
  - `requeue_orphans` resets `bytes_downloaded=0, bytes_total=0`.

- [ ] **Step 1: Write the migration**

Create `backend/migrations/0024_prefetch_progress.sql`:

```sql
-- Issue #78: record total proxy size so the UI can show download progress.
-- bytes_downloaded already exists (0007); add the denominator. 0 = unknown
-- (no Content-Length) → the UI shows no percentage.
ALTER TABLE prefetch_queue ADD COLUMN bytes_total INTEGER NOT NULL DEFAULT 0;
```

- [ ] **Step 2: Write the failing tests**

Append to `tests/integration/test_prefetch_queue_repo.py`:

```python
@pytest.mark.asyncio
async def test_update_progress_writes_both_byte_fields(db):
    repo = PrefetchQueueRepo()
    rid = await repo.enqueue(db, key=("catdv", "1"), who="request")
    await repo.claim_next(db)
    await repo.update_progress(db, rid, 5_000_000, 27_000_000)
    row = await repo.get(db, rid)
    assert row["bytes_downloaded"] == 5_000_000
    assert row["bytes_total"] == 27_000_000
    assert row["status"] == "downloading"  # progress does not change status


@pytest.mark.asyncio
async def test_bytes_total_defaults_zero_and_is_listed(db):
    repo = PrefetchQueueRepo()
    await repo.enqueue(db, key=("catdv", "1"), who="request")
    active = await repo.list_active(db)
    assert active[0]["bytes_total"] == 0  # new column present, defaults 0


@pytest.mark.asyncio
async def test_requeue_orphans_resets_progress(db):
    repo = PrefetchQueueRepo()
    rid = await repo.enqueue(db, key=("catdv", "1"), who="request")
    await repo.claim_next(db)
    await repo.update_progress(db, rid, 9_000_000, 27_000_000)
    await repo.requeue_orphans(db)
    row = await repo.get(db, rid)
    assert row["status"] == "queued"
    assert row["bytes_downloaded"] == 0 and row["bytes_total"] == 0
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/integration/test_prefetch_queue_repo.py -k "progress or bytes_total" -v`
Expected: FAIL — `update_progress` does not exist / `KeyError: 'bytes_total'`.

- [ ] **Step 4: Implement the repo changes**

In `backend/app/repositories/prefetch_queue.py`:

Add `"bytes_total"` to the key tuple in `_row_to_dict` (after `"bytes_downloaded"`):

```python
def _row_to_dict(row) -> dict[str, Any]:
    keys = (
        "id",
        "provider_id",
        "provider_clip_id",
        "status",
        "requested_by",
        "requested_at",
        "started_at",
        "finished_at",
        "error",
        "bytes_downloaded",
        "bytes_total",
    )
    return dict(zip(keys, row, strict=False))
```

Add `"bytes_total"` to `_row_to_dict_with_name` immediately after `"bytes_downloaded"` and **before** `"clip_name"`:

```python
def _row_to_dict_with_name(row) -> dict[str, Any]:
    keys = (
        "id",
        "provider_id",
        "provider_clip_id",
        "status",
        "requested_by",
        "requested_at",
        "started_at",
        "finished_at",
        "error",
        "bytes_downloaded",
        "bytes_total",
        "clip_name",
    )
    return dict(zip(keys, row, strict=False))
```

Add `q.bytes_total` to `_LIST_COLUMNS_WITH_NAME` (after `q.bytes_downloaded,`):

```python
_LIST_COLUMNS_WITH_NAME = """
    q.id, q.provider_id, q.provider_clip_id, q.status,
    q.requested_by, q.requested_at, q.started_at, q.finished_at,
    q.error, q.bytes_downloaded, q.bytes_total,
    cc.name AS clip_name
"""
```

Add `bytes_total` to the `SELECT` in `get()` (after `bytes_downloaded`):

```python
    async def get(self, conn: aiosqlite.Connection, rid: int) -> dict[str, Any] | None:
        cur = await conn.execute(
            """
            SELECT id, provider_id, provider_clip_id, status,
                   requested_by, requested_at, started_at, finished_at,
                   error, bytes_downloaded, bytes_total
              FROM prefetch_queue WHERE id = ?
            """,
            (rid,),
        )
        row = await cur.fetchone()
        return _row_to_dict(row) if row is not None else None
```

Add the `update_progress` method (place it right after `claim_next`, before `requeue_orphans`):

```python
    async def update_progress(
        self,
        conn: aiosqlite.Connection,
        rid: int,
        bytes_downloaded: int,
        bytes_total: int,
    ) -> None:
        """Record mid-download progress on a `downloading` row. Throttling
        is the caller's job (MediaPrefetcher); this is a plain UPDATE."""
        await conn.execute(
            "UPDATE prefetch_queue "
            "   SET bytes_downloaded=?, bytes_total=? "
            " WHERE id=?",
            (int(bytes_downloaded), int(bytes_total), rid),
        )
        await conn.commit()
```

Update `requeue_orphans` to reset both byte fields:

```python
        cur = await conn.execute(
            "UPDATE prefetch_queue "
            "   SET status='queued', started_at=NULL, "
            "       bytes_downloaded=0, bytes_total=0 "
            " WHERE status='downloading'"
        )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/integration/test_prefetch_queue_repo.py -v`
Expected: PASS (all, including the pre-existing tests).

- [ ] **Step 6: Commit**

```bash
git add backend/migrations/0024_prefetch_progress.sql backend/app/repositories/prefetch_queue.py tests/integration/test_prefetch_queue_repo.py
git commit -m "feat(#78): prefetch_queue.bytes_total column + update_progress

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: `catdv_client` — progress callback in the download path

**Files:**
- Modify: `backend/app/services/catdv_client.py` (`_content_total_bytes` reuse; `_stream_to_file:393`, `download_proxy:268`, `download_original:339`)
- Test: `tests/unit/test_catdv_client_progress.py` (create)

**Interfaces:**
- Consumes: nothing from prior tasks.
- Produces:
  - `ProgressCb = Callable[[int, int], Awaitable[None]]` type alias exported from `catdv_client`.
  - `download_proxy(self, clip_id, dest, chunk_size=..., *, progress_cb: ProgressCb | None = None)`
  - `download_original(self, media_id, dest, chunk_size=..., *, progress_cb: ProgressCb | None = None)`
  - `_stream_to_file(self, resp, dest, *, append, chunk_size, progress_cb=None, base=0, total=0)` — calls `progress_cb(base + written, total)` after each chunk when `progress_cb` is not None.
  - Contract: callback receives **absolute bytes-on-disk** (`base` = bytes already on disk before this stream; `written` = bytes written so far this stream) and `total` (`0` when unknown).

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_catdv_client_progress.py`:

```python
"""Issue #78: the download path reports absolute bytes-on-disk + total
through an optional progress callback, per chunk."""

import pytest


class _FakeStreamResp:
    """Minimal stand-in for httpx streaming response over _stream_to_file."""

    def __init__(self, chunks: list[bytes]):
        self._chunks = chunks

    async def aiter_bytes(self, chunk_size):
        for c in self._chunks:
            yield c


def _client():
    # _stream_to_file uses only self via the method; construct without network.
    from backend.app.services.catdv_client import CatdvClient
    return CatdvClient.__new__(CatdvClient)


@pytest.mark.asyncio
async def test_stream_reports_absolute_progress_with_base(tmp_path):
    client = _client()
    dest = tmp_path / "clip.mov"
    seen: list[tuple[int, int]] = []

    async def cb(downloaded: int, total: int) -> None:
        seen.append((downloaded, total))

    resp = _FakeStreamResp([b"a" * 10, b"b" * 5])
    await client._stream_to_file(
        resp, dest, append=False, chunk_size=1024,
        progress_cb=cb, base=100, total=200,
    )
    # absolute = base(100) + cumulative written
    assert seen == [(110, 200), (115, 200)]


@pytest.mark.asyncio
async def test_stream_no_callback_is_noop(tmp_path):
    client = _client()
    dest = tmp_path / "clip.mov"
    resp = _FakeStreamResp([b"x" * 3])
    # No progress_cb passed → must not raise, file still written.
    await client._stream_to_file(resp, dest, append=False, chunk_size=1024)
    assert dest.read_bytes() == b"x" * 3
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/pytest tests/unit/test_catdv_client_progress.py -v`
Expected: FAIL — `_stream_to_file() got an unexpected keyword argument 'progress_cb'`.

- [ ] **Step 3: Implement the callback plumbing**

In `backend/app/services/catdv_client.py`, add the type alias near the top (after the existing imports, before `_content_total_bytes`). Ensure `Awaitable` and `Callable` are imported from `collections.abc`:

```python
from collections.abc import Awaitable, Callable

# Issue #78: report absolute bytes-on-disk + total (0 = unknown) during a
# download. Threaded MediaPrefetcher -> backend -> resolver -> here.
ProgressCb = Callable[[int, int], Awaitable[None]]
```

Replace `_stream_to_file` (line 393) with:

```python
    async def _stream_to_file(
        self,
        resp: httpx.Response,
        dest: Path,
        *,
        append: bool,
        chunk_size: int,
        progress_cb: "ProgressCb | None" = None,
        base: int = 0,
        total: int = 0,
    ) -> None:
        mode = "ab" if append else "wb"
        dest.parent.mkdir(parents=True, exist_ok=True)
        written = 0
        # File writes hop to a worker thread so the event loop stays
        # responsive while we ingest a multi-hundred-MB proxy stream.
        with open(dest, mode) as f:  # noqa: ASYNC230
            async for chunk in resp.aiter_bytes(chunk_size):
                await asyncio.to_thread(f.write, chunk)
                if progress_cb is not None:
                    written += len(chunk)
                    await progress_cb(base + written, total)
```

In `download_proxy` (signature at line 268), add the keyword-only param and pass `base`/`total`/`progress_cb` into the `_stream_to_file` call. New signature:

```python
    async def download_proxy(
        self,
        clip_id: int,
        dest: Path,
        chunk_size: int = 1024 * 1024,
        *,
        progress_cb: "ProgressCb | None" = None,
    ) -> None:
```

In its body, change the existing `_stream_to_file` call (line ~311) to:

```python
                        append = resp.status_code == 206 and existing > 0
                        await self._stream_to_file(
                            resp, dest, append=append, chunk_size=chunk_size,
                            progress_cb=progress_cb,
                            base=(existing if append else 0),
                            total=(expected_total or 0),
                        )
```

In `download_original` (signature at line 339), add the param and pass progress through both `_stream_to_file` calls. New signature:

```python
    async def download_original(
        self,
        media_id: int,
        dest: Path,
        chunk_size: int = 1024 * 1024,
        *,
        progress_cb: "ProgressCb | None" = None,
    ) -> None:
```

For the re-login branch call (line ~359):

```python
                    await self._stream_to_file(
                        resp2, dest, append=False, chunk_size=chunk_size,
                        progress_cb=progress_cb, base=0,
                        total=(_content_total_bytes(resp2, 0) or 0),
                    )
                    return
```

For the main call (line ~362):

```python
            await self._stream_to_file(
                resp, dest, append=False, chunk_size=chunk_size,
                progress_cb=progress_cb, base=0,
                total=(_content_total_bytes(resp, 0) or 0),
            )
```

Leave `download_thumbnail` unchanged — thumbnails are not part of this feature, and `progress_cb` defaults to `None`.

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv/bin/pytest tests/unit/test_catdv_client_progress.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/catdv_client.py tests/unit/test_catdv_client_progress.py
git commit -m "feat(#78): optional progress callback in catdv_client download path

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Thread `progress_cb` through the resolver + cache backend

**Files:**
- Modify: `backend/app/services/proxy_resolver.py` (Protocol + `RestProxyResolver`, `FilesystemProxyResolver`, `LocalCacheOnlyResolver`, `UploadAwareResolver`)
- Modify: `backend/app/services/media_cache.py` (`MediaCacheBackend` Protocol + `LocalProxyBackend`, `AiStoreBackend`)
- Test: `tests/unit/test_progress_plumbing.py` (create)

**Interfaces:**
- Consumes: `ProgressCb` from `catdv_client` (Task 2).
- Produces:
  - Every `path_for_clip_id(self, clip_id, progress_cb: ProgressCb | None = None)`.
  - Every `ensure_cached(self, clip_id, progress_cb: ProgressCb | None = None)`.
  - The callback reaches `catdv_client.download_proxy/original` when (and only when) a download actually happens.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_progress_plumbing.py`:

```python
"""Issue #78: ensure_cached -> resolver -> download forwards progress_cb."""

import pytest

from backend.app.services.media_cache import LocalProxyBackend


class _RecordingResolver:
    is_host_local = False

    def __init__(self):
        self.seen_cb = "unset"

    async def path_for_clip_id(self, clip_id, progress_cb=None):
        self.seen_cb = progress_cb
        return None  # path unused by this test

    def is_managed(self, path):
        return False


@pytest.mark.asyncio
async def test_local_backend_forwards_progress_cb_to_resolver():
    resolver = _RecordingResolver()
    backend = LocalProxyBackend(resolver=resolver, ai_store=None, gcs=None)

    async def cb(d, t):
        pass

    await backend.ensure_cached(7, progress_cb=cb)
    assert resolver.seen_cb is cb


@pytest.mark.asyncio
async def test_local_backend_default_cb_is_none():
    resolver = _RecordingResolver()
    backend = LocalProxyBackend(resolver=resolver, ai_store=None, gcs=None)
    await backend.ensure_cached(7)
    assert resolver.seen_cb is None
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/pytest tests/unit/test_progress_plumbing.py -v`
Expected: FAIL — `ensure_cached() got an unexpected keyword argument 'progress_cb'`.

- [ ] **Step 3: Implement the plumbing**

In `backend/app/services/proxy_resolver.py`:

Import the type alias near the top (with the other imports). To avoid any import cycle, import it under `TYPE_CHECKING` and reference it as a string:

```python
if TYPE_CHECKING:
    from backend.app.services.catdv_client import ProgressCb
    from backend.app.services.media_store_map import MediaStoreMap
```

Update the **Protocol**:

```python
@runtime_checkable
class ProxyResolver(Protocol):
    is_host_local: bool

    async def path_for_clip_id(
        self, clip_id: int, progress_cb: "ProgressCb | None" = None
    ) -> Path: ...
    def is_managed(self, path: Path) -> bool: ...
```

`RestProxyResolver.path_for_clip_id` — accept the param and forward it into the downloader. Change the signature and the `_dest_and_downloader` call:

```python
    async def path_for_clip_id(
        self, clip_id: int, progress_cb: "ProgressCb | None" = None
    ) -> Path:
        # ... cache-hit block unchanged ...

        dest, download = await self._dest_and_downloader(clip_id, progress_cb)
        if not dest.exists() or dest.stat().st_size == 0:  # sync-io-ok: pre-existing, tracked for the tier-4 async-io pass
            await download()
        # ... record block unchanged ...
        return dest
```

`_dest_and_downloader` — accept the callback and pass it to the download calls:

```python
    async def _dest_and_downloader(
        self, clip_id: int, progress_cb: "ProgressCb | None" = None
    ) -> tuple[Path, Callable[[], Awaitable[None]]]:
        if self._archive is not None:
            # ... unchanged up to the image closure ...
                async def _dl_image() -> None:
                    await self._catdv.download_original(mid, dest, progress_cb=progress_cb)

                return dest, _dl_image

        dest = self._cache_dir / f"{clip_id}.mov"

        async def _dl_video() -> None:
            await self._catdv.download_proxy(clip_id, dest, progress_cb=progress_cb)

        return dest, _dl_video
```

`FilesystemProxyResolver.path_for_clip_id` and `LocalCacheOnlyResolver.path_for_clip_id` — accept and ignore (no download happens in these):

```python
    async def path_for_clip_id(
        self, clip_id: int, progress_cb: "ProgressCb | None" = None
    ) -> Path:
        # progress_cb intentionally unused: these resolvers never download.
        ... existing body unchanged ...
```

`UploadAwareResolver.path_for_clip_id` — accept and forward to inner only on the download path:

```python
    async def path_for_clip_id(
        self, clip_id: int, progress_cb: "ProgressCb | None" = None
    ) -> Path:
        if not is_uploaded(clip_id):
            return await self._inner.path_for_clip_id(clip_id, progress_cb)
        # uploaded clips are served from local cache; no download, cb unused
        ... existing uploaded-clip body unchanged ...
```

In `backend/app/services/media_cache.py`, add the import under `TYPE_CHECKING` (add the block near the top imports):

```python
if TYPE_CHECKING:
    from backend.app.services.catdv_client import ProgressCb
```

Update the **Protocol** and both backends:

```python
class MediaCacheBackend(Protocol):
    async def ensure_cached(
        self, clip_id: int, progress_cb: "ProgressCb | None" = None
    ) -> None: ...

    async def locate(self, clip_id: int) -> LocalFile | RemoteUrl | None: ...
```

`LocalProxyBackend.ensure_cached`:

```python
    async def ensure_cached(
        self, clip_id: int, progress_cb: "ProgressCb | None" = None
    ) -> None:
        await self._resolver.path_for_clip_id(clip_id, progress_cb)
```

`AiStoreBackend.ensure_cached` — forward into the resolver (the GCS upload itself is not part of the download progress):

```python
    async def ensure_cached(
        self, clip_id: int, progress_cb: "ProgressCb | None" = None
    ) -> None:
        key = _clip_key(clip_id)
        if await self._ai_store.status(key) is not None:
            return  # already in GCS -- no tunnel hit (status-first fast-path)

        path: Path = await self._resolver.path_for_clip_id(clip_id, progress_cb)
        # ... rest of the method (upload + cleanup) unchanged ...
```

Add `from __future__ import annotations` is already present in `media_cache.py`; if `TYPE_CHECKING` is not yet imported there, add `from typing import TYPE_CHECKING` to the existing `typing` import line.

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv/bin/pytest tests/unit/test_progress_plumbing.py -v`
Expected: PASS.

- [ ] **Step 5: Run the adjacent suites to confirm no regression**

Run: `.venv/bin/pytest tests/unit/test_media_cache_factory.py tests/unit/test_proxy_resolver_factory.py tests/unit/test_media_route_backend.py -v`
Expected: PASS (signatures are backward-compatible via the default).

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/proxy_resolver.py backend/app/services/media_cache.py tests/unit/test_progress_plumbing.py
git commit -m "feat(#78): thread progress_cb through resolver + media cache backend

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: `MediaPrefetcher` — throttled progress writes + true final size

**Files:**
- Modify: `backend/app/services/media_prefetcher.py`
- Test: `tests/integration/test_media_prefetcher.py` (extend; update fakes), `tests/unit/test_progress_tracker.py` (create)

**Interfaces:**
- Consumes: `PrefetchQueueRepo.update_progress` (Task 1); `ensure_cached(clip_id, progress_cb=...)` (Task 3).
- Produces:
  - `_ProgressTracker` callable: updates in-memory `last_downloaded` / `last_total` on every call; writes to the DB at most once per `min_interval_s` (default `0.75`).
  - `tick_once` passes the tracker as `progress_cb` and calls `mark_done(..., bytes_downloaded=tracker.last_downloaded)`.

- [ ] **Step 1: Write the failing unit test for the throttle**

Create `tests/unit/test_progress_tracker.py`:

```python
"""Issue #78: the prefetcher's progress tracker writes the DB at most once
per interval, but tracks the latest bytes in memory on every call."""

import pytest

from backend.app.services.media_prefetcher import _ProgressTracker


class _SpyRepo:
    def __init__(self):
        self.writes: list[tuple[int, int]] = []

    async def update_progress(self, conn, rid, bytes_downloaded, bytes_total):
        self.writes.append((bytes_downloaded, bytes_total))


@pytest.mark.asyncio
async def test_tracker_throttles_db_writes_but_tracks_latest():
    clock = {"t": 0.0}
    repo = _SpyRepo()
    tracker = _ProgressTracker(
        repo, conn=None, rid=1, min_interval_s=0.75, clock=lambda: clock["t"]
    )

    # First call always writes.
    await tracker(1_000, 10_000)
    # Within the interval: no new write, but latest is tracked.
    clock["t"] = 0.1
    await tracker(2_000, 10_000)
    clock["t"] = 0.5
    await tracker(3_000, 10_000)
    # Past the interval: writes again.
    clock["t"] = 0.9
    await tracker(4_000, 10_000)

    assert repo.writes == [(1_000, 10_000), (4_000, 10_000)]
    assert tracker.last_downloaded == 4_000
    assert tracker.last_total == 10_000
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/pytest tests/unit/test_progress_tracker.py -v`
Expected: FAIL — `cannot import name '_ProgressTracker'`.

- [ ] **Step 3: Implement `_ProgressTracker` and wire it into `tick_once`**

In `backend/app/services/media_prefetcher.py`, add imports at the top:

```python
import time
```

Add the tracker class (after the imports, before `class MediaPrefetcher`):

```python
class _ProgressTracker:
    """Progress callback for one download. Records the latest absolute
    bytes-on-disk in memory on every chunk, but throttles DB writes to at
    most once per `min_interval_s` so a multi-GB stream produces a few dozen
    UPDATEs, not thousands. The first call always writes."""

    def __init__(
        self,
        repo: PrefetchQueueRepo,
        *,
        conn: aiosqlite.Connection,
        rid: int,
        min_interval_s: float = 0.75,
        clock=time.monotonic,
    ) -> None:
        self._repo = repo
        self._conn = conn
        self._rid = rid
        self._min_interval_s = min_interval_s
        self._clock = clock
        self._last_write: float | None = None
        self.last_downloaded = 0
        self.last_total = 0

    async def __call__(self, downloaded: int, total: int) -> None:
        self.last_downloaded = downloaded
        self.last_total = total
        now = self._clock()
        if self._last_write is not None and (now - self._last_write) < self._min_interval_s:
            return
        self._last_write = now
        await self._repo.update_progress(self._conn, self._rid, downloaded, total)
```

Note: the test constructs it positionally as `_ProgressTracker(repo, conn=None, rid=1, ...)`. Keep `repo` positional and the rest keyword-only (the `*` above) so both the test and `tick_once` agree.

Update `tick_once` — replace the `try` body (lines 119-128) with:

```python
        try:
            tracker = _ProgressTracker(self._queue, conn=db, rid=rid)
            await self._backend.ensure_cached(clip_id_int, progress_cb=tracker)
            # Final flush: record the true on-disk size (covers clips that
            # finished inside one throttle window, so Recent never shows 0).
            await self._queue.mark_done(db, rid, bytes_downloaded=tracker.last_downloaded)
        except Exception as exc:  # noqa: BLE001
            msg = humanise(exc)
            log.warning("prefetch failed for clip %s: %s", clip_id_int, msg, exc_info=True)
            await self._queue.mark_error(db, rid, msg)
        return clip_id_int
```

- [ ] **Step 4: Run the unit test to verify it passes**

Run: `.venv/bin/pytest tests/unit/test_progress_tracker.py -v`
Expected: PASS.

- [ ] **Step 5: Update the prefetcher fakes + add an integration test**

The existing fakes in `tests/integration/test_media_prefetcher.py` define `ensure_cached(self, clip_id)`; the prefetcher now calls `ensure_cached(clip_id, progress_cb=...)`. Update every fake's signature to accept it. In `_FakeBackend`:

```python
    async def ensure_cached(self, clip_id: int, progress_cb=None) -> None:
        self.calls.append(clip_id)
        if clip_id in self._fail_on:
            raise RuntimeError(f"boom on {clip_id}")
        if self._sleep_s:
            await asyncio.sleep(self._sleep_s)
```

Apply the same `progress_cb=None` addition to `_TrackingBackend.ensure_cached` and the inline `_TimeoutBackend.ensure_cached` in that file.

Add a new integration test that proves progress is reported and the done row carries the final size:

```python
@pytest.mark.asyncio
async def test_tick_records_progress_and_final_size(db):
    from backend.app.services.media_prefetcher import MediaPrefetcher

    class _ProgressBackend:
        async def ensure_cached(self, clip_id, progress_cb=None):
            # Simulate a streamed download reporting absolute bytes.
            await progress_cb(10_000_000, 27_000_000)
            await progress_cb(27_000_000, 27_000_000)

    repo = PrefetchQueueRepo()
    rid = await repo.enqueue(db, key=("catdv", "5"), who="request")
    pf = MediaPrefetcher(queue_repo=repo, backend=_ProgressBackend(), db_provider=lambda: db)
    await pf.tick_once()

    row = await repo.get(db, rid)
    assert row["status"] == "done"
    assert row["bytes_downloaded"] == 27_000_000  # final flush
    assert row["bytes_total"] == 27_000_000
```

- [ ] **Step 6: Run the prefetcher suite**

Run: `.venv/bin/pytest tests/integration/test_media_prefetcher.py -v`
Expected: PASS (existing tests still green with the updated fakes, plus the new one).

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/media_prefetcher.py tests/unit/test_progress_tracker.py tests/integration/test_media_prefetcher.py
git commit -m "feat(#78): throttled progress writes + true final size in MediaPrefetcher

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Queue page — render percentage + size

**Files:**
- Modify: `backend/app/templates/pages/_cache_queue_active.html:43`
- Test: `tests/unit/test_cache_queue_progress_render.py` (create)

**Interfaces:**
- Consumes: `queue_active` rows now carry `bytes_total` (Task 1).
- Produces: downloading rows with a known total render `NN%   X.X MB`; unknown-total or non-downloading rows render `X.X MB` (unchanged).

- [ ] **Step 1: Write the failing render test**

Create `tests/unit/test_cache_queue_progress_render.py`:

```python
"""Issue #78: the active queue panel shows a percentage for downloading rows
with a known total, and falls back to size-only when the total is unknown."""

from backend.app.routes.pages import templates


def _render(rows) -> str:
    tmpl = templates.env.get_template("pages/_cache_queue_active.html")
    return tmpl.render(queue_active=rows)


def _row(**kw):
    base = {
        "id": 1, "provider_id": "catdv", "provider_clip_id": "5",
        "status": "downloading", "requested_at": "t", "started_at": "t",
        "error": None, "bytes_downloaded": 0, "bytes_total": 0,
        "clip_name": "clip.mov",
    }
    base.update(kw)
    return base


def test_downloading_with_total_shows_percentage():
    html = _render([_row(bytes_downloaded=12_000_000, bytes_total=27_000_000)])
    assert "44%" in html  # 12/27 -> 44
    assert "11.4 MB" in html  # 12_000_000 / 1048576


def test_downloading_without_total_shows_size_only():
    html = _render([_row(bytes_downloaded=5_000_000, bytes_total=0)])
    assert "%" not in html.split("queue-row")[1].split("</tr>")[0]


def test_done_row_unchanged_size_only():
    html = _render([_row(status="done", bytes_downloaded=27_000_000, bytes_total=27_000_000)])
    # done rows are not "downloading" → size only, no percentage cell text
    seg = html.split("queue-row")[1].split("</tr>")[0]
    assert "%" not in seg
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/pytest tests/unit/test_cache_queue_progress_render.py -v`
Expected: FAIL — `44%` not present (template only renders MB today).

- [ ] **Step 3: Update the Size cell**

In `backend/app/templates/pages/_cache_queue_active.html`, replace the Size `<td>` (line 43):

```html
            <td class="num mono">
              {% if r.status == "downloading" and r.bytes_total %}
                {{ (100 * r.bytes_downloaded // r.bytes_total) }}%&nbsp;&nbsp;{{ "%.1f" % (r.bytes_downloaded / 1048576) }} MB
              {% else %}
                {{ "%.1f" % (r.bytes_downloaded / 1048576) }} MB
              {% endif %}
            </td>
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv/bin/pytest tests/unit/test_cache_queue_progress_render.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/templates/pages/_cache_queue_active.html tests/unit/test_cache_queue_progress_render.py
git commit -m "feat(#78): show download percentage + size on the cache queue page

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Clip button — `Caching… (NN%)`

**Files:**
- Modify: `backend/app/static/cacheActions.js:92-116` (`_pollUntilDone`)

**Interfaces:**
- Consumes: `/api/cache/prefetch/queue` rows now carry `bytes_downloaded` + `bytes_total` (Task 1).
- Produces: while a clip is `downloading` with a known total, `busyLabel` becomes `Caching… (NN%)`; the button already renders `busyLabel`.

There is no JS test harness in this repo, so this task is verified by the manual acceptance flow below. Keep the change minimal and behind the same field guards used server-side (`bytes_total` truthy).

- [ ] **Step 1: Update `_pollUntilDone` to set the percentage label**

In `backend/app/static/cacheActions.js`, inside `_pollUntilDone`, after the `if (!mine) continue;` line and before the `if (mine.status === 'done') return;` line, add:

```javascript
        if (mine.status === 'downloading' && mine.bytes_total > 0) {
          const pct = Math.floor((100 * mine.bytes_downloaded) / mine.bytes_total);
          this.busyLabel = `Caching… (${pct}%)`;
        }
```

(Leave the `done` / `error` / `cancelled` branches unchanged. When `bytes_total` is 0 the label stays the plain `Caching…` set in `cacheNow()`.)

- [ ] **Step 2: Manual verification (no automated JS test)**

Start the dev server (use the `server-start` skill / discipline), open a clip whose proxy is several hundred MB and not yet cached, click **Cache**, and confirm the button cycles `Caching…` → `Caching… (NN%)` climbing to 100%, then flips to the cached/Purge state. Hard-refresh first to bypass the cached `cacheActions.js`.

- [ ] **Step 3: Commit**

```bash
git add backend/app/static/cacheActions.js
git commit -m "feat(#78): show caching percentage on the clip-page cache button

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Full-suite gate + guard checks

**Files:** none (verification only).

- [ ] **Step 1: Run the affected suites + guards**

```bash
.venv/bin/pytest tests/unit tests/integration -q
.venv/bin/python -m pytest tests/unit/test_no_sync_fs_in_async.py -q
```

Expected: all PASS. (No new unmarked sync-fs-in-async was introduced; the only new `async def`s are the tracker and the callback, which do DB I/O via the repo, not filesystem.)

- [ ] **Step 2: Lint imports (catch any accidental cycle / forbidden import)**

```bash
.venv/bin/lint-imports 2>/dev/null || echo "lint-imports not available; skip"
```

Expected: PASS, or skipped if the tool is absent. The `ProgressCb` imports are under `TYPE_CHECKING`, so they add no runtime edge.

- [ ] **Step 3: Run the manual acceptance flows from the spec**

Walk the four flows in `docs/superpowers/specs/2026-06-22-cache-queue-progress-design.md` ("Manual acceptance flows") against a running dev server. Tick each or report the failing step.

---

## Self-Review

**1. Spec coverage**
- "% in parens on the clip button" → Task 6. ✓
- "% + downloaded size on the queue page" → Task 5. ✓
- "text-only, over existing polling" → no new endpoint/SSE anywhere; both surfaces use their current polls. ✓
- Data model (`bytes_total`, `0 = unknown`) → Task 1. ✓
- Progress callback threaded MediaPrefetcher→backend→resolver→client→`_stream_to_file`, absolute bytes, default `None` → Tasks 2–4. ✓ (Spec's path diagram omitted the `MediaCacheBackend` link; Task 3 covers it — the only deviation from the spec's wording, and a necessary one since `MediaPrefetcher` calls `backend.ensure_cached`, not the resolver directly.)
- Throttle ~750 ms, no per-chunk write → Task 4 (`_ProgressTracker`, unit-tested). ✓
- `mark_done` shows final size → Task 4 final flush via `tracker.last_downloaded`; `bytes_total` persists from the last `update_progress`. ✓
- Edge: unknown total → Tasks 5/6 guard on `bytes_total` truthy; no `NaN%`. ✓
- Edge: resume reports absolute bytes → Task 2 `base=existing` on append. ✓
- Edge: restart/orphan resets progress → Task 1 `requeue_orphans`. ✓
- Edge: image originals → Task 2 `download_original` plumbed. ✓
- Existing playback untouched → default `None` everywhere; Task 3 Step 5 regression run. ✓

**2. Placeholder scan:** none — every step has concrete code/commands.

**3. Type consistency:** `ProgressCb = Callable[[int, int], Awaitable[None]]` defined once in `catdv_client` (Task 2), imported under `TYPE_CHECKING` in `proxy_resolver` and `media_cache` (Task 3). `update_progress(conn, rid, bytes_downloaded, bytes_total)` defined in Task 1, called identically by `_ProgressTracker` in Task 4. `_ProgressTracker(repo, *, conn, rid, min_interval_s, clock)` — test (Task 4 Step 1) and `tick_once` (Step 3) both construct it as `repo` positional + keyword rest. Row dict key `bytes_total` consistent across repo (Task 1), prefetcher test, template (Task 5), and JS (Task 6).
