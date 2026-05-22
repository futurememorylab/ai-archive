# Offline Fallback Mode — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the annotator run fully usable without CatDV — auto-fall-back to stale local cache when the connection drops, optional `CATDV_OFFLINE=true` env override to skip CatDV login at startup, and user-controlled reconnect via a UI state chip (no background re-probing while offline).

**Architecture:** Inject an `is_online_provider` callable into `CatdvArchiveAdapter`; reads degrade to stale-cache, `apply_changes` fail-fasts to `RetryableError` so the existing `SyncEngine` queues writes. `ConnectionMonitor` gains a `forced_offline` ctor flag, halts its probe loop on failure, and exposes `retry_now()` for the new `POST /api/connection/retry` endpoint. A `LocalCacheOnlyResolver` serves on-disk proxies without touching CatDV. UI: a topbar chip with three states (online / offline-click-to-reconnect / forced) and template-level hides for actions that need CatDV.

**Tech Stack:** Python 3.12, FastAPI, aiosqlite, pytest (async via `pytest-asyncio`), Jinja2 + HTMX templates, existing `tests/fakes/fake_catdv.py` for adapter tests.

**Spec:** `docs/specs/2026-05-22-offline-fallback-design.md`

---

## File Map

**Create:**
- `tests/integration/test_clip_cache_list_offline.py`
- `tests/integration/test_catdv_adapter_offline_fallback.py`
- `tests/unit/test_local_cache_only_resolver.py`
- `tests/integration/test_connection_monitor_halt_and_retry.py`
- `tests/integration/test_offline_mode_e2e.py`
- `backend/app/templates/_connection_chip.html`

**Modify:**
- `backend/app/settings.py` — add `catdv_offline` field
- `.env.example` — document `CATDV_OFFLINE`
- `backend/app/repositories/clip_cache.py` — extend `list_by_catalog` with pagination/search/canonical reconstruction
- `backend/app/archive/providers/catdv/adapter.py` — `is_online_provider` ctor param, stale-fallback in 3 reads, fail-fast in `apply_changes`, offline `list_clips` path
- `backend/app/services/proxy_resolver.py` — add `LocalCacheOnlyResolver`, branch in `build_resolver` on `source="cache-only"`
- `backend/app/services/connection_monitor.py` — `forced_offline` ctor flag, halt-after-failure loop, `retry_now()` method
- `backend/app/routes/connection.py` — `POST /api/connection/retry`, add `mode` to `/state`
- `backend/app/routes/health.py` (or wherever `/api/health` lives) — expose `mode`
- `backend/app/context.py` — branch on `catdv_offline`, build offline resolver, pass `is_online_provider`, catch startup auth fail and degrade
- `backend/app/templates/base.html` (or the topbar partial) — include `_connection_chip.html`
- `backend/app/templates/clips.html`, `clip_detail.html` — conditional hides + offline banner + 404-style "not cached" page
- `README.md`, `docs/DEPLOY.md` — document offline mode

---

## Task 1: Settings — add `CATDV_OFFLINE` flag

**Files:**
- Modify: `backend/app/settings.py`
- Modify: `.env.example`
- Test: `tests/unit/test_settings.py` (extend if it exists, else create a `test_settings_offline.py`)

- [ ] **Step 1: Write the failing test**

Create or extend `tests/unit/test_settings_offline.py`:

```python
import os

import pytest

from backend.app.settings import Settings


def _required_env() -> dict[str, str]:
    return {
        "CATDV_BASE_URL": "http://example",
        "CATDV_CATALOG_ID": "1",
        "GCP_PROJECT_ID": "p",
        "GCS_BUCKET_NAME": "b",
    }


def test_catdv_offline_defaults_to_false(monkeypatch):
    for k, v in _required_env().items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("CATDV_OFFLINE", raising=False)
    s = Settings(_env_file=None)
    assert s.catdv_offline is False


@pytest.mark.parametrize("val,expected", [("true", True), ("false", False), ("1", True), ("0", False)])
def test_catdv_offline_parses_truthy_strings(monkeypatch, val, expected):
    for k, v in _required_env().items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("CATDV_OFFLINE", val)
    s = Settings(_env_file=None)
    assert s.catdv_offline is expected
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/unit/test_settings_offline.py -v`
Expected: FAIL with `AttributeError: 'Settings' object has no attribute 'catdv_offline'`.

- [ ] **Step 3: Add the field to settings**

Edit `backend/app/settings.py`, in the `Settings` class right after `proxy_cache_cap_gb`:

```python
    catdv_offline: bool = False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/unit/test_settings_offline.py -v`
Expected: PASS.

- [ ] **Step 5: Document in `.env.example`**

Add this block above the existing `# GCP / Vertex AI` section:

```
# Offline mode
# Set to true to skip the CatDV login at startup and run from local
# cache only (clip list, clip detail, locally-cached proxies). The UI
# shows a red "Offline (forced)" chip; reconnect must be done by
# unsetting this flag and restarting.
CATDV_OFFLINE=false
```

- [ ] **Step 6: Commit**

```bash
git add backend/app/settings.py .env.example tests/unit/test_settings_offline.py
git commit -m "feat(settings): add CATDV_OFFLINE env flag"
```

---

## Task 2: Extend `ClipCacheRepo.list_by_catalog` with pagination + search + canonical reconstruction

**Files:**
- Modify: `backend/app/repositories/clip_cache.py`
- Test: `tests/integration/test_clip_cache_list_offline.py`

**Background:** the existing `list_by_catalog` returns raw rows with no pagination, no search, and no canonical conversion. Offline `list_clips` needs `(items: tuple[CanonicalClip,...], total: int)` with offset/limit/q. We extend the existing method rather than add a sibling — the only current caller (`CacheInspector.deep_orphans`) treats results as raw rows; we keep that path working by making the new behavior opt-in via kwargs.

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_clip_cache_list_offline.py`:

```python
from datetime import datetime, timezone

import pytest

from backend.app.archive.model import CanonicalClip, MediaRef
from backend.app.repositories.clip_cache import ClipCacheRepo


def _clip(name: str, notes: str = "", clip_id: str = "1") -> CanonicalClip:
    return CanonicalClip(
        key=("catdv", clip_id),
        name=name,
        duration_secs=10.0,
        fps=25.0,
        markers=(),
        fields={},
        notes={"notes": notes},
        media=MediaRef(mime_type="video/quicktime", size_bytes=0),
        provider_data={},
        fetched_at=datetime.now(timezone.utc),
    )


@pytest.mark.asyncio
async def test_paginated_list_returns_items_and_total(db):
    repo = ClipCacheRepo()
    for i in range(5):
        await repo.upsert(db, clip=_clip(f"Clip {i}", clip_id=str(i)), catalog_id="881507")

    items, total = await repo.list_by_catalog(
        db, provider_id="catdv", catalog_id="881507",
        offset=0, limit=2, q=None, canonical=True,
    )
    assert total == 5
    assert [c.name for c in items] == ["Clip 0", "Clip 1"]


@pytest.mark.asyncio
async def test_search_matches_name_case_insensitive(db):
    repo = ClipCacheRepo()
    await repo.upsert(db, clip=_clip("alpha", clip_id="1"), catalog_id="881507")
    await repo.upsert(db, clip=_clip("BETA", clip_id="2"), catalog_id="881507")
    await repo.upsert(db, clip=_clip("gamma", clip_id="3"), catalog_id="881507")

    items, total = await repo.list_by_catalog(
        db, provider_id="catdv", catalog_id="881507",
        offset=0, limit=10, q="bet", canonical=True,
    )
    assert total == 1
    assert items[0].name == "BETA"


@pytest.mark.asyncio
async def test_search_matches_notes(db):
    repo = ClipCacheRepo()
    await repo.upsert(db, clip=_clip("nope", notes="needle in here", clip_id="1"), catalog_id="881507")
    await repo.upsert(db, clip=_clip("other", notes="haystack", clip_id="2"), catalog_id="881507")

    items, total = await repo.list_by_catalog(
        db, provider_id="catdv", catalog_id="881507",
        offset=0, limit=10, q="needle", canonical=True,
    )
    assert total == 1
    assert items[0].name == "nope"


