# PR 8: Media prefetch queue + cache UI wiring — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the user warm the local proxy cache from the UI — pick one or many clips, watch them download one-at-a-time in the background (so the WireGuard pipe doesn't melt), and see at a glance from the clips list and the clip-detail page whether each clip is already cached. Add a queue panel to the existing `/cache` page so the user can inspect, cancel, or retry prefetches and still evict files the way PR 6 already allows.

**Architecture:** A new persistent SQLite queue (`prefetch_queue`) plus a single `MediaPrefetcher` background service in the same shape as `LruEviction` (`start()` / `stop()` / `tick_once()`). The worker pulls one `queued` row at a time, calls the existing `RestProxyResolver.path_for_clip_id` (which downloads via `CatdvClient.download_proxy`), and on success records the file into `proxy_cache` so `CacheInspector` can see it. A small change wraps `RestProxyResolver` so the on-demand `/api/media/{id}` path also writes to `proxy_cache` — this is a latent bug the prefetcher would otherwise paper over. The UI MVP pages (`pages/clips.html`, `pages/clip_detail.html`) gain a server-rendered cache badge column (no per-row HTMX fan-out — we bulk-fetch via `CacheInspector.status_for_clips`), checkboxes + a bulk toolbar on the list, and a "Cache video" / "Evict" pair on the detail page. The existing `/cache` page gets a "Queue" panel that lists prefetcher state and exposes cancel/retry. No new HTTP surface beyond `/api/cache/prefetch*` JSON routes; the UI uses HTMX for partial swaps.

**Tech Stack:** Python 3.12, FastAPI, `asyncio`, `aiosqlite`, frozen dataclasses, Jinja2, HTMX, `pytest` + `pytest-asyncio`. No new third-party deps. Database changes go in a single new migration `0007_prefetch_queue.sql`.

**Scope guardrail:** This plan does NOT introduce eviction rule changes, AI-store prefetching, or any concurrency above one download. It does NOT change the existing `cache_actions_log` schema, the `proxy_cache` schema, or the `/cache` page's eviction flow. It does NOT add prefetch of `metadata` or `media-ai` layers — only `media-local` (the only one that actually traverses the slow VPN). It does NOT introduce a JS framework — the bulk toolbar uses plain HTMX + a 10-line Alpine controller for the selection set.

**Decisions to record in `docs/decisions.md`** (Task 11):