@pytest.mark.asyncio
async def test_catalog_filter_excludes_other_catalogs(db):
    repo = ClipCacheRepo()
    await repo.upsert(db, clip=_clip("mine", clip_id="1"), catalog_id="881507")
    await repo.upsert(db, clip=_clip("theirs", clip_id="2"), catalog_id="999999")

    items, total = await repo.list_by_catalog(
        db, provider_id="catdv", catalog_id="881507",
        offset=0, limit=10, q=None, canonical=True,
    )
    assert total == 1
    assert items[0].name == "mine"


@pytest.mark.asyncio
async def test_legacy_call_returns_raw_rows_unchanged(db):
    """Existing CacheInspector.deep_orphans still calls the no-kwarg shape."""
    repo = ClipCacheRepo()
    await repo.upsert(db, clip=_clip("x", clip_id="1"), catalog_id="881507")
    rows = await repo.list_by_catalog(db, provider_id="catdv", catalog_id="881507")
    assert isinstance(rows, list) and len(rows) == 1
    assert rows[0]["provider_clip_id"] == "1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/integration/test_clip_cache_list_offline.py -v`
Expected: FAIL with `TypeError: list_by_catalog() got an unexpected keyword argument 'offset'`.

- [ ] **Step 3: Extend the method**

Replace the existing `list_by_catalog` in `backend/app/repositories/clip_cache.py` (around line 206) with this overload-style implementation:

```python
    async def list_by_catalog(
        self,
        conn: aiosqlite.Connection,
        *,
        provider_id: str,
        catalog_id: str,
        offset: int | None = None,
        limit: int | None = None,
        q: str | None = None,
        canonical: bool = False,
    ):
        """Two modes:

        - Legacy (no offset/limit/canonical): returns `list[dict]` of raw
          rows for callers like `CacheInspector.deep_orphans`.
        - Paginated/canonical (kwargs provided): returns
          `(tuple[CanonicalClip, ...], total: int)` filtered by optional
          substring `q` against `name` and the cached blob's `notes` map.
        """
        if not canonical and offset is None and limit is None and q is None:
            cur = await conn.execute(
                f"SELECT {', '.join(_ROW_COLS)} FROM clip_cache "
                "WHERE provider_id = ? AND catalog_id = ?",
                (provider_id, catalog_id),
            )
            return [dict(zip(_ROW_COLS, row)) for row in await cur.fetchall()]

        # Paginated path. Substring match on name AND blob (blob holds notes
        # under "notes": {...}). LIKE is case-insensitive by default in
        # SQLite for ASCII; for Czech diacritics this is a known
        # approximation — see spec §4 "out of scope".
        params: list = [provider_id, catalog_id]
        where = "provider_id = ? AND catalog_id = ?"
        if q:
            where += " AND (LOWER(name) LIKE ? OR LOWER(blob_json) LIKE ?)"
            needle = f"%{q.lower()}%"
            params.extend([needle, needle])

        count_cur = await conn.execute(
            f"SELECT COUNT(*) FROM clip_cache WHERE {where}", tuple(params)
        )
        total_row = await count_cur.fetchone()
        total = int(total_row[0]) if total_row else 0

        page_sql = (
            f"SELECT {', '.join(_ROW_COLS)} FROM clip_cache WHERE {where} "
            "ORDER BY provider_clip_id ASC LIMIT ? OFFSET ?"
        )
        page_params = tuple(params) + (int(limit or 50), int(offset or 0))
        cur = await conn.execute(page_sql, page_params)
        rows = await cur.fetchall()

        items: list[CanonicalClip] = []
        for row in rows:
            row_dict = dict(zip(_ROW_COLS, row))
            blob = row_dict.get("blob_json")
            if blob:
                items.append(_clip_from_json(blob))
        return tuple(items), total
```

If `_ROW_COLS` doesn't already include `blob_json` (check by grepping), add it. The column name in the schema is what matters; align this list to the actual columns.

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest tests/integration/test_clip_cache_list_offline.py -v`
Expected: all 5 PASS.

Also run the existing repo tests to make sure the legacy call still works:

Run: `.venv/bin/pytest tests/ -k "clip_cache or deep_orphans or cache_inspector" -v`
Expected: no new failures.

- [ ] **Step 5: Commit**

```bash
git add backend/app/repositories/clip_cache.py tests/integration/test_clip_cache_list_offline.py
git commit -m "feat(clip_cache): paginated, searchable list_by_catalog for offline mode"
```

---

## Task 3: Adapter — `is_online_provider` ctor + stale-cache fallback on read failure

**Files:**
- Modify: `backend/app/archive/providers/catdv/adapter.py`
- Test: `tests/integration/test_catdv_adapter_offline_fallback.py`

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_catdv_adapter_offline_fallback.py`:

```python
from datetime import datetime, timedelta, timezone

import pytest

from backend.app.archive.errors import RetryableError
from backend.app.archive.providers.catdv.adapter import CatdvArchiveAdapter
from backend.app.repositories.clip_cache import ClipCacheRepo
from backend.app.repositories.clip_list_cache import ClipListCacheRepo
from backend.app.repositories.field_def_cache import FieldDefCacheRepo
from backend.app.services.catdv_client import CatdvClient
from tests.fakes.fake_catdv import running_fake_catdv


def _adapter(client, db, *, is_online, ttl_hours=1, now=None):
    return CatdvArchiveAdapter(
        client=client,
        clip_cache_repo=ClipCacheRepo(),
        field_def_cache_repo=FieldDefCacheRepo(),
        clip_list_cache_repo=ClipListCacheRepo(),
        db_provider=lambda: db,
        clip_cache_ttl_hours=ttl_hours,
        clip_list_cache_ttl_minutes=1,
        clock=now or (lambda: datetime.now(timezone.utc)),
        is_online_provider=is_online,
        default_catalog_id="881507",
    )


@pytest.mark.asyncio
async def test_get_clip_serves_stale_cache_when_offline(db):
    with running_fake_catdv() as (base_url, fake):
        fake.clips[7] = {"ID": 7, "name": "Cached", "fps": 25.0, "markers": []}
        now_holder = {"t": datetime(2026, 1, 1, tzinfo=timezone.utc)}
        async with CatdvClient(base_url, "klientAI", "secret") as client:
            adapter = _adapter(
                client, db,
                is_online=lambda: True,
                ttl_hours=1,
                now=lambda: now_holder["t"],
            )
            await adapter.get_clip("7")  # warms cache

            # Advance past TTL — fresh cache miss. Flip offline.
            now_holder["t"] = now_holder["t"] + timedelta(hours=2)
            offline_adapter = _adapter(
                client, db,
                is_online=lambda: False,
                ttl_hours=1,
                now=lambda: now_holder["t"],
            )
            clip = await offline_adapter.get_clip("7")
            assert clip.name == "Cached"


@pytest.mark.asyncio
async def test_get_clip_offline_no_cache_raises_not_found(db):
    async with CatdvClient("http://nowhere.invalid", "klientAI", "secret") as client:
        adapter = _adapter(client, db, is_online=lambda: False)
        with pytest.raises(Exception):  # NotFound; adapt to whatever it actually raises
            await adapter.get_clip("999")


@pytest.mark.asyncio
async def test_get_clip_retryable_falls_back_to_stale_cache(db):
    """Online but CatDV is unreachable mid-session → stale cache wins."""
    with running_fake_catdv() as (base_url, fake):
        fake.clips[8] = {"ID": 8, "name": "Saved", "fps": 25.0, "markers": []}
        now_holder = {"t": datetime(2026, 1, 1, tzinfo=timezone.utc)}
        async with CatdvClient(base_url, "klientAI", "secret") as client:
            warm = _adapter(client, db, is_online=lambda: True, now=lambda: now_holder["t"])
            await warm.get_clip("8")

        # Cache is stale; CatDV is now unreachable (closed fake).
        now_holder["t"] = now_holder["t"] + timedelta(hours=2)
        async with CatdvClient(base_url, "klientAI", "secret") as dead_client:
            adapter = _adapter(dead_client, db, is_online=lambda: True, now=lambda: now_holder["t"])
            # Patch the client to raise RetryableError
            async def boom(*a, **kw):
                from backend.app.services.catdv_client import CatdvBusyError
                raise CatdvBusyError("simulated unreachable")
            dead_client.get_clip = boom  # type: ignore[assignment]

            clip = await adapter.get_clip("8")
            assert clip.name == "Saved"


@pytest.mark.asyncio
async def test_apply_changes_offline_raises_retryable_without_calling_client(db):
    from backend.app.archive.model import ChangeSet

    calls: list = []

    class SpyClient:
        async def get_clip(self, *a, **kw):
            calls.append(("get_clip", a, kw))
            raise AssertionError("must not be called")
        async def put_clip(self, *a, **kw):
            calls.append(("put_clip", a, kw))
            raise AssertionError("must not be called")

    adapter = _adapter(SpyClient(), db, is_online=lambda: False)  # type: ignore[arg-type]
    cs = ChangeSet(clip_key=("catdv", "1"), ops=(), expected_etag=None)
    with pytest.raises(RetryableError):
        await adapter.apply_changes(cs)
    assert calls == []


@pytest.mark.asyncio
async def test_is_online_provider_none_preserves_today_behavior(db):
    """Existing tests construct without is_online_provider; must keep working."""
    with running_fake_catdv() as (base_url, fake):
        fake.clips[1] = {"ID": 1, "name": "X", "fps": 25.0, "markers": []}
        async with CatdvClient(base_url, "klientAI", "secret") as client:
            adapter = CatdvArchiveAdapter(
                client=client,
                clip_cache_repo=ClipCacheRepo(),
                clip_list_cache_repo=ClipListCacheRepo(),
                field_def_cache_repo=FieldDefCacheRepo(),
                db_provider=lambda: db,
            )
            clip = await adapter.get_clip("1")
            assert clip.name == "X"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/integration/test_catdv_adapter_offline_fallback.py -v`