1. **Prefetch is a persistent SQLite queue, not in-memory.** A clean shutdown or a server restart must not lose a long-running pre-flight (a 300 MB clip is ~12 min of VPN time and the user may close their laptop lid). The queue table doubles as the data source for the `/cache?tab=queue` panel.
2. **Single-flight serialization is enforced at the worker, not the resolver.** The `RestProxyResolver` stays request-driven and synchronous; the prefetcher runs at most one `tick_once()` body at a time and processes one row at a time. This way the on-demand `/api/media/{id}` request — when a user is *watching* — still goes through with no extra queueing. If a user opens the video for a clip already being prefetched, the resolver's existing "file exists, skip download" check covers de-dup once the file lands; if the prefetch is in progress the user-facing GET will pile on behind it (acceptable: the prefetcher started first, the user benefits from the warm file).
3. **`RestProxyResolver` learns to record into `proxy_cache`.** Today it doesn't, which is a latent bug — `CacheInspector` reports `media-local: absent` even when the file exists on disk. We fix it in this PR rather than letting the prefetcher be the only writer.
4. **Cancellation is honored for `queued` and `error` rows only.** A `downloading` row cannot be cancelled mid-stream (we don't want partial files on disk for a `curl -C -` resume that will never happen); the user is told to wait for the current file to finish. The worker still honors a `stop()` signal between rows.
5. **The cache badge is rendered server-side, in-line with the list, not via per-row HTMX.** Bulk lookup uses `CacheInspector.status_for_clips([keys])` once per page load. The existing `/ui/cache-badge/...` HTMX route stays — it's still the refresh path after a per-clip evict — but it's no longer the primary render path.
6. **No new field on `proxy_cache`.** The prefetch queue's status is the queue table's job; once a file lands it's recorded in `proxy_cache` like any other download, and the queue row goes to `done`. The two tables are joined by `(provider_id, provider_clip_id)` on display.

---

## File map

### New files

| Path | Responsibility |
|---|---|
| `backend/migrations/0007_prefetch_queue.sql` | Creates `prefetch_queue` table + indexes. |
| `backend/app/repositories/prefetch_queue.py` | `PrefetchQueueRepo`: `enqueue`, `claim_next`, `mark_done`, `mark_error`, `mark_cancelled`, `list_recent`, `list_active`, `count_by_status`. |
| `backend/app/services/media_prefetcher.py` | `MediaPrefetcher` background task: `start()`, `stop()`, `tick_once()`. |
| `backend/app/templates/pages/_cache_badge.html` | Server-rendered, no-HTMX variant of the badge used inside clip rows and the detail header. |
| `backend/app/templates/pages/_prefetch_panel.html` | HTMX partial for the queue panel on `/cache`. |
| `tests/integration/test_migration_0007.py` | Schema shape assertions. |
| `tests/integration/test_prefetch_queue_repo.py` | Repo unit-ish tests against a real SQLite. |
| `tests/integration/test_media_prefetcher.py` | Serial-drain, single-flight, error, cancel-between-rows. |
| `tests/integration/test_routes_prefetch.py` | JSON enqueue / list / cancel routes. |
| `tests/integration/test_routes_pages_cache_badge.py` | Cache badge appears in list + detail. |

### Modified files

| Path | Change |
|---|---|
| `backend/app/settings.py` | Add `prefetch_tick_interval_s: int = 2`. |
| `backend/migrations/0001_initial.sql` | Untouched — new migration only. |
| `backend/app/services/proxy_resolver.py` | `RestProxyResolver` records into `proxy_cache` after a successful download (Task 3). |
| `backend/app/context.py` | Wire `prefetch_queue_repo`, `media_prefetcher`; pass `proxy_cache_repo` + `db_provider` into `build_resolver`. |
| `backend/app/main.py` | Start `media_prefetcher` if `init_external`. |
| `backend/app/routes/cache.py` | New JSON endpoints `POST /api/cache/prefetch`, `GET /api/cache/prefetch/queue`, `POST /api/cache/prefetch/{id}/cancel`; mount `pages/_prefetch_panel.html` under a new `GET /ui/cache/queue` HTMX partial. |
| `backend/app/routes/pages.py` | Inject per-clip cache status into the list + detail view-models. |
| `backend/app/ui/view_models.py` | `clip_summary` accepts an optional `cache_status` arg; new `cache_status_view(status)` helper. |
| `backend/app/templates/pages/clips.html` | Bulk toolbar (select-all, prefetch selected, evict selected). |
| `backend/app/templates/pages/_clips_tbody.html` | Checkbox column + badge column. |
| `backend/app/templates/pages/clip_detail.html` | Badge + "Cache video" / "Evict local" button pair in the header. |
| `backend/app/templates/cache_page.html` | Add "Queue" panel that includes `pages/_prefetch_panel.html`. |
| `backend/app/static/app.css` | Styles for `.cache-badge`, `.cache-actions`, `.row-select`, `.bulk-toolbar`, `.prefetch-row`. |
| `tests/integration/test_initial_schema.py` | `EXPECTED_TABLES` gains `"prefetch_queue"`. |
| `docs/decisions.md` | Append the six decisions above. |

### Deleted files

None.

---

## Tasks

### Task 1: Migration 0007 — `prefetch_queue`

**Files:**
- Create: `backend/migrations/0007_prefetch_queue.sql`
- Create: `tests/integration/test_migration_0007.py`
- Modify: `tests/integration/test_initial_schema.py`

- [ ] **Step 1: Write the failing migration shape test**

`tests/integration/test_migration_0007.py`:

```python
import pytest


@pytest.mark.asyncio
async def test_prefetch_queue_columns(db):
    cur = await db.execute("PRAGMA table_info(prefetch_queue)")
    cols = {row[1]: row[2] for row in await cur.fetchall()}
    assert cols == {
        "id":                "INTEGER",
        "provider_id":       "TEXT",
        "provider_clip_id":  "TEXT",
        "status":            "TEXT",
        "requested_by":      "TEXT",
        "requested_at":      "TEXT",
        "started_at":        "TEXT",
        "finished_at":       "TEXT",
        "error":             "TEXT",
        "bytes_downloaded":  "INTEGER",
    }


@pytest.mark.asyncio
async def test_prefetch_queue_indexes(db):
    cur = await db.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='index' AND tbl_name='prefetch_queue'"
    )
    names = {row[0] for row in await cur.fetchall()}
    assert "idx_prefetch_queue_status_requested_at" in names
    assert "idx_prefetch_queue_clip_status" in names
```

- [ ] **Step 2: Run it and confirm it fails**

```
.venv/bin/python -m pytest tests/integration/test_migration_0007.py -v
```

Expected: FAIL — `prefetch_queue` table does not exist.

- [ ] **Step 3: Write the migration**

`backend/migrations/0007_prefetch_queue.sql`:

```sql
-- PR 8: persistent prefetch queue for proxy downloads.
--
-- One row per requested prefetch. The MediaPrefetcher background worker
-- claims rows in `requested_at` order, status='queued' → 'downloading' →
-- 'done' | 'error'. The user may cancel queued/error rows from the
-- /cache page; cancellation of an in-flight row is not supported (see
-- decision 4 in the plan).

CREATE TABLE prefetch_queue (
  id                INTEGER PRIMARY KEY,
  provider_id       TEXT NOT NULL,
  provider_clip_id  TEXT NOT NULL,
  status            TEXT NOT NULL,            -- queued|downloading|done|error|cancelled
  requested_by      TEXT NOT NULL,            -- "request" today; future user id
  requested_at      TEXT NOT NULL,
  started_at        TEXT,
  finished_at       TEXT,
  error             TEXT,
  bytes_downloaded  INTEGER NOT NULL DEFAULT 0
);

-- Worker drains by (status, requested_at).
CREATE INDEX idx_prefetch_queue_status_requested_at
  ON prefetch_queue(status, requested_at);

-- Enqueue de-dup check: do we already have a non-terminal row for this clip?
CREATE INDEX idx_prefetch_queue_clip_status
  ON prefetch_queue(provider_id, provider_clip_id, status);
```

- [ ] **Step 4: Update `EXPECTED_TABLES`**

In `tests/integration/test_initial_schema.py` find the `EXPECTED_TABLES` set and add `"prefetch_queue"`. If the test is structured as a list/tuple, match its style; example diff:

```python
EXPECTED_TABLES = {
    "annotations", "review_items", ..., "cache_actions_log",
    "prefetch_queue",   # PR 8
}
```

- [ ] **Step 5: Run all three tests**

```
.venv/bin/python -m pytest tests/integration/test_migration_0007.py tests/integration/test_initial_schema.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/migrations/0007_prefetch_queue.sql \
        tests/integration/test_migration_0007.py \
        tests/integration/test_initial_schema.py
git commit -m "feat(cache): add prefetch_queue migration"
```

---

### Task 2: `PrefetchQueueRepo`

**Files:**
- Create: `backend/app/repositories/prefetch_queue.py`
- Create: `tests/integration/test_prefetch_queue_repo.py`

- [ ] **Step 1: Write failing tests**

`tests/integration/test_prefetch_queue_repo.py`:

```python
import pytest

from backend.app.repositories.prefetch_queue import PrefetchQueueRepo


@pytest.mark.asyncio
async def test_enqueue_returns_id_and_idempotent(db):
    repo = PrefetchQueueRepo()
    a = await repo.enqueue(db, key=("catdv", "1"), who="request")
    assert isinstance(a, int) and a > 0
    # A second enqueue for the same clip while still active returns the
    # existing row id (no duplicate work).
    b = await repo.enqueue(db, key=("catdv", "1"), who="request")
    assert b == a


@pytest.mark.asyncio
async def test_claim_next_is_fifo_and_atomic(db):
    repo = PrefetchQueueRepo()
    id1 = await repo.enqueue(db, key=("catdv", "1"), who="request")
    id2 = await repo.enqueue(db, key=("catdv", "2"), who="request")
    claimed_a = await repo.claim_next(db)
    claimed_b = await repo.claim_next(db)
    assert claimed_a["id"] == id1 and claimed_a["status"] == "downloading"
    assert claimed_b["id"] == id2
    # No more queued rows
    assert await repo.claim_next(db) is None


@pytest.mark.asyncio
async def test_mark_done_records_bytes_and_finished_at(db):
    repo = PrefetchQueueRepo()
    rid = await repo.enqueue(db, key=("catdv", "1"), who="request")
    await repo.claim_next(db)
    await repo.mark_done(db, rid, bytes_downloaded=12345)
    row = await repo.get(db, rid)
    assert row["status"] == "done"
    assert row["bytes_downloaded"] == 12345
    assert row["finished_at"] is not None


@pytest.mark.asyncio
async def test_mark_error_stores_message(db):
    repo = PrefetchQueueRepo()
    rid = await repo.enqueue(db, key=("catdv", "1"), who="request")
    await repo.claim_next(db)
    await repo.mark_error(db, rid, "VPN timeout after 1800s")
    row = await repo.get(db, rid)
    assert row["status"] == "error"
    assert row["error"] == "VPN timeout after 1800s"


@pytest.mark.asyncio
async def test_cancel_blocks_downloading(db):
    repo = PrefetchQueueRepo()
    rid = await repo.enqueue(db, key=("catdv", "1"), who="request")
    await repo.claim_next(db)
    out = await repo.mark_cancelled(db, rid)
    assert out is False, "cancel must reject downloading rows"
    row = await repo.get(db, rid)
    assert row["status"] == "downloading"


@pytest.mark.asyncio
async def test_cancel_succeeds_for_queued(db):
    repo = PrefetchQueueRepo()
    rid = await repo.enqueue(db, key=("catdv", "1"), who="request")
    out = await repo.mark_cancelled(db, rid)
    assert out is True
    row = await repo.get(db, rid)
    assert row["status"] == "cancelled"


@pytest.mark.asyncio
async def test_count_by_status(db):
    repo = PrefetchQueueRepo()
    await repo.enqueue(db, key=("catdv", "1"), who="request")
    await repo.enqueue(db, key=("catdv", "2"), who="request")
    rid3 = await repo.enqueue(db, key=("catdv", "3"), who="request")
    await repo.claim_next(db)  # 1 -> downloading
    await repo.mark_cancelled(db, rid3)
    counts = await repo.count_by_status(db)
    assert counts == {"downloading": 1, "queued": 1, "cancelled": 1}


@pytest.mark.asyncio
async def test_list_active_excludes_terminal(db):
    repo = PrefetchQueueRepo()
    a = await repo.enqueue(db, key=("catdv", "1"), who="request")
    b = await repo.enqueue(db, key=("catdv", "2"), who="request")
    await repo.claim_next(db)
    await repo.mark_done(db, a, bytes_downloaded=1)
    rows = await repo.list_active(db)
    assert [r["id"] for r in rows] == [b]
```

- [ ] **Step 2: Run them to confirm they fail**

```
.venv/bin/python -m pytest tests/integration/test_prefetch_queue_repo.py -v
```

Expected: FAIL — `prefetch_queue` module not found.

- [ ] **Step 3: Implement the repo**

`backend/app/repositories/prefetch_queue.py`:

```python
from datetime import datetime, timezone
from typing import Any

import aiosqlite

from backend.app.archive.model import ClipKey

ACTIVE_STATUSES = ("queued", "downloading")
TERMINAL_STATUSES = ("done", "error", "cancelled")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_dict(row) -> dict[str, Any]:
    keys = (
        "id", "provider_id", "provider_clip_id", "status",
        "requested_by", "requested_at", "started_at", "finished_at",
        "error", "bytes_downloaded",
    )
    return dict(zip(keys, row))


class PrefetchQueueRepo:
    async def enqueue(
        self,
        conn: aiosqlite.Connection,
        *,
        key: ClipKey,
        who: str,
    ) -> int:
        """Enqueue a prefetch. If an active row already exists for the
        clip, return its id (idempotent)."""
        cur = await conn.execute(
            """
            SELECT id FROM prefetch_queue
             WHERE provider_id = ? AND provider_clip_id = ?
               AND status IN ('queued', 'downloading')
             LIMIT 1
            """,
            (key[0], key[1]),
        )
        existing = await cur.fetchone()
        if existing is not None:
            return int(existing[0])
        cur = await conn.execute(
            """
            INSERT INTO prefetch_queue
              (provider_id, provider_clip_id, status,
               requested_by, requested_at, bytes_downloaded)
            VALUES (?, ?, 'queued', ?, ?, 0)
            """,
            (key[0], key[1], who, _now_iso()),
        )
        await conn.commit()
        assert cur.lastrowid is not None
        return int(cur.lastrowid)

    async def claim_next(
        self, conn: aiosqlite.Connection
    ) -> dict[str, Any] | None:
        """Atomically take the oldest queued row and mark it downloading.

        Returns the claimed row (with status now `downloading`) or None
        if the queue is empty.
        """
        await conn.execute("BEGIN IMMEDIATE")
        try:
            cur = await conn.execute(
                """
                SELECT id, provider_id, provider_clip_id, status,
                       requested_by, requested_at, started_at, finished_at,
                       error, bytes_downloaded
                  FROM prefetch_queue
                 WHERE status = 'queued'
                 ORDER BY requested_at ASC
                 LIMIT 1
                """
            )
            row = await cur.fetchone()
            if row is None:
                await conn.commit()
                return None
            rid = int(row[0])
            now = _now_iso()
            await conn.execute(
                "UPDATE prefetch_queue "
                "   SET status='downloading', started_at=? "
                " WHERE id=? AND status='queued'",
                (now, rid),
            )
            await conn.commit()
        except Exception:
            await conn.rollback()
            raise
        # Re-read so callers see the updated status/started_at.
        return await self.get(conn, rid)

    async def get(
        self, conn: aiosqlite.Connection, rid: int
    ) -> dict[str, Any] | None:
        cur = await conn.execute(
            """
            SELECT id, provider_id, provider_clip_id, status,
                   requested_by, requested_at, started_at, finished_at,
                   error, bytes_downloaded
              FROM prefetch_queue WHERE id = ?
            """,
            (rid,),
        )
        row = await cur.fetchone()
        return _row_to_dict(row) if row is not None else None

    async def mark_done(
        self,
        conn: aiosqlite.Connection,
        rid: int,
        *,
        bytes_downloaded: int,
    ) -> None:
        await conn.execute(
            "UPDATE prefetch_queue "
            "   SET status='done', finished_at=?, bytes_downloaded=? "
            " WHERE id=?",
            (_now_iso(), int(bytes_downloaded), rid),
        )
        await conn.commit()

    async def mark_error(
        self,
        conn: aiosqlite.Connection,
        rid: int,
        message: str,
    ) -> None:
        await conn.execute(
            "UPDATE prefetch_queue "
            "   SET status='error', finished_at=?, error=? "
            " WHERE id=?",
            (_now_iso(), message[:500], rid),
        )
        await conn.commit()

    async def mark_cancelled(
        self, conn: aiosqlite.Connection, rid: int
    ) -> bool:
        """Cancel a queued/error row. Returns False (without mutating)
        if the row is `downloading` or already terminal."""
        cur = await conn.execute(
            "UPDATE prefetch_queue "
            "   SET status='cancelled', finished_at=? "
            " WHERE id=? AND status IN ('queued', 'error')",
            (_now_iso(), rid),
        )
        await conn.commit()
        return cur.rowcount > 0

    async def list_active(
        self, conn: aiosqlite.Connection
    ) -> list[dict[str, Any]]:
        cur = await conn.execute(
            """
            SELECT id, provider_id, provider_clip_id, status,
                   requested_by, requested_at, started_at, finished_at,
                   error, bytes_downloaded
              FROM prefetch_queue
             WHERE status IN ('queued', 'downloading')
             ORDER BY requested_at ASC
            """
        )
        return [_row_to_dict(r) for r in await cur.fetchall()]

    async def list_recent(
        self,
        conn: aiosqlite.Connection,
        *,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        cur = await conn.execute(
            """
            SELECT id, provider_id, provider_clip_id, status,
                   requested_by, requested_at, started_at, finished_at,
                   error, bytes_downloaded
              FROM prefetch_queue
             ORDER BY requested_at DESC
             LIMIT ?
            """,
            (limit,),
        )
        return [_row_to_dict(r) for r in await cur.fetchall()]

    async def count_by_status(
        self, conn: aiosqlite.Connection
    ) -> dict[str, int]:
        cur = await conn.execute(
            "SELECT status, COUNT(*) FROM prefetch_queue GROUP BY status"
        )
        return {row[0]: int(row[1]) for row in await cur.fetchall()}
```

- [ ] **Step 4: Run tests until green**

```
.venv/bin/python -m pytest tests/integration/test_prefetch_queue_repo.py -v
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/repositories/prefetch_queue.py \
        tests/integration/test_prefetch_queue_repo.py
git commit -m "feat(cache): PrefetchQueueRepo"
```

---

### Task 3: `RestProxyResolver` records into `proxy_cache`

This fixes the latent bug discovered while writing the plan: today the on-demand `/api/media/{id}` download path leaves `proxy_cache` empty, so `CacheInspector.status_for_clip` reports `media-local: absent` even for files that exist on disk.

**Files:**
- Modify: `backend/app/services/proxy_resolver.py`
- Modify: `backend/app/context.py`
- Create: `tests/integration/test_rest_proxy_resolver_records.py`

- [ ] **Step 1: Write the failing test**

`tests/integration/test_rest_proxy_resolver_records.py`:

```python
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from backend.app.repositories.proxy_cache import ProxyCacheRepo
from backend.app.services.proxy_resolver import RestProxyResolver


class _FakeCatdv:
    """Minimal CatDV client stub: writes a 9-byte file."""
    def __init__(self):
        self.calls = []

    async def download_proxy(self, clip_id: int, dest: Path) -> None:
        self.calls.append((clip_id, dest))
        dest.write_bytes(b"PROXY-OK!")


@pytest.mark.asyncio
async def test_resolver_records_after_download(db, tmp_path):
    repo = ProxyCacheRepo()
    catdv = _FakeCatdv()
    resolver = RestProxyResolver(
        catdv=catdv,
        cache_dir=tmp_path / "cache",
        proxy_cache_repo=repo,
        db_provider=lambda: db,
    )
    path = await resolver.path_for_clip_id(1234)

    assert path.exists() and path.read_bytes() == b"PROXY-OK!"
    row = await repo.get(db, 1234)
    assert row is not None
    assert row["size_bytes"] == 9
    assert row["file_path"] == str(path)


@pytest.mark.asyncio
async def test_resolver_does_not_redownload_or_redouble_record(db, tmp_path):
    repo = ProxyCacheRepo()
    catdv = _FakeCatdv()
    resolver = RestProxyResolver(
        catdv=catdv,
        cache_dir=tmp_path / "cache",
        proxy_cache_repo=repo,
        db_provider=lambda: db,
    )
    await resolver.path_for_clip_id(1234)
    await resolver.path_for_clip_id(1234)
    assert len(catdv.calls) == 1   # cache hit on second call
```

- [ ] **Step 2: Run, confirm failure**

```
.venv/bin/python -m pytest tests/integration/test_rest_proxy_resolver_records.py -v
```

Expected: FAIL — `RestProxyResolver.__init__` doesn't accept `proxy_cache_repo`/`db_provider`.

- [ ] **Step 3: Update `RestProxyResolver`**

Edit `backend/app/services/proxy_resolver.py` to make this part the new `RestProxyResolver` (keep the rest of the file unchanged):

```python
from collections.abc import Callable

import aiosqlite

from backend.app.repositories.proxy_cache import ProxyCacheRepo


class RestProxyResolver:
    """Downloads proxies via CatDV REST and caches them on local disk.

    After a successful download, records the file into `proxy_cache` so
    `CacheInspector` and friends see it.
    """

    def __init__(
        self,
        catdv,
        cache_dir: Path,
        *,
        proxy_cache_repo: ProxyCacheRepo | None = None,
        db_provider: Callable[[], aiosqlite.Connection] | None = None,
    ) -> None:
        self._catdv = catdv
        self._cache_dir = cache_dir
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._repo = proxy_cache_repo
        self._db_provider = db_provider

    async def path_for_clip_id(self, clip_id: int) -> Path:
        dest = self._cache_dir / f"{clip_id}.mov"
        downloaded_now = False
        if not dest.exists() or dest.stat().st_size == 0:
            await self._catdv.download_proxy(clip_id, dest)
            downloaded_now = True
        if downloaded_now and self._repo is not None and self._db_provider is not None:
            await self._repo.record(
                self._db_provider(),
                clip_id=clip_id,
                file_path=str(dest),
                size_bytes=dest.stat().st_size,
                etag=None,
            )
        return dest

    def is_managed(self, path: Path) -> bool:
        try:
            path.resolve().relative_to(self._cache_dir.resolve())
        except ValueError:
            return False
        return True
```

Also extend `build_resolver` to accept and forward these:

```python
def build_resolver(
    *,
    source: str,
    catdv_client,
    cache_dir: Path | None,
    fs_root: Path | None,
    path_template: str | None,
    proxy_cache_repo: ProxyCacheRepo | None = None,
    db_provider: Callable[[], aiosqlite.Connection] | None = None,
) -> ProxyResolver:
    if source == "rest":
        if cache_dir is None or catdv_client is None:
            raise ValueError("rest source requires catdv_client and cache_dir")
        return RestProxyResolver(
            catdv=catdv_client,
            cache_dir=cache_dir,
            proxy_cache_repo=proxy_cache_repo,
            db_provider=db_provider,
        )
    if source == "filesystem":
        if fs_root is None:
            raise ValueError("filesystem source requires fs_root")
        return FilesystemProxyResolver(
            root=fs_root,
            path_template=path_template or "{root}/{clip_id}.mov",
        )
    raise ValueError(f"unknown PROXY_SOURCE: {source!r}")
```

- [ ] **Step 4: Update `AppContext.build` to pass the new args**

In `backend/app/context.py`, change the existing `build_resolver(...)` call (under `if use_catdv:`) to:

```python
ctx.proxy_resolver = build_resolver(
    source=settings.proxy_source,
    catdv_client=ctx.catdv,
    cache_dir=settings.data_dir / "cache" / "proxies",
    fs_root=settings.proxy_fs_root,
    path_template=settings.proxy_path_template,
    proxy_cache_repo=ctx.proxy_cache_repo,
    db_provider=lambda c=ctx: c.db,
)
```

- [ ] **Step 5: Run the new test and the full proxy-resolver test file**

```
.venv/bin/python -m pytest tests/integration/test_rest_proxy_resolver_records.py -v
.venv/bin/python -m pytest tests/integration/test_proxy_cache_repo.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/proxy_resolver.py backend/app/context.py \
        tests/integration/test_rest_proxy_resolver_records.py
git commit -m "fix(cache): RestProxyResolver records into proxy_cache after download"
```

---

### Task 4: `MediaPrefetcher` background service

**Files:**
- Create: `backend/app/services/media_prefetcher.py`
- Create: `tests/integration/test_media_prefetcher.py`

- [ ] **Step 1: Write the failing tests**

`tests/integration/test_media_prefetcher.py`:

```python
import asyncio
from pathlib import Path

import pytest

from backend.app.repositories.prefetch_queue import PrefetchQueueRepo


class _FakeResolver:
    def __init__(self, sleep_s: float = 0.0, fail_on: set[int] | None = None):
        self._sleep_s = sleep_s
        self._fail_on = fail_on or set()
        self.calls: list[int] = []
        self.cache_dir = Path("/tmp/_fake_cache_will_be_overridden_per_test")

    async def path_for_clip_id(self, clip_id: int) -> Path:
        self.calls.append(clip_id)
        if clip_id in self._fail_on:
            raise RuntimeError(f"boom on {clip_id}")
        if self._sleep_s:
            await asyncio.sleep(self._sleep_s)
        return Path(f"/tmp/{clip_id}.mov")


@pytest.mark.asyncio
async def test_tick_drains_in_order(db):
    from backend.app.services.media_prefetcher import MediaPrefetcher

    repo = PrefetchQueueRepo()
    await repo.enqueue(db, key=("catdv", "1"), who="request")
    await repo.enqueue(db, key=("catdv", "2"), who="request")
    resolver = _FakeResolver()
    pf = MediaPrefetcher(
        queue_repo=repo,
        resolver=resolver,
        db_provider=lambda: db,
    )
    a = await pf.tick_once()
    b = await pf.tick_once()
    c = await pf.tick_once()  # empty
    assert a == 1 and b == 2 and c is None
    assert resolver.calls == [1, 2]
    counts = await repo.count_by_status(db)
    assert counts.get("done") == 2


@pytest.mark.asyncio
async def test_tick_records_error_does_not_block_queue(db):
    from backend.app.services.media_prefetcher import MediaPrefetcher

    repo = PrefetchQueueRepo()
    await repo.enqueue(db, key=("catdv", "1"), who="request")
    await repo.enqueue(db, key=("catdv", "2"), who="request")
    resolver = _FakeResolver(fail_on={1})
    pf = MediaPrefetcher(
        queue_repo=repo,
        resolver=resolver,
        db_provider=lambda: db,
    )
    await pf.tick_once()
    await pf.tick_once()
    counts = await repo.count_by_status(db)
    assert counts.get("error") == 1
    assert counts.get("done") == 1


@pytest.mark.asyncio
async def test_loop_processes_one_at_a_time(db):
    """Two slow downloads must not overlap."""
    from backend.app.services.media_prefetcher import MediaPrefetcher

    repo = PrefetchQueueRepo()
    await repo.enqueue(db, key=("catdv", "1"), who="request")
    await repo.enqueue(db, key=("catdv", "2"), who="request")

    overlap = {"max_concurrent": 0, "current": 0}

    class _Tracking(_FakeResolver):
        async def path_for_clip_id(self, clip_id):
            overlap["current"] += 1
            overlap["max_concurrent"] = max(
                overlap["max_concurrent"], overlap["current"],
            )
            await asyncio.sleep(0.05)
            overlap["current"] -= 1
            return Path("/tmp/x.mov")

    pf = MediaPrefetcher(
        queue_repo=repo,
        resolver=_Tracking(),
        db_provider=lambda: db,
        tick_interval_s=0.01,
    )
    await pf.start()
    # Allow both rows to drain
    for _ in range(50):
        if (await repo.count_by_status(db)).get("done") == 2:
            break
        await asyncio.sleep(0.05)
    await pf.stop()
    assert overlap["max_concurrent"] == 1


@pytest.mark.asyncio
async def test_stop_returns_promptly_between_rows(db):
    from backend.app.services.media_prefetcher import MediaPrefetcher

    repo = PrefetchQueueRepo()
    resolver = _FakeResolver()
    pf = MediaPrefetcher(
        queue_repo=repo,
        resolver=resolver,
        db_provider=lambda: db,
        tick_interval_s=0.01,
    )
    await pf.start()
    await asyncio.sleep(0.05)
    await pf.stop()  # must not hang
```

- [ ] **Step 2: Run, confirm failure**

```
.venv/bin/python -m pytest tests/integration/test_media_prefetcher.py -v
```

Expected: FAIL — `media_prefetcher` module not found.

- [ ] **Step 3: Implement the service**

`backend/app/services/media_prefetcher.py`:

```python
"""MediaPrefetcher: one-at-a-time background download worker.

Drains `prefetch_queue` in FIFO order. Each row is processed by calling
`resolver.path_for_clip_id(int(clip_id))` — the same call the on-demand
`/api/media/{id}` route makes. The resolver is in charge of de-dup
(file exists on disk → skip download) and of recording the result into
`proxy_cache`; the prefetcher just sequences the work and records the
queue-row outcome.

Designed for the WireGuard pipe to Pragafilm: only one row is
in-flight at a time, by construction (a single coroutine + sequential
`tick_once()` calls). If a future deployment can tolerate parallelism,
that's a new service — don't add a semaphore knob here.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

import aiosqlite

from backend.app.repositories.prefetch_queue import PrefetchQueueRepo

log = logging.getLogger(__name__)


class MediaPrefetcher:
    def __init__(
        self,
        *,
        queue_repo: PrefetchQueueRepo,
        resolver,
        db_provider: Callable[[], aiosqlite.Connection],
        tick_interval_s: float = 2.0,
    ) -> None:
        self._queue = queue_repo
        self._resolver = resolver
        self._db_provider = db_provider
        self._tick_interval_s = tick_interval_s
        self._stop_evt: asyncio.Event = asyncio.Event()
        self._task: asyncio.Task | None = None

    # --- lifecycle ---------------------------------------------------

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stop_evt.clear()
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task is None:
            return
        self._stop_evt.set()
        try:
            # Generous timeout — a download in flight has to land or
            # error out before we can return. If the user really needs
            # the worker dead, cancel() is the escape hatch.
            await asyncio.wait_for(self._task, timeout=5.0)
        except TimeoutError:
            self._task.cancel()
        finally:
            self._task = None

    async def _loop(self) -> None:
        while not self._stop_evt.is_set():
            try:
                processed = await self.tick_once()
            except Exception:  # noqa: BLE001 — loop must not die
                log.exception("media_prefetcher tick failed")
                processed = None
            if processed is None:
                try:
                    await asyncio.wait_for(
                        self._stop_evt.wait(),
                        timeout=self._tick_interval_s,
                    )
                except TimeoutError:
                    pass
            # If we processed a row, loop immediately to drain.

    # --- single tick -------------------------------------------------

    async def tick_once(self) -> int | None:
        """Process the next queued row, if any.

        Returns the integer clip id that was processed, or None if the
        queue was empty.
        """
        db = self._db_provider()
        row = await self._queue.claim_next(db)
        if row is None:
            return None
        rid = int(row["id"])
        clip_id_str = row["provider_clip_id"]
        try:
            clip_id_int = int(clip_id_str)
        except ValueError:
            await self._queue.mark_error(
                db, rid, f"non-integer clip id: {clip_id_str!r}",
            )
            return clip_id_int if clip_id_str.isdigit() else 0

        try:
            path = await self._resolver.path_for_clip_id(clip_id_int)
            size = path.stat().st_size if path.exists() else 0
            await self._queue.mark_done(db, rid, bytes_downloaded=size)
        except Exception as exc:  # noqa: BLE001
            log.warning("prefetch failed for clip %s: %s", clip_id_int, exc)
            await self._queue.mark_error(db, rid, str(exc))
        return clip_id_int
```

- [ ] **Step 4: Run tests**

```
.venv/bin/python -m pytest tests/integration/test_media_prefetcher.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/media_prefetcher.py \
        tests/integration/test_media_prefetcher.py
git commit -m "feat(cache): MediaPrefetcher one-at-a-time background worker"
```

---

### Task 5: Wire prefetcher into `AppContext` + startup/shutdown

**Files:**
- Modify: `backend/app/settings.py`
- Modify: `backend/app/context.py`
- Modify: `backend/app/main.py`

- [ ] **Step 1: Add setting**

In `backend/app/settings.py`, add under the existing cache-management block:

```python
    # media prefetch queue
    prefetch_tick_interval_s: int = 2
```

- [ ] **Step 2: Wire into `AppContext`**

In `backend/app/context.py`:

a) Add the import at the top:

```python
from backend.app.repositories.prefetch_queue import PrefetchQueueRepo
from backend.app.services.media_prefetcher import MediaPrefetcher
```

b) Add to the `AppContext` dataclass — the repo as a `field(default_factory=...)` next to the other repos, and the service as a `Optional` next to `lru_eviction`:

```python
    prefetch_queue_repo: PrefetchQueueRepo = field(default_factory=PrefetchQueueRepo)
```

```python
    media_prefetcher: MediaPrefetcher | None = None
```

c) Inside `build()`, immediately after the `lru_eviction = LruEviction(...)` block (under `if init_external:`) and only when `ctx.proxy_resolver is not None`:

```python
            if ctx.proxy_resolver is not None:
                ctx.media_prefetcher = MediaPrefetcher(
                    queue_repo=ctx.prefetch_queue_repo,
                    resolver=ctx.proxy_resolver,
                    db_provider=lambda c=ctx: c.db,
                    tick_interval_s=float(settings.prefetch_tick_interval_s),
                )
```

d) In `aclose()`, stop the prefetcher first (it may hold a download in flight):

```python
    async def aclose(self) -> None:
        if self.media_prefetcher is not None:
            await self.media_prefetcher.stop()
        if self.lru_eviction is not None:
            await self.lru_eviction.stop()
        if self.sync_engine is not None:
            await self.sync_engine.stop()
        if self.connection_monitor is not None:
            await self.connection_monitor.stop()
        if self.catdv is not None:
            await self.catdv.__aexit__(None, None, None)
        await self.db_cm.__aexit__(None, None, None)
```