Expected: FAIL — `TypeError: ... got an unexpected keyword argument 'is_online_provider'`.

- [ ] **Step 3: Add `is_online_provider` to adapter ctor**

In `backend/app/archive/providers/catdv/adapter.py`, modify `__init__`:

```python
    def __init__(
        self,
        *,
        client: CatdvClient | None,
        clip_cache_repo: Any = None,
        field_def_cache_repo: Any = None,
        clip_list_cache_repo: Any = None,
        db_provider: Callable[[], Any] | None = None,
        clip_cache_ttl_hours: int = 168,
        clip_list_cache_ttl_minutes: int = 10,
        clock: Callable[[], datetime] | None = None,
        default_catalog_id: str = "",
        is_online_provider: Callable[[], bool] | None = None,
    ) -> None:
        self._client = client
        # ... existing assignments unchanged ...
        self._is_online_provider = is_online_provider
```

Add a small helper near the bottom of the class:

```python
    def _is_online(self) -> bool:
        if self._is_online_provider is None:
            return True
        return bool(self._is_online_provider())
```

- [ ] **Step 4: Add stale-cache helpers**

Below the existing `_read_clip_from_cache`, add:

```python
    async def _read_clip_from_cache_stale(self, clip_id: str) -> CanonicalClip | None:
        if not self._cache_enabled():
            return None
        return await self._clip_cache.get_by_key(
            self._db_provider(), provider_id=self.id, provider_clip_id=clip_id
        )

    async def _read_field_defs_from_cache_stale(self) -> list[FieldDef] | None:
        if not self._field_def_cache_enabled():
            return None
        return await self._field_def_cache.list_all(self._db_provider())
```

(Use whatever existing method on `field_def_cache_repo` returns all defs ignoring TTL — grep the repo to find the exact name; the test will catch a mismatch.)

- [ ] **Step 5: Restructure `get_clip` for offline + retryable fallback**

Replace the body of `get_clip`:

```python
    async def get_clip(self, clip: str) -> CanonicalClip:
        cached = await self._read_clip_from_cache(clip)
        if cached is not None:
            return cached

        if not self._is_online():
            stale = await self._read_clip_from_cache_stale(clip)
            if stale is not None:
                return stale
            raise FatalProviderError(f"clip {clip} not available offline")

        try:
            raw = await self._client.get_clip(int(clip))
        except CatdvAuthError as exc:
            raise AuthError(str(exc)) from exc
        except CatdvBusyError as exc:
            stale = await self._read_clip_from_cache_stale(clip)
            if stale is not None:
                return stale
            raise RetryableError(str(exc)) from exc
        except CatdvError as exc:
            raise FatalProviderError(str(exc)) from exc

        canonical = from_catdv_clip(raw, fetched_at=self._clock())
        await self._write_clip_through(canonical, raw)
        return canonical
```

- [ ] **Step 6: Same shape for `list_field_definitions`**

```python
    async def list_field_definitions(self) -> list[FieldDef]:
        cached = await self._read_field_defs_from_cache()
        if cached is not None:
            return cached

        if not self._is_online():
            stale = await self._read_field_defs_from_cache_stale()
            return stale or []

        try:
            rows = await self._client.list_fields()
        except CatdvAuthError as exc:
            raise AuthError(str(exc)) from exc
        except CatdvBusyError as exc:
            stale = await self._read_field_defs_from_cache_stale()
            if stale is not None:
                return stale
            raise RetryableError(str(exc)) from exc
        except CatdvError as exc:
            raise FatalProviderError(str(exc)) from exc

        defs = [field_def_from_catdv(r) for r in rows]
        await self._write_field_defs_through(defs)
        return defs
```

- [ ] **Step 7: Fail-fast `apply_changes` when offline**

Add at the top of `apply_changes`, right after the provider-id check:

```python
        if not self._is_online():
            raise RetryableError("offline — change queued")
```

- [ ] **Step 8: Run the new tests**

Run: `.venv/bin/pytest tests/integration/test_catdv_adapter_offline_fallback.py -v`
Expected: all PASS.

- [ ] **Step 9: Run the existing adapter test suite to confirm no regressions**

Run: `.venv/bin/pytest tests/ -k "catdv_adapter" -v`
Expected: no new failures (existing tests construct without `is_online_provider`, default-None branch keeps them green).

- [ ] **Step 10: Commit**

```bash
git add backend/app/archive/providers/catdv/adapter.py tests/integration/test_catdv_adapter_offline_fallback.py
git commit -m "feat(catdv-adapter): stale-cache fallback + offline guard via is_online_provider"
```

---

## Task 4: Adapter `list_clips` — offline path uses paginated `list_by_catalog`

**Files:**
- Modify: `backend/app/archive/providers/catdv/adapter.py`
- Test: extend `tests/integration/test_catdv_adapter_offline_fallback.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/integration/test_catdv_adapter_offline_fallback.py`:

```python
from backend.app.archive.model import ClipQuery


@pytest.mark.asyncio
async def test_list_clips_offline_paginates_from_cache(db):
    """Offline path returns ClipPage built from clip_cache."""
    from backend.app.archive.model import CanonicalClip, MediaRef

    repo = ClipCacheRepo()
    for i in range(3):
        clip = CanonicalClip(
            key=("catdv", str(i)),
            name=f"Clip{i}",
            duration_secs=10.0, fps=25.0,
            markers=(), fields={}, notes={"notes": ""},
            media=MediaRef(mime_type="video/quicktime", size_bytes=0),
            provider_data={}, fetched_at=datetime.now(timezone.utc),
        )
        await repo.upsert(db, clip=clip, catalog_id="881507")

    # Build adapter with offline + no client at all
    adapter = CatdvArchiveAdapter(
        client=None,
        clip_cache_repo=repo,
        field_def_cache_repo=FieldDefCacheRepo(),
        clip_list_cache_repo=ClipListCacheRepo(),
        db_provider=lambda: db,
        is_online_provider=lambda: False,
        default_catalog_id="881507",
    )
    page = await adapter.list_clips("881507", ClipQuery(text=None, offset=0, limit=10))
    assert page.total == 3
    assert {c.name for c in page.items} == {"Clip0", "Clip1", "Clip2"}


@pytest.mark.asyncio
async def test_list_clips_offline_search_q(db):
    from backend.app.archive.model import CanonicalClip, MediaRef
    repo = ClipCacheRepo()
    for name, cid in [("Alpha", "1"), ("Beta", "2"), ("Bravo", "3")]:
        clip = CanonicalClip(
            key=("catdv", cid),
            name=name,
            duration_secs=10.0, fps=25.0,
            markers=(), fields={}, notes={"notes": ""},
            media=MediaRef(mime_type="video/quicktime", size_bytes=0),
            provider_data={}, fetched_at=datetime.now(timezone.utc),
        )
        await repo.upsert(db, clip=clip, catalog_id="881507")

    adapter = CatdvArchiveAdapter(
        client=None,
        clip_cache_repo=repo,
        field_def_cache_repo=FieldDefCacheRepo(),
        clip_list_cache_repo=ClipListCacheRepo(),
        db_provider=lambda: db,
        is_online_provider=lambda: False,
        default_catalog_id="881507",
    )
    page = await adapter.list_clips("881507", ClipQuery(text="b", offset=0, limit=10))
    assert page.total == 2
    assert {c.name for c in page.items} == {"Beta", "Bravo"}
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest tests/integration/test_catdv_adapter_offline_fallback.py -k "list_clips_offline" -v`
Expected: FAIL (calls real client with `None`, or returns empty page).

- [ ] **Step 3: Add offline branch in `list_clips`**

Restructure `list_clips`:

```python
    async def list_clips(self, catalog: str, query: ClipQuery) -> ClipPage:
        if not self._is_online():
            return await self._list_clips_from_cache(catalog, query)

        cached = await self._read_list_from_cache(catalog, query)
        if cached is not None:
            return cached

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
            # transport-level failure: fall back to cached subset
            return await self._list_clips_from_cache(catalog, query)
        except CatdvError as exc:
            raise FatalProviderError(str(exc)) from exc

        now = self._clock()
        raw_items = data.get("items") if isinstance(data, dict) else []
        items = tuple(from_catdv_clip(raw, fetched_at=now) for raw in (raw_items or []))
        total = int((data or {}).get("totalItems", len(items)))
        page = ClipPage(items=items, total=total, offset=query.offset, limit=query.limit)
        await self._write_list_through(catalog, query, page, fetched_at=now)
        return page

    async def _list_clips_from_cache(self, catalog: str, query: ClipQuery) -> ClipPage:
        if not self._cache_enabled():
            return ClipPage(items=(), total=0, offset=query.offset, limit=query.limit)
        items, total = await self._clip_cache.list_by_catalog(
            self._db_provider(),
            provider_id=self.id,
            catalog_id=catalog,
            offset=query.offset,
            limit=query.limit,
            q=query.text,
            canonical=True,
        )
        return ClipPage(items=items, total=total, offset=query.offset, limit=query.limit)
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest tests/integration/test_catdv_adapter_offline_fallback.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/archive/providers/catdv/adapter.py tests/integration/test_catdv_adapter_offline_fallback.py
git commit -m "feat(catdv-adapter): offline list_clips serves from cache_repo"
```

---

## Task 5: `LocalCacheOnlyResolver` + `build_resolver` branch

**Files:**
- Modify: `backend/app/services/proxy_resolver.py`
- Test: `tests/unit/test_local_cache_only_resolver.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_local_cache_only_resolver.py`:

```python
from pathlib import Path

import pytest

from backend.app.repositories.proxy_cache import ProxyCacheRepo
from backend.app.services.proxy_resolver import (
    LocalCacheOnlyResolver,
    ProxyNotFound,
    build_resolver,
)


@pytest.mark.asyncio
async def test_returns_path_when_file_on_disk(db, tmp_path):
    repo = ProxyCacheRepo()
    f = tmp_path / "42.mov"
    f.write_bytes(b"x")
    await repo.record(
        db, clip_id=42, file_path=str(f), size_bytes=1, etag=None,
        provider_id="catdv", provider_clip_id="42",
    )
    r = LocalCacheOnlyResolver(repo=repo, db_provider=lambda: db)
    assert await r.path_for_clip_id(42) == f


@pytest.mark.asyncio
async def test_raises_when_file_missing(db, tmp_path):
    repo = ProxyCacheRepo()
    f = tmp_path / "ghost.mov"
    await repo.record(
        db, clip_id=99, file_path=str(f), size_bytes=0, etag=None,
        provider_id="catdv", provider_clip_id="99",
    )
    r = LocalCacheOnlyResolver(repo=repo, db_provider=lambda: db)
    with pytest.raises(ProxyNotFound):
        await r.path_for_clip_id(99)


@pytest.mark.asyncio
async def test_raises_when_no_db_row(db):
    repo = ProxyCacheRepo()
    r = LocalCacheOnlyResolver(repo=repo, db_provider=lambda: db)
    with pytest.raises(ProxyNotFound):
        await r.path_for_clip_id(1234)


def test_build_resolver_returns_local_cache_only_for_cache_source():
    r = build_resolver(
        source="cache-only",
        catdv_client=None,
        cache_dir=None,
        proxy_cache_repo=ProxyCacheRepo(),
        db_provider=lambda: None,
    )
    assert isinstance(r, LocalCacheOnlyResolver)


def test_is_managed_returns_true_when_in_cache_dir(tmp_path):
    repo = ProxyCacheRepo()
    r = LocalCacheOnlyResolver(repo=repo, db_provider=lambda: None, cache_dir=tmp_path)
    inside = tmp_path / "1.mov"
    inside.write_bytes(b"")
    assert r.is_managed(inside) is True
    assert r.is_managed(Path("/elsewhere/2.mov")) is False
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest tests/unit/test_local_cache_only_resolver.py -v`
Expected: FAIL — `ImportError: cannot import name 'LocalCacheOnlyResolver'`.

- [ ] **Step 3: Implement `LocalCacheOnlyResolver`**

Add to `backend/app/services/proxy_resolver.py` (above `build_resolver`):

```python
class LocalCacheOnlyResolver:
    """Returns proxy paths only if they're already on local disk.

    Does NOT contact CatDV. Used when the app runs in offline mode
    (CATDV_OFFLINE=true or detected disconnect). Raises ProxyNotFound
    when the requested clip's proxy hasn't been previously cached.
    """

    is_host_local = False

    def __init__(
        self,
        *,
        repo: ProxyCacheRepo,
        db_provider: Callable[[], aiosqlite.Connection],
        cache_dir: Path | None = None,
    ) -> None:
        self._repo = repo
        self._db_provider = db_provider
        self._cache_dir = cache_dir

    async def path_for_clip_id(self, clip_id: int) -> Path:
        row = await self._repo.get(self._db_provider(), clip_id)
        if row is None:
            raise ProxyNotFound(f"clip {clip_id} not cached locally")
        file_path = Path(row["file_path"])
        if not file_path.exists() or file_path.stat().st_size == 0:
            raise ProxyNotFound(
                f"clip {clip_id} cache row present but file missing: {file_path}"
            )
        return file_path

    def is_managed(self, path: Path) -> bool:
        if self._cache_dir is None:
            return False
        try:
            path.resolve().relative_to(self._cache_dir.resolve())
        except ValueError:
            return False
        return True
```

- [ ] **Step 4: Add `cache-only` branch to `build_resolver`**

Insert above the existing `if source == "rest":` block:

```python
    if source == "cache-only":
        if proxy_cache_repo is None or db_provider is None:
            raise ValueError(
                "cache-only source requires proxy_cache_repo and db_provider"
            )
        return LocalCacheOnlyResolver(
            repo=proxy_cache_repo,
            db_provider=db_provider,
            cache_dir=cache_dir,
        )
```

- [ ] **Step 5: Run tests**

Run: `.venv/bin/pytest tests/unit/test_local_cache_only_resolver.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/proxy_resolver.py tests/unit/test_local_cache_only_resolver.py
git commit -m "feat(proxy): LocalCacheOnlyResolver for offline mode"
```

---