- [ ] **Step 3: Start the prefetcher in `main.py`**

In `backend/app/main.py`, inside the `if init_external:` block in `lifespan`:

```python
        if ctx.lru_eviction is not None:
            await ctx.lru_eviction.start()
        if ctx.media_prefetcher is not None:
            await ctx.media_prefetcher.start()
```

- [ ] **Step 4: Smoke test boot**

```
.venv/bin/python -m pytest tests/integration -k "smoke or main or context" -v
```

If no existing smoke test, at minimum run:

```
.venv/bin/python -c "
import asyncio
from pathlib import Path
from backend.app.context import AppContext
from backend.app.settings import Settings

async def main():
    import os
    os.environ.setdefault('CATDV_BASE_URL', 'http://localhost/none')
    os.environ.setdefault('CATDV_CATALOG_ID', '0')
    os.environ.setdefault('GCP_PROJECT_ID', 'x')
    os.environ.setdefault('GCS_BUCKET_NAME', 'x')
    s = Settings(data_dir=Path('/tmp/pf_smoke'))
    ctx = await AppContext.build(s, init_external=False)
    assert ctx.prefetch_queue_repo is not None
    await ctx.aclose()
asyncio.run(main())
print('ok')
"
```

Expected: prints `ok`.

- [ ] **Step 5: Commit**

```bash
git add backend/app/settings.py backend/app/context.py backend/app/main.py
git commit -m "feat(cache): wire MediaPrefetcher into AppContext + lifespan"
```

---