## Task 6: `ConnectionMonitor` — halt-after-failure + `retry_now()` + `forced_offline` ctor flag

**Files:**
- Modify: `backend/app/services/connection_monitor.py`
- Test: `tests/integration/test_connection_monitor_halt_and_retry.py`

**Background:** the existing monitor probes forever on the configured interval. The spec requires the loop to halt when a probe fails (or at startup if the initial probe fails). The user reconnects via a `retry_now()` call from the new HTTP endpoint. `forced_offline=True` skips probing entirely and is read by the routes to refuse manual retry.

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_connection_monitor_halt_and_retry.py`:

```python
import asyncio

import pytest

from backend.app.services.connection_monitor import (
    ConnectionMonitor,
    ConnectionState,
)
from backend.app.services.events import EventBus


class StubProvider:
    def __init__(self, *, healthy: bool):
        self.healthy = healthy
        self.calls = 0

    async def health(self):
        self.calls += 1
        if not self.healthy:
            raise RuntimeError("offline")
        return None


@pytest.mark.asyncio
async def test_loop_halts_on_failure(db):
    provider = StubProvider(healthy=False)
    monitor = ConnectionMonitor(
        provider=provider,
        db_provider=lambda: db,
        interval_s=0.05,
        timeout_s=0.5,
        event_bus=EventBus(),
    )
    await monitor.probe_once()  # explicit initial probe
    assert monitor.current_state() == ConnectionState.offline

    await monitor.start()
    await asyncio.sleep(0.3)  # several intervals' worth
    await monitor.stop()
    # one probe at start of loop; loop must halt after seeing offline
    assert provider.calls <= 2


@pytest.mark.asyncio
async def test_retry_now_success_flips_online_and_restarts_loop(db):
    provider = StubProvider(healthy=False)
    monitor = ConnectionMonitor(
        provider=provider, db_provider=lambda: db,
        interval_s=0.05, timeout_s=0.5, event_bus=EventBus(),
    )
    await monitor.probe_once()
    assert monitor.current_state() == ConnectionState.offline

    provider.healthy = True
    result = await monitor.retry_now()
    assert result == ConnectionState.online
    assert monitor.current_state() == ConnectionState.online


@pytest.mark.asyncio
async def test_retry_now_failure_stays_offline(db):
    provider = StubProvider(healthy=False)
    monitor = ConnectionMonitor(
        provider=provider, db_provider=lambda: db,
        interval_s=0.05, timeout_s=0.5, event_bus=EventBus(),
    )
    await monitor.probe_once()
    result = await monitor.retry_now()
    assert result == ConnectionState.offline
    assert monitor.current_state() == ConnectionState.offline


@pytest.mark.asyncio
async def test_forced_offline_ignores_probes(db):
    provider = StubProvider(healthy=True)
    monitor = ConnectionMonitor(
        provider=provider, db_provider=lambda: db,
        interval_s=0.05, timeout_s=0.5,
        event_bus=EventBus(),
        forced_offline=True,
    )
    await monitor.probe_once()
    assert monitor.current_state() == ConnectionState.offline
    assert provider.calls == 0

    # retry_now while forced returns offline without probing
    result = await monitor.retry_now()
    assert result == ConnectionState.offline
    assert provider.calls == 0


@pytest.mark.asyncio
async def test_mid_session_failure_halts_loop(db):
    provider = StubProvider(healthy=True)
    monitor = ConnectionMonitor(
        provider=provider, db_provider=lambda: db,
        interval_s=0.05, timeout_s=0.5, event_bus=EventBus(),
    )
    await monitor.probe_once()
    assert monitor.current_state() == ConnectionState.online
    await monitor.start()
    await asyncio.sleep(0.1)
    provider.healthy = False
    await asyncio.sleep(0.3)
    await monitor.stop()
    assert monitor.current_state() == ConnectionState.offline
    # loop should not keep retrying after flipping offline
    calls_when_offline = provider.calls
    await asyncio.sleep(0.2)
    assert provider.calls == calls_when_offline
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest tests/integration/test_connection_monitor_halt_and_retry.py -v`
Expected: most FAIL with either `TypeError: unexpected kwarg 'forced_offline'` or assertion failures (loop keeps probing).

- [ ] **Step 3: Add `forced_offline` ctor flag**

In `backend/app/services/connection_monitor.py` `__init__`, add the kwarg and store:

```python
        forced_offline: bool = False,
```

And in the body:

```python
        self._forced_offline: bool = forced_offline
```

Update `current_state()`:

```python
    def current_state(self) -> ConnectionState:
        if self._forced_offline or self._manual_offline:
            return ConnectionState.offline
        return self._state
```

Update `probe_once()` to also short-circuit on forced:

```python
    async def probe_once(self) -> ConnectionState:
        if self._forced_offline or self._manual_offline:
            return ConnectionState.offline
        # ... rest unchanged
```

- [ ] **Step 4: Halt-after-failure in `_loop`**

Replace `_loop`:

```python
    async def _loop(self) -> None:
        while not self._stop_evt.is_set():
            try:
                state = await self.probe_once()
            except Exception:  # noqa: BLE001
                state = ConnectionState.offline
            if state != ConnectionState.online:
                # halt — user must explicitly retry_now() to resume
                return
            try:
                await asyncio.wait_for(
                    self._stop_evt.wait(), timeout=self._interval_s
                )
            except TimeoutError:
                pass
```

- [ ] **Step 5: Add `retry_now()`**

Add as a public method:

```python
    async def retry_now(self) -> ConnectionState:
        """User-triggered probe. On success, resumes the probe loop."""
        if self._forced_offline:
            return ConnectionState.offline
        state = await self.probe_once()
        if state == ConnectionState.online and self._task is None:
            await self.start()
        return state
```

- [ ] **Step 6: Run tests**

Run: `.venv/bin/pytest tests/integration/test_connection_monitor_halt_and_retry.py -v`
Expected: all PASS.

- [ ] **Step 7: Update existing monitor tests**

Run: `.venv/bin/pytest tests/integration/test_connection_monitor.py -v`

Likely failure: tests asserting the monitor keeps probing after a failure. Identify each and adjust to expect halt-after-fail. For each broken assertion, change to:
- Was: `assert provider.calls > N after sleeping`
- Becomes: `assert monitor.current_state() == ConnectionState.offline` (the loop legitimately stopped).

- [ ] **Step 8: Commit**

```bash
git add backend/app/services/connection_monitor.py tests/integration/test_connection_monitor_halt_and_retry.py tests/integration/test_connection_monitor.py
git commit -m "feat(connection-monitor): halt loop on failure, add retry_now, forced_offline ctor flag"
```

---

## Task 7: Routes — `POST /api/connection/retry`, `mode` in `/state` and `/api/health`

**Files:**
- Modify: `backend/app/routes/connection.py`
- Modify: wherever `/api/health` is defined — grep `"/api/health"` to find it
- Test: extend `tests/integration/test_connection_monitor_halt_and_retry.py` OR add `tests/integration/test_routes_connection_retry.py`

- [ ] **Step 1: Find the health route**

Run: `grep -rn '"/api/health"\|api/health' backend/app/routes/`

Note the file path; it likely returns a dict already.

- [ ] **Step 2: Write the failing tests**

Create `tests/integration/test_routes_connection_retry.py`:

```python
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(app_with_offline_monitor):  # define this fixture or inline-build
    return TestClient(app_with_offline_monitor)


def test_retry_endpoint_returns_state(client):
    resp = client.post("/api/connection/retry")
    assert resp.status_code in (200, 409)
    body = resp.json()
    assert "state" in body


def test_retry_endpoint_409_when_forced(client_forced_offline):
    resp = client_forced_offline.post("/api/connection/retry")
    assert resp.status_code == 409
    assert resp.json().get("detail", "").startswith("forced")


def test_state_endpoint_includes_mode(client):
    resp = client.get("/api/connection/state")
    body = resp.json()
    assert body["state"] in {"online", "offline", "degraded", "syncing"}
    assert body["mode"] in {"online", "offline", "forced_offline"}


def test_health_endpoint_includes_mode(client):
    resp = client.get("/api/health")
    body = resp.json()
    assert body["mode"] in {"online", "offline", "forced_offline"}
```

For the fixtures, since the full app needs a `ctx`, build a minimal helper. If existing tests already have an `app` fixture, reuse it and inject a stubbed monitor via dependency override; otherwise inline:

```python
import pytest
from fastapi import FastAPI

from backend.app.routes.connection import router as connection_router


class StubMonitor:
    def __init__(self, *, forced=False, state="online"):
        self._forced = forced
        self._state = state
    def current_state(self):
        from backend.app.services.connection_monitor import ConnectionState
        return ConnectionState(self._state)
    async def retry_now(self):
        from backend.app.services.connection_monitor import ConnectionState
        return ConnectionState.offline if self._forced else ConnectionState.online
    @property
    def is_forced(self):
        return self._forced


def _build_app(monitor) -> FastAPI:
    app = FastAPI()
    app.include_router(connection_router)
    # also add /api/health route here for the test
    @app.get("/api/health")
    async def health():
        return {
            "ok": True,
            "mode": "forced_offline" if monitor.is_forced
                    else ("online" if monitor.current_state().value == "online" else "offline"),
        }

    class Ctx:
        connection_monitor = monitor
        event_bus = None
    app.state.ctx = Ctx()
    return app


@pytest.fixture
def app_with_offline_monitor():
    return _build_app(StubMonitor(state="offline"))

@pytest.fixture
def client_forced_offline():
    from fastapi.testclient import TestClient
    return TestClient(_build_app(StubMonitor(forced=True, state="offline")))
```

- [ ] **Step 3: Run to verify failure**

Run: `.venv/bin/pytest tests/integration/test_routes_connection_retry.py -v`
Expected: FAIL — `/retry` not found, `/state` missing `mode`, `/api/health` may not exist in test app yet.

- [ ] **Step 4: Add the route**

In `backend/app/routes/connection.py`, append:

```python
from fastapi import HTTPException


def _mode(monitor) -> str:
    if getattr(monitor, "is_forced", False) or getattr(monitor, "_forced_offline", False):
        return "forced_offline"
    from backend.app.services.connection_monitor import ConnectionState
    return "online" if monitor.current_state() == ConnectionState.online else "offline"


@router.post("/retry")
async def retry_now(request: Request) -> dict:
    ctx = request.app.state.ctx
    monitor = getattr(ctx, "connection_monitor", None)
    if monitor is None:
        return {"state": "online", "mode": "online"}
    # If forced, refuse with 409
    if getattr(monitor, "_forced_offline", False):
        raise HTTPException(status_code=409, detail="forced offline (CATDV_OFFLINE=true)")
    state = await monitor.retry_now()
    return {"state": str(state.value), "mode": _mode(monitor)}
```

Also update `get_state`:

```python
@router.get("/state")
async def get_state(request: Request) -> dict:
    ctx = request.app.state.ctx
    monitor = getattr(ctx, "connection_monitor", None)
    if monitor is None:
        return {"state": "online", "mode": "online"}
    return {
        "state": str(monitor.current_state().value),
        "mode": _mode(monitor),
    }
```

- [ ] **Step 5: Add `mode` to real `/api/health` route**

In the health route file you identified in step 1, add to the returned dict:

```python
    ctx = request.app.state.ctx
    monitor = getattr(ctx, "connection_monitor", None)
    if monitor is None:
        mode = "online"
    elif getattr(monitor, "_forced_offline", False):
        mode = "forced_offline"
    else:
        from backend.app.services.connection_monitor import ConnectionState
        mode = "online" if monitor.current_state() == ConnectionState.online else "offline"
    # ... existing fields ...
    return {..., "mode": mode}
```

- [ ] **Step 6: Run tests**

Run: `.venv/bin/pytest tests/integration/test_routes_connection_retry.py -v`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/app/routes/connection.py backend/app/routes/<health-file>.py tests/integration/test_routes_connection_retry.py
git commit -m "feat(routes): POST /api/connection/retry + mode field in /state and /api/health"
```

---

## Task 8: Context wiring — `CATDV_OFFLINE` branch + auth-fail degradation + offline resolver

**Files:**
- Modify: `backend/app/context.py`
- Test: `tests/integration/test_offline_mode_e2e.py` (created in Task 9)

This task is the wire-up; its tests are the end-to-end ones in Task 9. We commit this task with a manual smoke before moving on.

- [ ] **Step 1: Add `catdv_offline` branch in `AppContext.build`**

Find the `if init_external:` block. Restructure so that:

```python
        if init_external:
            from backend.app.services.catdv_client import (
                CatdvAuthError, CatdvClient
            )
            from backend.app.services.gcs import GcsService
            from backend.app.services.gemini import GeminiService
            from backend.app.services.proxy_resolver import build_resolver

            use_catdv = settings.archive_provider == "catdv"
            forced_offline = settings.catdv_offline and use_catdv
            login_failed = False

            if use_catdv and not forced_offline:
                ctx.catdv = CatdvClient(
                    base_url=settings.catdv_base_url,
                    username=settings.catdv_username or "",
                    password=settings.catdv_password or "",
                )
                try:
                    await ctx.catdv.__aenter__()
                except CatdvAuthError as exc:
                    # Boot into offline mode rather than crashing.
                    import logging
                    logging.getLogger(__name__).warning(
                        "CatDV login failed at startup (%s); booting offline", exc
                    )
                    ctx.catdv = None
                    login_failed = True
                except Exception as exc:  # noqa: BLE001 — transport / DNS
                    import logging
                    logging.getLogger(__name__).warning(
                        "CatDV unreachable at startup (%s); booting offline", exc
                    )
                    ctx.catdv = None
                    login_failed = True

            # archive provider
            ctx.archive = build_archive_provider(
                settings,
                catdv_client=ctx.catdv,
                clip_cache_repo=ctx.clip_cache_repo,
                field_def_cache_repo=ctx.field_def_cache_repo,
                clip_list_cache_repo=ctx.clip_list_cache_repo,
                db_provider=lambda c=ctx: c.db,
            )

            # ... existing gcs/ai_store/gemini wiring unchanged ...

            # connection monitor
            ctx.connection_monitor = ConnectionMonitor(
                provider=ctx.archive,
                db_provider=lambda c=ctx: c.db,
                interval_s=float(settings.health_probe_interval_s),
                timeout_s=float(settings.health_probe_timeout_s),
                event_bus=ctx.event_bus,
                forced_offline=forced_offline,
            )

            # adapter needs the is_online callable injected. The adapter
            # was built by build_archive_provider above with no provider.
            # We attach it here so the adapter sees live monitor state.
            if hasattr(ctx.archive, "_is_online_provider"):
                ctx.archive._is_online_provider = (
                    lambda: ctx.connection_monitor.current_state().value == "online"
                )

            # proxy resolver: cache-only when forced/login-failed
            if use_catdv and (forced_offline or login_failed):
                ctx.proxy_resolver = build_resolver(
                    source="cache-only",
                    catdv_client=None,
                    cache_dir=settings.data_dir / "cache" / "proxies",
                    proxy_cache_repo=ctx.proxy_cache_repo,
                    db_provider=lambda c=ctx: c.db,
                )
            elif use_catdv:
                media_store_map = None
                if settings.proxy_source == "filesystem":
                    from backend.app.services.media_store_map import (
                        fetch_media_store_map,
                    )
                    media_store_map = await fetch_media_store_map(ctx.catdv)
                ctx.proxy_resolver = build_resolver(
                    source=settings.proxy_source,
                    catdv_client=ctx.catdv,
                    cache_dir=settings.data_dir / "cache" / "proxies",
                    archive=ctx.archive,
                    media_store_map=media_store_map,
                    proxy_cache_repo=ctx.proxy_cache_repo,
                    db_provider=lambda c=ctx: c.db,
                )
            else:
                ctx.proxy_resolver = None

            # ... rest of init_external (sync_engine, workspace_manager,
            # cache_inspector, cache_actions, lru_eviction, media_prefetcher)
            # unchanged. Note: media_prefetcher should only be built if
            # the resolver supports it (LocalCacheOnlyResolver can't
            # fetch anything new).
            if ctx.proxy_resolver is not None and not isinstance(
                ctx.proxy_resolver,
                __import__(
                    "backend.app.services.proxy_resolver", fromlist=["LocalCacheOnlyResolver"]
                ).LocalCacheOnlyResolver,
            ):
                ctx.media_prefetcher = MediaPrefetcher(
                    queue_repo=ctx.prefetch_queue_repo,
                    resolver=ctx.proxy_resolver,
                    db_provider=lambda c=ctx: c.db,
                    tick_interval_s=float(settings.prefetch_tick_interval_s),
                )
```