### Task 6: JSON routes — enqueue, list, cancel

**Files:**
- Modify: `backend/app/routes/cache.py`
- Create: `tests/integration/test_routes_prefetch.py`

- [ ] **Step 1: Write the failing tests**

`tests/integration/test_routes_prefetch.py`:

```python
import pytest
from fastapi.testclient import TestClient

from backend.app.main import app


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("CATDV_BASE_URL", "http://localhost/none")
    monkeypatch.setenv("CATDV_CATALOG_ID", "0")
    monkeypatch.setenv("GCP_PROJECT_ID", "x")
    monkeypatch.setenv("GCS_BUCKET_NAME", "x")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("APP_ENV", "dev")
    with TestClient(app) as c:
        yield c


def test_prefetch_enqueue_single(client):
    r = client.post(
        "/api/cache/prefetch",
        json={"clip_keys": [["catdv", "42"]]},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["enqueued"] == 1
    assert isinstance(body["ids"], list) and len(body["ids"]) == 1


def test_prefetch_enqueue_bulk_idempotent(client):
    r = client.post(
        "/api/cache/prefetch",
        json={"clip_keys": [["catdv", "1"], ["catdv", "2"], ["catdv", "1"]]},
    )
    assert r.status_code == 200
    body = r.json()
    # 3 enqueues but only 2 distinct active rows
    assert body["enqueued"] == 3
    assert len(set(body["ids"])) == 2


def test_queue_list_returns_rows(client):
    client.post("/api/cache/prefetch", json={"clip_keys": [["catdv", "7"]]})
    r = client.get("/api/cache/prefetch/queue")
    assert r.status_code == 200
    body = r.json()
    assert "active" in body and "recent" in body and "counts" in body
    active_keys = [(row["provider_id"], row["provider_clip_id"])
                   for row in body["active"]]
    assert ("catdv", "7") in active_keys


def test_cancel_queued_row(client):
    rid = client.post(
        "/api/cache/prefetch", json={"clip_keys": [["catdv", "99"]]}
    ).json()["ids"][0]
    r = client.post(f"/api/cache/prefetch/{rid}/cancel")
    assert r.status_code == 200
    assert r.json()["cancelled"] is True
```

- [ ] **Step 2: Run and confirm they fail**

```
.venv/bin/python -m pytest tests/integration/test_routes_prefetch.py -v
```

Expected: 404 on the prefetch routes.

- [ ] **Step 3: Add the routes**

In `backend/app/routes/cache.py`, add near the other JSON endpoints (after the `bulk_evict` route):

```python
class PrefetchBody(BaseModel):
    clip_keys: list[tuple[str, str]] = []


@api_router.post("/prefetch")
async def prefetch_enqueue(
    request: Request, body: PrefetchBody
) -> dict[str, Any]:
    ctx = request.app.state.ctx
    if ctx.media_prefetcher is None:
        raise HTTPException(503, "media prefetcher not initialized")
    ids: list[int] = []
    for prov, clip_id in body.clip_keys:
        rid = await ctx.prefetch_queue_repo.enqueue(
            ctx.db, key=(prov, clip_id), who="request",
        )
        ids.append(rid)
    return {"enqueued": len(body.clip_keys), "ids": ids}


@api_router.get("/prefetch/queue")
async def prefetch_queue_list(request: Request) -> dict[str, Any]:
    ctx = request.app.state.ctx
    active = await ctx.prefetch_queue_repo.list_active(ctx.db)
    recent = await ctx.prefetch_queue_repo.list_recent(ctx.db, limit=50)
    counts = await ctx.prefetch_queue_repo.count_by_status(ctx.db)
    return {"active": active, "recent": recent, "counts": counts}


@api_router.post("/prefetch/{rid}/cancel")
async def prefetch_cancel(
    request: Request, rid: int
) -> dict[str, Any]:
    ctx = request.app.state.ctx
    ok = await ctx.prefetch_queue_repo.mark_cancelled(ctx.db, rid)
    if not ok:
        raise HTTPException(
            409,
            "row is not cancellable (downloading or already terminal)",
        )
    return {"cancelled": True}
```

- [ ] **Step 4: Run the route tests**

```
.venv/bin/python -m pytest tests/integration/test_routes_prefetch.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/routes/cache.py tests/integration/test_routes_prefetch.py
git commit -m "feat(cache): /api/cache/prefetch{,/queue,/{id}/cancel} routes"
```

---

### Task 7: View-models inject cache hints

**Files:**
- Modify: `backend/app/ui/view_models.py`
- Modify: `backend/app/routes/pages.py`
- Create: `tests/unit/test_view_models_cache.py`

- [ ] **Step 1: Write failing tests**

`tests/unit/test_view_models_cache.py`:

```python
from backend.app.ui.view_models import cache_status_view


class _Layer:
    def __init__(self, present, evictable, size_bytes=0, pinned=()):
        self.present = present
        self.evictable = evictable
        self.size_bytes = size_bytes
        self.pinned_by_workspaces = pinned

    def to_dict(self):
        return {
            "present": self.present,
            "evictable": self.evictable,
            "size_bytes": self.size_bytes,
            "pinned_by_workspaces": list(self.pinned_by_workspaces),
        }


class _Status:
    def __init__(self, ml_present, ml_pinned=()):
        self.clip_key = ("catdv", "1")
        self.layers = (
            _Layer(True, True),                              # metadata
            _Layer(ml_present, not ml_pinned, 1024 * 1024, ml_pinned),
            _Layer(False, False),                            # media-ai
        )


def test_cache_status_view_present_unpinned():
    view = cache_status_view(_Status(ml_present=True))
    assert view["media_local"]["present"] is True
    assert view["media_local"]["pinned"] is False
    assert view["media_local"]["size_mb"] == 1


def test_cache_status_view_present_pinned():
    view = cache_status_view(_Status(ml_present=True, ml_pinned=(3,)))
    assert view["media_local"]["present"] is True
    assert view["media_local"]["pinned"] is True


def test_cache_status_view_absent():
    view = cache_status_view(_Status(ml_present=False))
    assert view["media_local"]["present"] is False
```

- [ ] **Step 2: Confirm failure**

```
.venv/bin/python -m pytest tests/unit/test_view_models_cache.py -v
```

Expected: FAIL — `cache_status_view` not defined.

- [ ] **Step 3: Add the helper + update `clip_summary` / `clip_detail`**

In `backend/app/ui/view_models.py`, add at the bottom:

```python
def cache_status_view(status) -> dict[str, Any]:
    """Render-ready cache hints for the badge + buttons.

    Accepts a `ClipCacheStatus` (or any object whose `.layers` is a
    3-tuple of layer objects exposing `present`, `evictable`,
    `size_bytes`, `pinned_by_workspaces`).
    """
    md, ml, ai = status.layers

    def _shape(layer) -> dict[str, Any]:
        size = int(layer.size_bytes or 0)
        pinned = bool(layer.pinned_by_workspaces)
        return {
            "present": bool(layer.present),
            "pinned": pinned,
            "evictable": bool(layer.evictable),
            "size_bytes": size,
            "size_mb": size // (1024 * 1024),
        }

    return {
        "clip_key": list(status.clip_key),
        "metadata": _shape(md),
        "media_local": _shape(ml),
        "media_ai": _shape(ai),
    }
```

Then update `clip_summary` to accept an optional cache status, and add a `cache_summary_for_clips` helper:

```python
def clip_summary(
    clip: CanonicalClip,
    cache_status: Any | None = None,
) -> dict[str, Any]:
    """One row in the clips-list table."""
    out = {
        "id": int(clip.key[1]),
        "name": _fix(clip.name),
        "duration_secs": clip.duration_secs,
        "year": _first_value(clip.fields.get(_YEAR_FIELD)),
        "decade": _first_value(clip.fields.get(_DECADE_FIELD)),
        "marker_count": len(clip.markers),
        "cache": cache_status_view(cache_status) if cache_status else None,
    }
    return out
```

And similarly add an optional `cache_status` to `clip_detail`:

```python
def clip_detail(
    clip: CanonicalClip,
    cache_status: Any | None = None,
) -> dict[str, Any]:
    # ... existing body unchanged ...
    out = {
        "clip": {
            "id": clip_id,
            "name": clip.name,
            "duration_secs": clip.duration_secs,
            "fps": clip.fps or 25.0,
            "format": _format_summary(clip.provider_data),
            "media_url": f"/api/media/{clip_id}",
            "markers": [_marker_view(m) for m in clip.markers],
            "fields": fields_view,
            "notes": _fix(clip.provider_data.get("notes")) or None,
            "big_notes": _fix(clip.provider_data.get("bigNotes")) or None,
            "cache": cache_status_view(cache_status) if cache_status else None,
        },
    }
    return out
```

- [ ] **Step 4: Update `routes/pages.py` to look up status**

In `backend/app/routes/pages.py`, change the list and detail handlers to consult the cache inspector when present. Replace the `clips_list` function body:

```python
@router.get("/", response_class=HTMLResponse)
async def clips_list(
    request: Request,
    q: str | None = None,
    offset: int = 0,
    limit: int = 50,
):
    ctx = request.app.state.ctx
    if ctx.archive is None:
        raise HTTPException(503, "archive provider not initialized")
    try:
        page = await ctx.archive.list_clips(
            str(ctx.settings.catdv_catalog_id),
            ClipQuery(text=q, offset=offset, limit=limit),
        )
    except ProviderError as exc:
        raise HTTPException(502, f"archive error: {exc}") from exc

    # Bulk cache lookup so each row gets a badge with no per-row HTMX hop.
    statuses: dict[tuple[str, str], object] = {}
    if ctx.cache_inspector is not None and page.items:
        keys = [c.key for c in page.items]
        rows = await ctx.cache_inspector.status_for_clips(keys)
        statuses = {r.clip_key: r for r in rows}

    ctx_dict = {
        "q": q or "",
        "offset": offset,
        "limit": limit,
        "total": page.total,
        "catalog": {
            "id": ctx.settings.catdv_catalog_id,
            "name": "AI katalog",
        },
        "clips": [
            clip_summary(c, cache_status=statuses.get(c.key))
            for c in page.items
        ],
        "prev_offset": max(0, offset - limit) if offset > 0 else None,
        "next_offset": offset + limit if offset + limit < page.total else None,
    }

    template = (
        "pages/_clips_tbody.html"
        if request.headers.get("HX-Request") == "true"
        else "pages/clips.html"
    )
    return templates.TemplateResponse(request, template, ctx_dict)
```