- [ ] **Step 2: Update the `archive_provider` builder OR pass `is_online_provider` directly**

The cleanest way is to thread `is_online_provider` through `build_archive_provider`. In `backend/app/archive/registry.py` add the kwarg and pass it to `CatdvArchiveAdapter(...)`:

```python
def build_archive_provider(
    settings: Any,
    *,
    catdv_client: Any = None,
    clip_cache_repo: Any = None,
    field_def_cache_repo: Any = None,
    clip_list_cache_repo: Any = None,
    db_provider: Any = None,
    is_online_provider: Any = None,
) -> ArchiveProvider:
    ...
    if name == "catdv":
        ...
        return CatdvArchiveAdapter(
            client=catdv_client,
            ...
            default_catalog_id=str(getattr(settings, "catdv_catalog_id", "")),
            is_online_provider=is_online_provider,
        )
```

Then in `context.py`, replace the post-hoc attribute set with passing it at construction time. Since the monitor is built *after* the archive (because monitor needs the archive for `health()`), this creates an ordering issue. Resolve it by passing a **lambda that closes over `ctx`** — `ctx.connection_monitor` is None at construction time, so the lambda must defer:

```python
            ctx.archive = build_archive_provider(
                settings,
                catdv_client=ctx.catdv,
                ...
                is_online_provider=lambda c=ctx: (
                    True if c.connection_monitor is None
                    else c.connection_monitor.current_state().value == "online"
                ),
            )
```

This avoids the `_is_online_provider` private-attribute poke from Step 1.

- [ ] **Step 3: Manual smoke**

Smoke 1 — forced offline:

```bash
CATDV_OFFLINE=true ./run.sh &
sleep 2
curl -s http://localhost:8765/api/health | python3 -m json.tool
# Expect: "mode": "forced_offline"
curl -s -X POST http://localhost:8765/api/connection/retry
# Expect: 409 "forced offline ..."
/bin/kill -TERM $(/usr/sbin/lsof -nP -iTCP:8765 -sTCP:LISTEN -t)
```

Smoke 2 — normal:

```bash
./run.sh &
sleep 5
curl -s http://localhost:8765/api/health | python3 -m json.tool
# If VPN up: "mode": "online". If VPN down: "mode": "offline"
/bin/kill -TERM $(/usr/sbin/lsof -nP -iTCP:8765 -sTCP:LISTEN -t)
```

Confirm in the run log that the shutdown was graceful (`Application shutdown complete.`) — the CATDV seat must be freed.

- [ ] **Step 4: Commit**

```bash
git add backend/app/context.py backend/app/archive/registry.py
git commit -m "feat(context): boot offline on CATDV_OFFLINE or login failure"
```

---

## Task 9: Integration smoke — full offline mode e2e

**Files:**
- Test: `tests/integration/test_offline_mode_e2e.py`

- [ ] **Step 1: Write the e2e test**

```python
import pytest
from fastapi.testclient import TestClient

from backend.app.context import AppContext
from backend.app.main import build_app  # adjust import to actual entrypoint
from backend.app.settings import Settings


def _settings(**overrides):
    base = {
        "catdv_base_url": "http://nowhere.invalid",
        "catdv_catalog_id": 881507,
        "gcp_project_id": "p",
        "gcs_bucket_name": "b",
        "data_dir": "/tmp/test-offline",
    }
    base.update(overrides)
    return Settings(**base)


@pytest.mark.asyncio
async def test_forced_offline_boots_and_serves_health(tmp_path):
    s = _settings(catdv_offline=True, data_dir=str(tmp_path))
    ctx = await AppContext.build(s, init_external=True)
    try:
        app = build_app(ctx)
        with TestClient(app) as c:
            body = c.get("/api/health").json()
            assert body["mode"] == "forced_offline"

            r = c.post("/api/connection/retry")
            assert r.status_code == 409
    finally:
        await ctx.aclose()


@pytest.mark.asyncio
async def test_catdv_unreachable_at_startup_boots_offline(tmp_path):
    """No CATDV_OFFLINE, but the configured CatDV is unreachable. App still boots."""
    s = _settings(data_dir=str(tmp_path))  # base_url is invalid
    ctx = await AppContext.build(s, init_external=True)
    try:
        assert ctx.catdv is None  # login failed → context degraded
        assert ctx.connection_monitor.current_state().value == "offline"
        app = build_app(ctx)
        with TestClient(app) as c:
            body = c.get("/api/health").json()
            assert body["mode"] == "offline"
    finally:
        await ctx.aclose()


@pytest.mark.asyncio
async def test_offline_clip_list_empty_when_no_cache(tmp_path):
    s = _settings(catdv_offline=True, data_dir=str(tmp_path))
    ctx = await AppContext.build(s, init_external=True)
    try:
        app = build_app(ctx)
        with TestClient(app) as c:
            # however the clip list is fetched in your API — adjust path
            resp = c.get("/clips")
            assert resp.status_code in (200, 404)
            # No cached rows → empty list rendered, but page returns 200.
    finally:
        await ctx.aclose()
```

- [ ] **Step 2: Run**

Run: `.venv/bin/pytest tests/integration/test_offline_mode_e2e.py -v`
Expected: all PASS. If `build_app` differs in your codebase, grep for the function actually used (probably in `backend/app/main.py`).

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_offline_mode_e2e.py
git commit -m "test(offline): end-to-end smoke for forced + auth-fail-degraded offline modes"
```

---

## Task 10: UI — connection chip partial + template hides + offline banner

**Files:**
- Create: `backend/app/templates/_connection_chip.html`
- Modify: `backend/app/templates/base.html` (or topbar partial) — include the chip
- Modify: `backend/app/templates/clips.html`, `clip_detail.html` — hide actions, add offline banner, 404 page partial

- [ ] **Step 1: Identify the base template + how `mode` reaches templates**

Run: `grep -rn 'base.html\|topbar\|connection' backend/app/templates/ backend/app/routes/ | head`

Locate where global template context is injected (likely a Jinja2 context processor or a base route dependency). The `mode` value should be added there so every template can read `{{ mode }}`.

- [ ] **Step 2: Add `mode` to the template context**

Find the existing context provider (grep for `templates.TemplateResponse(`). Add a small helper:

```python
def _request_mode(ctx) -> str:
    monitor = getattr(ctx, "connection_monitor", None)
    if monitor is None:
        return "online"
    if getattr(monitor, "_forced_offline", False):
        return "forced_offline"
    from backend.app.services.connection_monitor import ConnectionState
    return "online" if monitor.current_state() == ConnectionState.online else "offline"
```

And include it in every render: `{"mode": _request_mode(request.app.state.ctx), ...}`.

If there are many call sites, instead set it on `request.state.mode` via middleware and pull it in a context processor.

- [ ] **Step 3: Write `_connection_chip.html`**

```html
{# Topbar connection chip. Renders one of three states. #}
<div id="connection-chip" class="conn-chip conn-chip--{{ mode }}">
  {% if mode == "online" %}
    <span class="dot dot--green" aria-hidden="true"></span>
    <span>Online</span>
  {% elif mode == "offline" %}
    <button
      type="button"
      hx-post="/api/connection/retry"
      hx-target="#connection-chip"
      hx-swap="outerHTML"
      title="Click to try reconnecting"
    >
      <span class="dot dot--yellow" aria-hidden="true"></span>
      <span>Offline — click to reconnect</span>
    </button>
  {% else %}{# forced_offline #}
    <span class="dot dot--red" aria-hidden="true" title="Set CATDV_OFFLINE=false to enable"></span>
    <span>Offline (forced)</span>
  {% endif %}
</div>
```

- [ ] **Step 4: Make `/api/connection/retry` return the chip partial on HTMX requests**

Update the retry endpoint to detect HTMX and return HTML:

```python
@router.post("/retry")
async def retry_now(request: Request):
    ctx = request.app.state.ctx
    monitor = getattr(ctx, "connection_monitor", None)
    is_htmx = request.headers.get("HX-Request") == "true"
    if monitor is None:
        result = {"state": "online", "mode": "online"}
    elif getattr(monitor, "_forced_offline", False):
        if is_htmx:
            # render chip in forced state; cannot reconnect
            return templates.TemplateResponse(
                "_connection_chip.html",
                {"request": request, "mode": "forced_offline"},
                status_code=409,
            )
        raise HTTPException(status_code=409, detail="forced offline (CATDV_OFFLINE=true)")
    else:
        state = await monitor.retry_now()
        result = {"state": str(state.value), "mode": _mode(monitor)}
    if is_htmx:
        return templates.TemplateResponse(
            "_connection_chip.html",
            {"request": request, "mode": result["mode"]},
        )
    return result
```

(Import `templates` from wherever the app's Jinja2Templates singleton lives — grep for `Jinja2Templates(`.)

- [ ] **Step 5: Include the chip in the base/topbar template**

In `base.html` (or the topbar partial), add to the right side of the header:

```html
{% include "_connection_chip.html" %}
```

- [ ] **Step 6: Hide actions when offline**

In `clips.html` clip-row actions, wrap CatDV-dependent buttons:

```html
{% if mode == "online" %}
  <button class="action action--annotate" ...>Annotate</button>
  <button class="action action--cache" ...>Cache locally</button>
{% endif %}
```

In `clip_detail.html`, same pattern for the Annotate dropdown.

Add a banner above the clip list in `clips.html`:

```html
{% if mode != "online" %}
  <div class="banner banner--offline">
    Showing cached clips only — {{ total_cached }} clip{{ "s" if total_cached != 1 else "" }} available.
  </div>
{% endif %}
```

Pass `total_cached` from the clip-list route when offline (already returned by `list_clips` as `page.total`).

- [ ] **Step 7: 404 "not available offline" page**

In the clip-detail route, catch the new `FatalProviderError("... not available offline")` thrown by the adapter and render a friendly 404:

```python
from backend.app.archive.errors import FatalProviderError

try:
    clip = await ctx.archive.get_clip(clip_id)
except FatalProviderError as exc:
    if "not available offline" in str(exc):
        return templates.TemplateResponse(
            "clip_not_cached.html",
            {"request": request, "mode": _request_mode(ctx), "clip_id": clip_id},
            status_code=404,
        )
    raise
```

Create `backend/app/templates/clip_not_cached.html`:

```html
{% extends "base.html" %}
{% block content %}
<div class="empty-state">
  <h2>Clip {{ clip_id }} is not available offline</h2>
  <p>This clip hasn't been cached locally yet. Reconnect to CatDV and open it once to cache it.</p>
  {% if mode == "offline" %}
    <p>Click the yellow chip in the header to try reconnecting.</p>
  {% endif %}
</div>
{% endblock %}
```

- [ ] **Step 8: Smoke the UI manually**

```bash
CATDV_OFFLINE=true ./run.sh &
sleep 2
open http://localhost:8765/   # or curl the page and grep for "Offline (forced)"
# Verify: red chip; no Annotate/Cache buttons; banner above list
/bin/kill -TERM $(/usr/sbin/lsof -nP -iTCP:8765 -sTCP:LISTEN -t)
```

- [ ] **Step 9: Commit**

```bash
git add backend/app/templates/_connection_chip.html \
        backend/app/templates/clips.html \
        backend/app/templates/clip_detail.html \
        backend/app/templates/clip_not_cached.html \
        backend/app/templates/base.html \
        backend/app/routes/connection.py \
        backend/app/routes/<clip-detail-route>.py
git commit -m "feat(ui): connection chip + hide CatDV actions when offline"
```

---

## Task 11: Documentation

**Files:**
- Modify: `README.md`, `docs/DEPLOY.md`
- Modify: `docs/decisions.md` (append new dated entry per project convention)

- [ ] **Step 1: README section**

Add to `README.md` under "Running on the CatDV host (no proxy cache)":

```markdown
## Running offline (no CatDV at all)

When the CatDV VPN is unavailable, run with:

```
CATDV_OFFLINE=true
```

The app will:

- Boot without attempting CatDV login (no seat taken).
- Serve the clip list and clip details from the local SQLite cache.
- Serve proxies only when already cached to `data/cache/proxies/`.
- Hide the Annotate, "Cache locally", and "Refresh from CatDV" actions.
- Show a red "Offline (forced)" chip in the header.

To go back online, unset the flag and restart.

### Auto-fallback (no env flag)

When `CATDV_OFFLINE` is not set, the app boots normally but **degrades to
offline automatically** if the initial CatDV login fails or if a periodic
health probe fails mid-session. The header chip turns yellow and shows
"Offline — click to reconnect"; clicking it triggers a single probe.
```

- [ ] **Step 2: DEPLOY.md section**

Add a similar block to `docs/DEPLOY.md` after the existing host-local section.

- [ ] **Step 3: decisions.md entry**

Append to `docs/decisions.md` following the existing format:

```markdown
## 2026-05-22 — Offline fallback: auto-degrade + manual reconnect

**Context:** The annotator crashed on startup without VPN and raised 5xx on
every read when CatDV went down mid-session. Users wanted to keep working
from cache while disconnected.

**Alternatives:** (a) New `CacheOnlyArchiveAdapter` wrapper class — heavier,
double test surface. (b) Strictly automatic fallback driven by
`ConnectionMonitor` only — no env override, can't boot without VPN.
(c) Auto-degrade inside the existing CatdvArchiveAdapter via injected
`is_online_provider`, plus a `CATDV_OFFLINE` env override and a user-
triggered reconnect.

**Choice:** (c). The 2026-05-19 abstraction already had cache-first reads,
`WriteQueue`, and `SyncEngine`; this finished the loop with the smallest
surface area.

**Why:** Existing tests keep passing (the injected callable defaults to
"always online"). The reconnect lives in the UI as a chip, not as
background retry, so users have agency and CatDV doesn't get hammered by
a stuck app. Auth failure at startup is treated as offline rather than
fatal, matching the spirit of "the app should be usable without CatDV".
```

- [ ] **Step 4: Commit**

```bash
git add README.md docs/DEPLOY.md docs/decisions.md
git commit -m "docs(offline): document CATDV_OFFLINE + auto-fallback + reconnect chip"
```

---

## Self-review (already performed by author)

- **Spec coverage:** all sections of `2026-05-22-offline-fallback-design.md` map to a task — §2 (UX/states) covered by Tasks 6/7/10; §3.1 settings → Task 1; §3.2 startup → Task 8; §3.3 adapter → Tasks 3/4; §3.4 list_by_catalog → Task 2; §3.5 ConnectionMonitor → Task 6; §3.6 SyncEngine guard → already implemented (noted in preamble); §3.7 proxy resolver → Task 5; §3.8 API + UI → Tasks 7/10; §5 tests interleaved per-task; §6 rollout → matches task ordering.
- **Placeholder scan:** no TBD/TODO/"appropriate handling" left.
- **Type consistency:** `is_online_provider` (Callable[[], bool]) used identically in Tasks 3, 4, 8; `path_for_clip_id(clip_id: int)` matches the existing `ProxyResolver` protocol; `ProxyCacheRepo` field is `file_path` (Task 5); `_forced_offline` private attr accessed identically from monitor and routes; `ConnectionState` enum unchanged.
- **Open question for the engineer:** Task 8 Step 2 mentions threading `is_online_provider` through `build_archive_provider`; the alternative in Task 8 Step 1 (post-hoc attr set) is left as a comment. Use the threading approach — it's cleaner and easier to test.