And `clip_detail_page`:

```python
@router.get("/clips/{clip_id}", response_class=HTMLResponse)
async def clip_detail_page(request: Request, clip_id: int):
    ctx = request.app.state.ctx
    if ctx.archive is None:
        raise HTTPException(503, "archive provider not initialized")
    try:
        clip = await ctx.archive.get_clip(str(clip_id))
    except ProviderError as exc:
        raise HTTPException(404, f"clip not found: {exc}") from exc

    cache_status = None
    if ctx.cache_inspector is not None:
        cache_status = await ctx.cache_inspector.status_for_clip(clip.key)

    ctx_dict = clip_detail(clip, cache_status=cache_status)
    ctx_dict["duration_smpte"] = secs_to_smpte(
        ctx_dict["clip"]["duration_secs"], ctx_dict["clip"]["fps"]
    )
    return templates.TemplateResponse(request, "pages/clip_detail.html", ctx_dict)
```

- [ ] **Step 5: Run all relevant tests**

```
.venv/bin/python -m pytest tests/unit/test_view_models_cache.py tests/unit/test_view_models.py tests/integration/test_routes_pages.py -v
```

Expected: PASS. (Existing view-model and route tests must still pass — the new `cache_status` arg is optional.)

- [ ] **Step 6: Commit**

```bash
git add backend/app/ui/view_models.py backend/app/routes/pages.py \
        tests/unit/test_view_models_cache.py
git commit -m "feat(ui): inject cache status into list + detail view-models"
```

---

### Task 8: List UI — checkbox column, cache badge, bulk toolbar

**Files:**
- Create: `backend/app/templates/pages/_cache_badge.html`
- Modify: `backend/app/templates/pages/_clips_tbody.html`
- Modify: `backend/app/templates/pages/clips.html`
- Modify: `backend/app/static/app.css`
- Create: `tests/integration/test_routes_pages_cache_badge.py`

- [ ] **Step 1: Write the failing tests**

`tests/integration/test_routes_pages_cache_badge.py`:

```python
import pytest
from fastapi.testclient import TestClient

from backend.app.main import app


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("CATDV_BASE_URL", "http://localhost/none")
    monkeypatch.setenv("CATDV_CATALOG_ID", "0")
    monkeypatch.setenv("GCP_PROJECT_ID", "x")
    monkeypatch.setenv("GCS_BUCKET_NAME", "x")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("APP_ENV", "dev")
    with TestClient(app) as c:
        yield c


def test_list_page_includes_cache_badge_column(client, monkeypatch):
    # Inject a fake archive that returns one canonical clip
    from backend.app.archive.model import CanonicalClip, ClipPage

    class _Archive:
        async def list_clips(self, catalog_id, q):
            clip = CanonicalClip(
                key=("catdv", "1"),
                name="Test", duration_secs=10.0,
                fps=25.0, markers=[], fields={},
                provider_data={},
            )
            return ClipPage(items=[clip], total=1, offset=0, limit=50)

    from fastapi.testclient import TestClient
    with TestClient(app) as c:
        c.app.state.ctx.archive = _Archive()
        # CacheInspector is wired even when init_external=False
        r = c.get("/")
    assert r.status_code == 200
    html = r.text
    assert 'class="cache-badge"' in html or "cache-badge" in html
    assert 'name="clip_keys"' in html        # bulk checkbox
    assert "bulk-toolbar" in html             # toolbar present


def test_list_page_bulk_toolbar_actions_present(client):
    r = client.get("/")
    assert r.status_code in (200, 502)  # 502 if no real archive
    if r.status_code == 200:
        assert "Cache selected" in r.text
        assert "Evict selected" in r.text
```

- [ ] **Step 2: Run, confirm failure**

```
.venv/bin/python -m pytest tests/integration/test_routes_pages_cache_badge.py -v
```

Expected: FAIL — markup missing.

- [ ] **Step 3: Create the server-rendered badge partial**

`backend/app/templates/pages/_cache_badge.html`:

```html
{# Server-rendered cache badge. Expects `cache` (dict from view_models.cache_status_view).
   No HTMX — refreshes happen via full HX-swaps of the surrounding row.
#}
{% if cache %}
<span class="cache-badge"
      title="metadata: {% if cache.metadata.present %}present{% else %}absent{% endif %} ·
             local: {% if cache.media_local.present %}{{ cache.media_local.size_mb }} MB{% if cache.media_local.pinned %} (pinned){% endif %}{% else %}absent{% endif %} ·
             ai: {% if cache.media_ai.present %}{{ cache.media_ai.size_mb }} MB{% else %}absent{% endif %}">
  <span class="glyph metadata
               {% if cache.metadata.present %}{% if cache.metadata.evictable %}present-fresh{% else %}present-pinned{% endif %}{% else %}absent{% endif %}">●</span>
  <span class="glyph media-local
               {% if cache.media_local.present %}{% if cache.media_local.pinned %}present-pinned{% else %}present-fresh{% endif %}{% else %}absent{% endif %}">▣</span>
  <span class="glyph media-ai
               {% if cache.media_ai.present %}{% if cache.media_ai.evictable %}present-fresh{% else %}present-pinned{% endif %}{% else %}absent{% endif %}">▲</span>
</span>
{% else %}
<span class="cache-badge cache-badge-absent" title="cache state unknown">— — —</span>
{% endif %}
```

- [ ] **Step 4: Update `_clips_tbody.html`**

`backend/app/templates/pages/_clips_tbody.html`:

```html
<div id="clips-region" class="clips-region">
  <div class="tbl-scroll">
    <table class="tbl">
      <thead>
        <tr>
          <th class="col-sel"><input type="checkbox" id="row-select-all" aria-label="Select all"></th>
          <th class="col-cache" title="metadata · media-local · media-ai">Cache</th>
          <th class="col-name">Clip</th>
          <th class="col-year">Year</th>
          <th class="col-decade">Decade</th>
          <th class="col-dur">Duration</th>
          <th class="col-mk">Markers</th>
        </tr>
      </thead>
      <tbody>
        {% for c in clips %}
        <tr class="row" data-clip-id="{{ c.id }}">
          <td class="row-select" onclick="event.stopPropagation()">
            <input type="checkbox"
                   class="row-check"
                   name="clip_keys"
                   value="catdv/{{ c.id }}"
                   aria-label="Select clip {{ c.id }}">
          </td>
          <td class="cell-cache" onclick="event.stopPropagation()">
            {% with cache = c.cache %}
              {% include "pages/_cache_badge.html" %}
            {% endwith %}
          </td>
          <td class="clip-name" onclick="location.href='/clips/{{ c.id }}'">
            <span class="thumb"></span>
            <span class="name">{{ c.name }}</span>
          </td>
          <td class="mono" onclick="location.href='/clips/{{ c.id }}'">{{ c.year or "—" }}</td>
          <td onclick="location.href='/clips/{{ c.id }}'">{{ c.decade or "—" }}</td>
          <td class="mono" onclick="location.href='/clips/{{ c.id }}'">{{ "%d:%02d"|format((c.duration_secs|int)//60, (c.duration_secs|int)%60) }}</td>
          <td class="mono" onclick="location.href='/clips/{{ c.id }}'">{{ c.marker_count }}</td>
        </tr>
        {% else %}
        <tr><td colspan="7" class="empty">No clips match.</td></tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
  <nav class="pager">
    {% if prev_offset is not none %}
      <a class="pg-btn" href="/?q={{ q|urlencode }}&offset={{ prev_offset }}&limit={{ limit }}">‹ Prev</a>
    {% else %}
      <span class="pg-btn disabled">‹ Prev</span>
    {% endif %}
    <span class="pg-meta mono">{{ offset + 1 }}–{{ offset + clips|length }} of {{ total }}</span>
    {% if next_offset is not none %}
      <a class="pg-btn" href="/?q={{ q|urlencode }}&offset={{ next_offset }}&limit={{ limit }}">Next ›</a>
    {% else %}
      <span class="pg-btn disabled">Next ›</span>
    {% endif %}
  </nav>
</div>
```

- [ ] **Step 5: Update `clips.html` — add bulk toolbar**

`backend/app/templates/pages/clips.html`:

```html
{% extends "pages/layout.html" %}
{% block crumb %}
  <span class="crumb">{{ catalog.name }} · #{{ catalog.id }}</span>
{% endblock %}
{% block body %}
<div class="page page-clips" x-data="bulkSel()">
  <div class="toolbar">
    <div class="search">
      <input type="search" name="q" value="{{ q }}"
             placeholder="search name…"
             hx-get="/" hx-trigger="input changed delay:300ms, keyup[key=='Enter']"
             hx-target="#clips-region" hx-swap="outerHTML"
             hx-include="this">
    </div>
    <span class="grow"></span>
    <span class="meta mono">catalog {{ catalog.id }}</span>
  </div>

  <div class="bulk-toolbar"
       :class="{ 'bulk-toolbar-active': count > 0 }">
    <span class="bulk-count" x-text="count + ' selected'">0 selected</span>
    <span class="grow"></span>
    <a class="bulk-btn" href="/cache">Cache view ›</a>
    <button type="button" class="bulk-btn"
            :disabled="count === 0"
            @click="bulkPrefetch()">Cache selected</button>
    <button type="button" class="bulk-btn bulk-btn-danger"
            :disabled="count === 0"
            @click="bulkEvict()">Evict selected</button>
  </div>

  {% include "pages/_clips_tbody.html" %}
</div>

<script>
  function bulkSel() {
    return {
      count: 0,
      _selectedKeys() {
        return Array.from(
          document.querySelectorAll('.row-check:checked')
        ).map(el => el.value.split('/'));
      },
      init() {
        document.addEventListener('change', e => {
          if (e.target.classList.contains('row-check')) {
            this.count = this._selectedKeys().length;
          }
          if (e.target.id === 'row-select-all') {
            document.querySelectorAll('.row-check').forEach(
              cb => cb.checked = e.target.checked
            );
            this.count = this._selectedKeys().length;
          }
        });
      },
      async bulkPrefetch() {
        const keys = this._selectedKeys();
        if (keys.length === 0) return;
        const r = await fetch('/api/cache/prefetch', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ clip_keys: keys }),
        });
        if (r.ok) htmx.ajax('GET', window.location.href, '#clips-region');
      },
      async bulkEvict() {
        const keys = this._selectedKeys();
        if (keys.length === 0) return;
        if (!confirm(`Evict local media for ${keys.length} clip(s)?`)) return;
        const r = await fetch('/api/cache/bulk-evict', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            clip_keys: keys,
            layers: ['media-local'],
            force: false,
          }),
        });
        if (r.ok) htmx.ajax('GET', window.location.href, '#clips-region');
      },
    }
  }
</script>
{% endblock %}
```

- [ ] **Step 6: Add the CSS**

Append to `backend/app/static/app.css`:

```css
/* ─── cache badge ────────────────────────────────────────────────────── */
.cache-badge { display: inline-flex; gap: 2px; font-family: var(--f-mono); }
.cache-badge .glyph { display: inline-block; padding: 0 1px; line-height: 1; }
.cache-badge .glyph.present-fresh  { color: var(--good); }
.cache-badge .glyph.present-pinned { color: var(--accent); }
.cache-badge .glyph.absent         { color: var(--text-4); }
.cache-badge-absent { color: var(--text-4); }

/* ─── row select + bulk toolbar ──────────────────────────────────────── */
.col-sel, .row-select { width: 28px; text-align: center; }
.col-cache, .cell-cache { width: 56px; }
.row-check { cursor: pointer; }
.bulk-toolbar {
  display: flex; gap: 0.5rem; align-items: center;
  padding: 0.5rem 0.75rem;
  border: 1px solid var(--line);
  border-radius: var(--r-2);
  background: var(--panel);
  margin-bottom: 0.5rem;
}
.bulk-toolbar-active { border-color: var(--accent-2); }
.bulk-count { color: var(--text-3); font-family: var(--f-mono); }
.bulk-btn {
  background: var(--surface); color: var(--text);
  border: 1px solid var(--line-2);
  border-radius: var(--r-1);
  padding: 0.35rem 0.7rem;
  font: 500 0.85rem/1 var(--f-sans);
  cursor: pointer; text-decoration: none;
}
.bulk-btn:hover { background: var(--surface-2); }
.bulk-btn[disabled] { opacity: 0.4; cursor: not-allowed; }
.bulk-btn-danger { color: var(--bad); border-color: color-mix(in oklab, var(--bad) 30%, transparent); }
```

- [ ] **Step 7: Run the page test + manual smoke**

```
.venv/bin/python -m pytest tests/integration/test_routes_pages_cache_badge.py tests/integration/test_routes_pages.py -v
```

Expected: PASS.

Manual smoke (only if VPN is up; this is optional):
- Run `.venv/bin/python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8765`
- Open `http://localhost:8765/`
- Confirm: checkbox column visible, cache column shows `● ▣ ▲` triplets, toolbar shows `0 selected`, clicking checkboxes updates the count, clicking "Cache selected" enqueues (check `/api/cache/prefetch/queue` JSON).

- [ ] **Step 8: Commit**

```bash
git add backend/app/templates/pages/_cache_badge.html \
        backend/app/templates/pages/_clips_tbody.html \
        backend/app/templates/pages/clips.html \
        backend/app/static/app.css \
        tests/integration/test_routes_pages_cache_badge.py
git commit -m "feat(ui): cache badge column + bulk select toolbar on clips list"
```

---

### Task 9: Detail page — badge + "Cache video" / "Evict local" buttons

**Files:**
- Modify: `backend/app/templates/pages/clip_detail.html`
- Modify: `backend/app/static/app.css`

- [ ] **Step 1: Update the detail template header**

In `backend/app/templates/pages/clip_detail.html`, change the existing `<header class="detail-hdr">` to:

```html
  <header class="detail-hdr">
    <span class="clip-title">{{ clip.name }}</span>
    <span class="meta mono">{{ clip.format or "" }}</span>
    <span class="cache-actions">
      {% with cache = clip.cache %}
        {% include "pages/_cache_badge.html" %}
      {% endwith %}
      {% if clip.cache and clip.cache.media_local.present %}
        <button type="button"
                class="ca-btn ca-btn-danger"
                onclick="evictLocal({{ clip.id }})">Evict local</button>
      {% else %}
        <button type="button"
                class="ca-btn"
                onclick="cacheClip({{ clip.id }})">⬇ Cache video</button>
      {% endif %}
    </span>
    <span class="grow"></span>
    <span class="tc-readout mono">
      <span class="cur" x-text="tc(current)">00:00:00:00</span>
      <span class="slash">/</span>
      <span class="end" x-text="tc(duration)">{{ duration_smpte }}</span>
    </span>
  </header>
```

And add this `<script>` block at the very bottom of the template, before `{% endblock %}`:

```html
<script>
  async function cacheClip(clipId) {
    const r = await fetch('/api/cache/prefetch', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ clip_keys: [['catdv', String(clipId)]] }),
    });
    if (r.ok) {
      // Re-render the page so the badge updates.
      window.location.reload();
    }
  }
  async function evictLocal(clipId) {
    if (!confirm('Evict the local proxy for this clip?')) return;
    const r = await fetch(
      `/api/cache/clip/catdv/${clipId}/evict`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ layers: ['media-local'], force: false }),
      },
    );
    if (r.ok) window.location.reload();
  }
</script>
```

- [ ] **Step 2: Add CSS**

Append to `backend/app/static/app.css`:

```css
/* ─── cache actions on detail header ─────────────────────────────────── */
.cache-actions { display: inline-flex; align-items: center; gap: 0.5rem; margin-left: 0.75rem; }
.ca-btn {
  background: var(--surface); color: var(--text);
  border: 1px solid var(--line-2);
  border-radius: var(--r-1);
  padding: 0.3rem 0.6rem;
  font: 500 0.8rem/1 var(--f-sans);
  cursor: pointer;
}
.ca-btn:hover { background: var(--surface-2); }
.ca-btn-danger { color: var(--bad); border-color: color-mix(in oklab, var(--bad) 30%, transparent); }
```

- [ ] **Step 3: Manual smoke (no automated test — pure markup change)**

Open `http://localhost:8765/clips/<some-id>` (VPN up). Confirm the badge appears in the header and the button switches between "Cache video" and "Evict local" depending on whether the proxy is already on disk.

- [ ] **Step 4: Commit**

```bash
git add backend/app/templates/pages/clip_detail.html backend/app/static/app.css
git commit -m "feat(ui): cache badge + prefetch/evict buttons on clip detail"
```

---

### Task 10: `/cache` page — queue panel

**Files:**
- Create: `backend/app/templates/pages/_prefetch_panel.html`
- Modify: `backend/app/templates/cache_page.html`
- Modify: `backend/app/routes/cache.py`

- [ ] **Step 1: Add the HTMX panel route**

In `backend/app/routes/cache.py`, add (near the other UI router endpoints):

```python
@ui_router.get("/cache/queue", response_class=HTMLResponse)
async def cache_queue_panel(request: Request) -> HTMLResponse:
    ctx = request.app.state.ctx
    active = await ctx.prefetch_queue_repo.list_active(ctx.db)
    recent = await ctx.prefetch_queue_repo.list_recent(ctx.db, limit=20)
    counts = await ctx.prefetch_queue_repo.count_by_status(ctx.db)
    return templates.TemplateResponse(
        request,
        "pages/_prefetch_panel.html",
        {"active": active, "recent": recent, "counts": counts},
    )
```

- [ ] **Step 2: Create the panel partial**

`backend/app/templates/pages/_prefetch_panel.html`:

```html
{# Prefetch queue panel, served at /ui/cache/queue and embedded into /cache.

   Refreshes itself every 2 seconds via HTMX so the user can watch a
   download tick. Each row's cancel button POSTs to the cancel route
   and swaps the panel back in.
#}
<div id="prefetch-panel"
     hx-get="/ui/cache/queue"
     hx-trigger="every 2s"
     hx-swap="outerHTML">
  <h3>Prefetch queue
    <span class="muted">
      queued={{ counts.get("queued", 0) }} ·
      downloading={{ counts.get("downloading", 0) }} ·
      done={{ counts.get("done", 0) }} ·
      error={{ counts.get("error", 0) }} ·
      cancelled={{ counts.get("cancelled", 0) }}
    </span>
  </h3>

  {% if active %}
    <table class="prefetch-active">
      <thead>
        <tr><th>Clip</th><th>Status</th><th>Started</th><th>Size</th><th></th></tr>
      </thead>
      <tbody>
        {% for r in active %}
          <tr class="prefetch-row prefetch-{{ r.status }}">
            <td><a href="/clips/{{ r.provider_clip_id }}">{{ r.provider_id }}/{{ r.provider_clip_id }}</a></td>
            <td>{{ r.status }}</td>
            <td class="mono">{{ r.started_at or "—" }}</td>
            <td class="mono">{{ "%.1f" % (r.bytes_downloaded / 1048576) }} MB</td>
            <td>
              {% if r.status == "queued" %}
                <button type="button"
                        hx-post="/api/cache/prefetch/{{ r.id }}/cancel"
                        hx-target="#prefetch-panel"
                        hx-swap="outerHTML"
                        hx-on::after-request="htmx.ajax('GET', '/ui/cache/queue', '#prefetch-panel')">
                  Cancel
                </button>
              {% endif %}
            </td>
          </tr>
        {% endfor %}
      </tbody>
    </table>
  {% else %}
    <p class="muted">Queue is empty.</p>
  {% endif %}

  {% if recent %}
    <details class="prefetch-history">
      <summary>Recent activity</summary>
      <table>
        <thead>
          <tr><th>Clip</th><th>Status</th><th>Requested</th><th>Finished</th><th>Size</th><th>Error</th></tr>
        </thead>
        <tbody>
          {% for r in recent %}
            <tr class="prefetch-row prefetch-{{ r.status }}">
              <td>{{ r.provider_id }}/{{ r.provider_clip_id }}</td>
              <td>{{ r.status }}</td>
              <td class="mono">{{ r.requested_at }}</td>
              <td class="mono">{{ r.finished_at or "—" }}</td>
              <td class="mono">{{ "%.1f" % (r.bytes_downloaded / 1048576) }} MB</td>
              <td>{{ r.error or "" }}</td>
            </tr>
          {% endfor %}
        </tbody>
      </table>
    </details>
  {% endif %}
</div>
```

- [ ] **Step 3: Include the panel in `cache_page.html`**

In `backend/app/templates/cache_page.html`, add the panel near the top, just below `</div>` of `.summary`:

```html
  </div>

  {# Prefetch queue lives here, server-rendered on first paint, then
     self-refreshes every 2s via HTMX. #}
  {% include "pages/_prefetch_panel.html" %}

  <form action="" method="get" class="filters">
```

Also extend the controller `cache_page` (in `routes/cache.py`) to pass the queue data so the first paint isn't empty. Change the function so it adds the three queue keys to the context dict:

```python
@page_router.get("/cache", response_class=HTMLResponse)
async def cache_page(
    request: Request,
    store: str | None = None,
    workspace: int | None = None,
    orphans: int | None = None,
    evictable: int | None = None,
) -> HTMLResponse:
    insp = _inspector(request)
    summary = await insp.summary()
    if orphans:
        statuses = await insp.list_orphans()
    else:
        ctx = request.app.state.ctx
        keys = await _all_cached_keys(ctx.db)
        statuses = await insp.status_for_clips(keys)
    rows = []
    for status in statuses:
        if store:
            ai_layer = status.layers[2]
            if not ai_layer.present or store not in (ai_layer.location or ""):
                continue
        if workspace is not None:
            md_layer = status.layers[0]
            if workspace not in md_layer.pinned_by_workspaces:
                continue
        if evictable:
            if not any(layer.evictable for layer in status.layers):
                continue
        rows.append(status)

    ctx = request.app.state.ctx
    active = await ctx.prefetch_queue_repo.list_active(ctx.db)
    recent = await ctx.prefetch_queue_repo.list_recent(ctx.db, limit=20)
    counts = await ctx.prefetch_queue_repo.count_by_status(ctx.db)

    return templates.TemplateResponse(
        request,
        "cache_page.html",
        {
            "summary": summary,
            "rows": [_status_for_template(s) for s in rows],
            "filters": {
                "store": store,
                "workspace": workspace,
                "orphans": bool(orphans),
                "evictable": bool(evictable),
            },
            "active": active,
            "recent": recent,
            "counts": counts,
        },
    )
```

- [ ] **Step 4: Add CSS**

Append to `backend/app/static/app.css`:

```css
/* ─── prefetch panel ────────────────────────────────────────────────── */
#prefetch-panel { margin-bottom: 1.5rem; padding: 0.75rem 1rem;
                  background: var(--panel); border: 1px solid var(--line);
                  border-radius: var(--r-2); }
#prefetch-panel h3 { margin: 0 0 0.5rem; font-size: 1rem; }
#prefetch-panel .muted { color: var(--text-3); font-weight: normal;
                         font-family: var(--f-mono); font-size: 0.8rem;
                         margin-left: 0.5rem; }
.prefetch-active, .prefetch-history table { width: 100%; border-collapse: collapse; }
.prefetch-active th, .prefetch-active td,
.prefetch-history th, .prefetch-history td {
  text-align: left; padding: 0.3rem 0.5rem;
  border-bottom: 1px solid var(--line); font-size: 0.85rem;
}
.prefetch-row.prefetch-downloading { background: color-mix(in oklab, var(--accent) 8%, transparent); }
.prefetch-row.prefetch-error       { background: color-mix(in oklab, var(--bad) 8%, transparent); }
.prefetch-row.prefetch-cancelled   { color: var(--text-3); }
```

- [ ] **Step 5: Smoke**

- `.venv/bin/python -m pytest tests/integration/test_routes_prefetch.py tests/integration/test_routes_cache.py -v` — all pass.
- Manual: open `/cache`, watch the panel auto-refresh; enqueue a clip from the list; confirm the queue panel shows it within 2 seconds.

- [ ] **Step 6: Commit**

```bash
git add backend/app/templates/pages/_prefetch_panel.html \
        backend/app/templates/cache_page.html \
        backend/app/routes/cache.py \
        backend/app/static/app.css
git commit -m "feat(ui): prefetch queue panel on /cache page"
```

---

### Task 11: Decisions doc + README

**Files:**
- Modify: `docs/decisions.md`
- Modify: `README.md`

- [ ] **Step 1: Append the six decisions**

Open `docs/decisions.md` and add a new section:

```markdown
## 2026-05-20 — Media prefetch + cache UI wiring (PR 8)

1. Prefetch is a persistent SQLite queue (`prefetch_queue`), not in-memory. A
   long download must survive process restart. The same table powers the
   `/cache?tab=queue` UI panel.

2. Single-flight serialization lives in the worker, not in `RestProxyResolver`.
   The resolver remains request-driven; the prefetcher runs at most one
   `tick_once()` body at a time. On-demand `/api/media/{id}` requests do not
   queue behind it — the existing "file exists, skip download" check de-dups
   naturally once the file lands.

3. `RestProxyResolver` now records into `proxy_cache` after a successful
   download. Without this, `CacheInspector` reports `media-local: absent`
   even when the file is on disk. The prefetcher would have papered over
   this; we fix the underlying gap instead.

4. Cancellation is honored only for `queued` and `error` rows. A
   `downloading` row cannot be cancelled mid-stream — we do not want
   partial files that `curl -C -` would later treat as a resume target.
   `stop()` is still respected between rows.

5. Cache badges in the clips list are rendered server-side from a single
   bulk `CacheInspector.status_for_clips([keys])` lookup, not via per-row
   HTMX. The `/ui/cache-badge/{provider}/{clip_id}` route stays for
   post-evict refresh but is no longer the primary render path.

6. No new column on `proxy_cache`. The queue table's `status` is the queue's
   job. Once a file lands, `proxy_cache.record()` is called and the queue
   row goes to `done`. The two tables are joined on
   `(provider_id, provider_clip_id)` only at display time.
```

- [ ] **Step 2: README pointer**

In `README.md`, add a line under the existing "UI" section:

```markdown
- **Cache view:** `http://localhost:8765/cache` — manage local proxy cache (status, queue, evict).
```

- [ ] **Step 3: Commit**

```bash
git add docs/decisions.md README.md
git commit -m "docs(cache): PR 8 decisions + README pointer to /cache"
```

---

## Verification checklist (run before declaring done)

- [ ] `.venv/bin/python -m pytest -x` — all tests pass (no `xfail` introduced).
- [ ] `.venv/bin/python -m ruff check backend tests` — clean.
- [ ] Boot the app against the real VPN: `bash run.sh` (or `uvicorn ...`).
  - Open `/`, confirm badges render and reflect actual cache state.
  - Tick three clips, hit "Cache selected", confirm `/cache` shows them queued and then transition through `downloading` → `done` one at a time.
  - During a `downloading` row, confirm "Cancel" is absent on that row but available on still-`queued` rows.
  - Open a clip's detail page that is `done`; confirm the button reads "Evict local" and clicking it removes the row (badge updates after reload).
- [ ] Inspect `data/app.db`: `SELECT status, COUNT(*) FROM prefetch_queue GROUP BY status` shows expected mix; `SELECT COUNT(*) FROM proxy_cache` increased by the number of successful downloads.
- [ ] Shut down the server during an active download; restart; confirm the downloading row was reset to `queued` (or `error`, depending on policy) — adjust crash recovery in `AppContext.build` if needed: SQL `UPDATE prefetch_queue SET status='queued', started_at=NULL WHERE status='downloading'` modelled on the existing `pending_operations` recovery.

> **Note for the implementer:** The crash-recovery step above is not in any task because it's a one-line addition to `AppContext.build()` that's easier to add when you confirm during verification that it's actually needed. If the manual restart test shows a stuck `downloading` row, add this line right next to the existing `pending_operations` recovery in `context.py:build()`:
>
> ```python
> await conn.execute(
>     "UPDATE prefetch_queue SET status='queued', started_at=NULL "
>     "WHERE status='downloading'"
> )
> ```
>
> And add a matching test in `tests/integration/test_media_prefetcher.py`.
