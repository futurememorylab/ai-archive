# Clips List Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the dense table-style clips list with a media-row layout that shows each clip's CatDV poster image and a 2-line notes excerpt (expandable), defaults page size to 20, and adds a disk-cached `/api/poster/{clip_id}` route.

**Architecture:** Posters and notes already arrive in the existing `clip_list_cache` payload, so no new CatDV list calls. A new FastAPI route proxies `/catdv/api/9/clips/{id}/poster` to disk-cached JPEGs that the browser caches indefinitely via a versioned URL (`?v={poster_id}`). The clips template becomes a `<ul>` of `<li class="clip-row">` blocks with Alpine-driven inline expand for long notes.

**Tech Stack:** FastAPI, Jinja2, Alpine.js, htmx, httpx, pytest + `httpx.MockTransport`, plain CSS in `backend/app/static/app.css`. No new dependencies.

**Spec:** `docs/specs/2026-05-22-clips-list-redesign-design.md`.

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `backend/app/ui/view_models.py` | modify | `clip_summary()` adds `poster_id`, `notes_excerpt`, `notes_has_more`. |
| `backend/app/services/catdv_client.py` | modify | New `download_poster(clip_id) -> bytes` method. |
| `backend/app/services/poster_cache.py` | create | Disk-cache helper: `get_or_fetch(clip_id, fetcher)`; per-clip `asyncio.Lock` coalescing; atomic `.tmp → rename` write. |
| `backend/app/routes/posters.py` | create | `GET /api/poster/{clip_id}` — calls cache, returns `FileResponse` with immutable cache headers. |
| `backend/app/main.py` | modify | Mount `posters.router`. |
| `backend/app/routes/pages.py` | modify | Default `limit: int = 50` → `limit: int = 20`. |
| `backend/app/templates/pages/_clips_tbody.html` | modify | Replace `<table>` body with `<ul class="clip-list">` of `<li class="clip-row">`. Pager preserved. |
| `backend/app/templates/pages/clips.html` | modify | Wrap region in `x-data="{ expanded: {} }"` for per-row expand state. |
| `backend/app/static/app.css` | modify | Add `.clip-list`, `.clip-row*`, `.poster-fallback`, `.clip-row__notes.is-clamped` rules. Remove obsolete `.tbl .clip-name`, `.tbl .thumb` rules. |
| `backend/app/static/film-strip.svg` | create | Fallback glyph for posterless clips (used by `.poster-fallback` background). |
| `tests/unit/test_view_models.py` | modify | Add cases for the three new `clip_summary` keys. |
| `tests/unit/test_catdv_client_poster.py` | create | `httpx.MockTransport` test of `download_poster` happy path + 401-reauth retry. |
| `tests/unit/test_poster_cache.py` | create | Disk write + atomic rename + concurrent-fetch coalescing. |
| `tests/integration/test_posters_route.py` | create | `TestClient` route test with a fake fetcher. |
| `tests/integration/test_routes_pages.py` | modify | Assertions for default `limit=20`, `<img src="/api/poster/.../?v=...">` presence, notes-excerpt rendering, posterless fallback. |

---

## Task 1: Extend `clip_summary` view-model

**Files:**
- Modify: `backend/app/ui/view_models.py:57-70`
- Test: `tests/unit/test_view_models.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_view_models.py`:

```python
def test_clip_summary_carries_poster_id_from_provider_data():
    clip = _canonical(provider_data={"ID": 12041, "name": "x", "posterID": 882119})
    s = clip_summary(clip)
    assert s["poster_id"] == 882119


def test_clip_summary_poster_id_none_when_absent():
    clip = _canonical(provider_data={"ID": 12041, "name": "x"})
    s = clip_summary(clip)
    assert s["poster_id"] is None


def test_clip_summary_notes_excerpt_prefers_notes_over_bigNotes():
    clip = _canonical(provider_data={
        "ID": 1, "name": "x",
        "notes": "krátká poznámka",
        "bigNotes": "totally different text",
    })
    s = clip_summary(clip)
    assert s["notes_excerpt"] == "krátká poznámka"


def test_clip_summary_notes_excerpt_falls_back_to_bigNotes():
    clip = _canonical(provider_data={
        "ID": 1, "name": "x",
        "notes": "",
        "bigNotes": "fallback text",
    })
    s = clip_summary(clip)
    assert s["notes_excerpt"] == "fallback text"


def test_clip_summary_notes_excerpt_none_when_both_empty():
    clip = _canonical(provider_data={"ID": 1, "name": "x"})
    s = clip_summary(clip)
    assert s["notes_excerpt"] is None


def test_clip_summary_notes_has_more_true_when_long():
    long_text = "x" * 200
    clip = _canonical(provider_data={"ID": 1, "name": "x", "notes": long_text})
    s = clip_summary(clip)
    assert s["notes_has_more"] is True


def test_clip_summary_notes_has_more_true_when_multiline():
    clip = _canonical(provider_data={
        "ID": 1, "name": "x",
        "notes": "line a\nline b\nline c",
    })
    s = clip_summary(clip)
    assert s["notes_has_more"] is True


def test_clip_summary_notes_has_more_false_for_short_notes():
    clip = _canonical(provider_data={"ID": 1, "name": "x", "notes": "krátké"})
    s = clip_summary(clip)
    assert s["notes_has_more"] is False


def test_clip_summary_notes_excerpt_fixes_mojibake():
    # Single-mojibaked 'Žena s dítětem'
    bad = (
        b"\xc3\x85\xc2\xbdena s d"
        b"\xc3\x83\xc2\xadt"
        b"\xc3\x84\xc2\x9bte"
        b"m"
    ).decode("utf-8")
    clip = _canonical(provider_data={"ID": 1, "name": "x", "notes": bad})
    s = clip_summary(clip)
    assert s["notes_excerpt"] == "Žena s dítětem"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
.venv/bin/python -m pytest tests/unit/test_view_models.py -v -k "poster_id or notes_excerpt or notes_has_more"
```

Expected: 9 failures — `KeyError: 'poster_id'` / `'notes_excerpt'` / `'notes_has_more'`.

- [ ] **Step 3: Implement**

Replace the body of `clip_summary` in `backend/app/ui/view_models.py` (currently lines 57–70):

```python
def clip_summary(
    clip: CanonicalClip,
    cache_status: Any | None = None,
) -> dict[str, Any]:
    """One row in the clips-list table."""
    pd = clip.provider_data
    raw_notes = pd.get("notes")
    if not raw_notes:
        raw_notes = pd.get("bigNotes") or ""
    notes_excerpt = _fix(raw_notes) or None
    notes_has_more = bool(
        notes_excerpt
        and (len(notes_excerpt) > 140 or notes_excerpt.count("\n") >= 2)
    )
    return {
        "id": int(clip.key[1]),
        "name": _fix(clip.name),
        "duration_secs": clip.duration_secs,
        "year": _first_value(clip.fields.get(_YEAR_FIELD)),
        "decade": _first_value(clip.fields.get(_DECADE_FIELD)),
        "marker_count": len(clip.markers),
        "cache": cache_status_view(cache_status) if cache_status else None,
        "poster_id": pd.get("posterID"),
        "notes_excerpt": notes_excerpt,
        "notes_has_more": notes_has_more,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
.venv/bin/python -m pytest tests/unit/test_view_models.py -v
```

Expected: all green. The pre-existing tests must still pass.

- [ ] **Step 5: Commit**

```bash
git add backend/app/ui/view_models.py tests/unit/test_view_models.py
git commit -m "feat(view-models): clip_summary carries poster_id and notes excerpt"
```

---

## Task 2: `CatdvClient.download_poster`

**Files:**
- Modify: `backend/app/services/catdv_client.py` (add method after `download_proxy`, around line 155)
- Test: `tests/unit/test_catdv_client_poster.py` (new)

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_catdv_client_poster.py`:

```python
import httpx
import pytest

from backend.app.services.catdv_client import CatdvClient


@pytest.mark.asyncio
async def test_download_poster_returns_bytes_on_success():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.method} {request.url.path}")
        if request.url.path.endswith("/session"):
            return httpx.Response(200, json={"status": "OK", "data": {}})
        if request.url.path.endswith("/clips/42/poster"):
            return httpx.Response(200, content=b"\xff\xd8\xff\xe0JPEGBYTES")
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    async with CatdvClient(
        base_url="http://catdv.test",
        username="u",
        password="p",
        transport=transport,
    ) as client:
        data = await client.download_poster(42)

    assert data.startswith(b"\xff\xd8")
    assert "/clips/42/poster" in " ".join(calls)


@pytest.mark.asyncio
async def test_download_poster_reauthenticates_on_401():
    state = {"logged_in_count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/session") and request.method == "POST":
            state["logged_in_count"] += 1
            return httpx.Response(200, json={"status": "OK", "data": {}})
        if request.url.path.endswith("/clips/42/poster"):
            # First GET (after initial login) → 401. Re-login + retry → 200.
            if state["logged_in_count"] < 2:
                return httpx.Response(401)
            return httpx.Response(200, content=b"\xff\xd8AFTERRELOGIN")
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    async with CatdvClient(
        base_url="http://catdv.test",
        username="u",
        password="p",
        transport=transport,
    ) as client:
        data = await client.download_poster(42)

    assert data == b"\xff\xd8AFTERRELOGIN"
    assert state["logged_in_count"] == 2
```

(If the existing `CatdvClient` constructor does not accept a `transport=` kwarg, see Step 2 — the test signals that adding it is necessary for unit testability. Inspect `backend/app/services/catdv_client.py` first; if the constructor builds the httpx client internally, modify it to accept an optional `transport: httpx.AsyncBaseTransport | None = None` that it passes through to `httpx.AsyncClient(..., transport=transport)`. Keep the default `None` so production behaviour is unchanged.)

- [ ] **Step 2: Run tests to verify they fail**

```bash
.venv/bin/python -m pytest tests/unit/test_catdv_client_poster.py -v
```

Expected: failure — either `AttributeError: 'CatdvClient' object has no attribute 'download_poster'` or `TypeError` if `transport=` is not accepted yet.

- [ ] **Step 3: Make `CatdvClient` constructor accept an optional transport (if not already)**

Read `backend/app/services/catdv_client.py` lines 22–45 (the `__init__` / `__aenter__`). If `httpx.AsyncClient(...)` is constructed without a `transport` parameter, add one:

```python
def __init__(
    self,
    *,
    base_url: str,
    username: str,
    password: str,
    timeout: float = 30.0,
    transport: "httpx.AsyncBaseTransport | None" = None,
) -> None:
    ...
    self._transport = transport
```

And where `httpx.AsyncClient(...)` is built (in `__aenter__`):

```python
self._client = httpx.AsyncClient(
    base_url=self._base,
    timeout=self._timeout,
    transport=self._transport,
)
```

Match the existing parameter names exactly — read the file before editing.

- [ ] **Step 4: Add `download_poster`**

Append after the existing `download_proxy` method (around line 155):

```python
async def download_poster(self, clip_id: int) -> bytes:
    """Fetch the JPEG poster for a clip. Small blob; returned all at once.

    Reuses the existing CatDV session (no new seat consumed). Re-logs in
    once on 401 and retries.
    """
    if not self._logged_in:
        await self.login()
    url = f"{self._base}/catdv/api/9/clips/{clip_id}/poster"
    resp = await self.http.get(url)
    if resp.status_code == 401:
        await self.login()
        resp = await self.http.get(url)
    resp.raise_for_status()
    return resp.content
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
.venv/bin/python -m pytest tests/unit/test_catdv_client_poster.py -v
```

Expected: both tests pass.

- [ ] **Step 6: Re-run the full unit suite as a regression check**

```bash
.venv/bin/python -m pytest tests/unit -q
```

Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/catdv_client.py tests/unit/test_catdv_client_poster.py
git commit -m "feat(catdv-client): download_poster with 401 re-auth"
```

---

## Task 3: Poster disk-cache module

**Files:**
- Create: `backend/app/services/poster_cache.py`
- Test: `tests/unit/test_poster_cache.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_poster_cache.py`:

```python
import asyncio
from pathlib import Path

import pytest

from backend.app.services.poster_cache import PosterCache


@pytest.mark.asyncio
async def test_cache_miss_calls_fetcher_and_writes_file(tmp_path: Path):
    cache = PosterCache(tmp_path)
    calls: list[int] = []

    async def fetcher(clip_id: int) -> bytes:
        calls.append(clip_id)
        return b"\xff\xd8POSTER"

    path = await cache.get_or_fetch(42, fetcher)

    assert path == tmp_path / "42.jpg"
    assert path.read_bytes() == b"\xff\xd8POSTER"
    assert calls == [42]


@pytest.mark.asyncio
async def test_cache_hit_skips_fetcher(tmp_path: Path):
    (tmp_path / "7.jpg").write_bytes(b"already here")
    cache = PosterCache(tmp_path)

    async def fetcher(clip_id: int) -> bytes:
        raise AssertionError("fetcher must not be called on hit")

    path = await cache.get_or_fetch(7, fetcher)
    assert path.read_bytes() == b"already here"


@pytest.mark.asyncio
async def test_concurrent_first_fetches_coalesce(tmp_path: Path):
    cache = PosterCache(tmp_path)
    barrier = asyncio.Event()
    started = 0

    async def fetcher(clip_id: int) -> bytes:
        nonlocal started
        started += 1
        await barrier.wait()
        return b"\xff\xd8FROMUPSTREAM"

    async def call() -> bytes:
        path = await cache.get_or_fetch(99, fetcher)
        return path.read_bytes()

    t1 = asyncio.create_task(call())
    t2 = asyncio.create_task(call())
    await asyncio.sleep(0)  # let both reach the lock
    barrier.set()
    a, b = await asyncio.gather(t1, t2)

    assert a == b == b"\xff\xd8FROMUPSTREAM"
    assert started == 1, "second waiter should have read from disk, not re-fetched"


@pytest.mark.asyncio
async def test_atomic_write_does_not_leave_partial_file(tmp_path: Path, monkeypatch):
    cache = PosterCache(tmp_path)

    async def fetcher(clip_id: int) -> bytes:
        raise RuntimeError("upstream blew up")

    with pytest.raises(RuntimeError):
        await cache.get_or_fetch(13, fetcher)

    # No 13.jpg should exist, and no leftover 13.jpg.tmp either.
    assert not (tmp_path / "13.jpg").exists()
    assert not (tmp_path / "13.jpg.tmp").exists()


@pytest.mark.asyncio
async def test_creates_cache_dir_if_missing(tmp_path: Path):
    cache_dir = tmp_path / "deep" / "posters"
    cache = PosterCache(cache_dir)

    async def fetcher(clip_id: int) -> bytes:
        return b"x"

    path = await cache.get_or_fetch(1, fetcher)
    assert path.exists()
    assert cache_dir.is_dir()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
.venv/bin/python -m pytest tests/unit/test_poster_cache.py -v
```

Expected: import error — `ModuleNotFoundError: backend.app.services.poster_cache`.

- [ ] **Step 3: Implement**

Create `backend/app/services/poster_cache.py`:

```python
"""On-disk cache for CatDV poster JPEGs.

One JPEG per clip, keyed by `clip_id`, written atomically. A per-clip
asyncio.Lock coalesces concurrent first-fetches so the upstream is hit
exactly once; subsequent waiters fall through to the disk-hit branch.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable
from pathlib import Path


class PosterCache:
    def __init__(self, cache_dir: Path) -> None:
        self._dir = Path(cache_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._locks: dict[int, asyncio.Lock] = {}
        self._locks_guard = asyncio.Lock()

    def _path_for(self, clip_id: int) -> Path:
        return self._dir / f"{clip_id}.jpg"

    async def _lock_for(self, clip_id: int) -> asyncio.Lock:
        async with self._locks_guard:
            lock = self._locks.get(clip_id)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[clip_id] = lock
            return lock

    async def get_or_fetch(
        self,
        clip_id: int,
        fetcher: Callable[[int], Awaitable[bytes]],
    ) -> Path:
        path = self._path_for(clip_id)
        if path.exists():
            return path

        lock = await self._lock_for(clip_id)
        async with lock:
            # Double-check: another coroutine may have written the file
            # while we were waiting.
            if path.exists():
                return path

            data = await fetcher(clip_id)
            tmp = path.with_suffix(path.suffix + ".tmp")
            try:
                tmp.write_bytes(data)
                os.replace(tmp, path)
            except Exception:
                if tmp.exists():
                    tmp.unlink()
                raise
            return path
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
.venv/bin/python -m pytest tests/unit/test_poster_cache.py -v
```

Expected: all 5 tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/poster_cache.py tests/unit/test_poster_cache.py
git commit -m "feat(poster-cache): disk-cached poster store with per-clip lock"
```

---

## Task 4: `/api/poster/{clip_id}` route

**Files:**
- Create: `backend/app/routes/posters.py`
- Modify: `backend/app/main.py` (mount router; add `PosterCache` to context)
- Modify: `backend/app/context.py` (add `poster_cache` attribute — read the file first to match the existing pattern)
- Test: `tests/integration/test_posters_route.py`

- [ ] **Step 1: Read `backend/app/context.py` to understand the AppContext shape**

```bash
.venv/bin/python -c "from backend.app.context import AppContext; import inspect; print(inspect.getsource(AppContext))" | head -80
```

Expected: prints the `AppContext` class. Note where existing services like `catdv` and `proxy_resolver` are attached so the new `poster_cache` field follows the same pattern.

- [ ] **Step 2: Write the failing route tests**

Create `tests/integration/test_posters_route.py`:

```python
import importlib

from fastapi.testclient import TestClient


def _make_client(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("CATDV_BASE_URL", "http://localhost:0")
    monkeypatch.setenv("CATDV_USERNAME", "")
    monkeypatch.setenv("CATDV_PASSWORD", "p")
    monkeypatch.setenv("CATDV_CATALOG_ID", "881507")
    monkeypatch.setenv("GCP_PROJECT_ID", "p")
    monkeypatch.setenv("GCS_BUCKET_NAME", "b")
    monkeypatch.setenv("PROXY_SOURCE", "rest")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from backend.app import main as main_mod
    importlib.reload(main_mod)
    return TestClient(main_mod.app)


class _FakeCatdvClient:
    def __init__(self) -> None:
        self.calls: list[int] = []

    async def download_poster(self, clip_id: int) -> bytes:
        self.calls.append(clip_id)
        return b"\xff\xd8\xff\xe0JPEG-FROM-FAKE"


def test_poster_route_serves_jpeg_and_caches(monkeypatch, tmp_path):
    with _make_client(monkeypatch, tmp_path) as client:
        fake = _FakeCatdvClient()
        client.app.state.ctx.catdv = fake

        r1 = client.get("/api/poster/42")
        assert r1.status_code == 200
        assert r1.headers["content-type"] == "image/jpeg"
        assert "immutable" in r1.headers.get("cache-control", "")
        assert r1.content.startswith(b"\xff\xd8")

        # Second call: must hit disk cache, not call the client again.
        r2 = client.get("/api/poster/42")
        assert r2.status_code == 200
        assert r2.content == r1.content
        assert fake.calls == [42], "second request must not re-fetch upstream"


def test_poster_route_ignores_v_query_string(monkeypatch, tmp_path):
    with _make_client(monkeypatch, tmp_path) as client:
        fake = _FakeCatdvClient()
        client.app.state.ctx.catdv = fake

        r1 = client.get("/api/poster/7?v=111")
        r2 = client.get("/api/poster/7?v=222")  # different version, same clip
        assert r1.status_code == r2.status_code == 200
        # `v` is purely client-side cache busting; server still caches by clip_id.
        assert fake.calls == [7]


def test_poster_route_returns_404_when_upstream_404s(monkeypatch, tmp_path):
    class NotFoundClient:
        async def download_poster(self, clip_id: int) -> bytes:
            import httpx
            raise httpx.HTTPStatusError(
                "404", request=httpx.Request("GET", "/"),
                response=httpx.Response(404),
            )

    with _make_client(monkeypatch, tmp_path) as client:
        client.app.state.ctx.catdv = NotFoundClient()
        r = client.get("/api/poster/999999")
        assert r.status_code == 404
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
.venv/bin/python -m pytest tests/integration/test_posters_route.py -v
```

Expected: 404s from FastAPI (the `/api/poster/{clip_id}` route does not exist yet).

- [ ] **Step 4: Implement the route**

Create `backend/app/routes/posters.py`:

```python
import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

router = APIRouter(prefix="/api/poster", tags=["posters"])

_IMMUTABLE = "public, max-age=31536000, immutable"


@router.get("/{clip_id}")
async def get_poster(request: Request, clip_id: int):
    ctx = request.app.state.ctx
    if ctx.catdv is None or ctx.poster_cache is None:
        raise HTTPException(503, "poster service not initialized")

    try:
        path = await ctx.poster_cache.get_or_fetch(
            clip_id, ctx.catdv.download_poster
        )
    except httpx.HTTPStatusError as exc:
        if exc.response is not None and exc.response.status_code == 404:
            raise HTTPException(404, "poster not available") from exc
        raise HTTPException(502, f"upstream poster fetch failed: {exc}") from exc
    except Exception as exc:
        raise HTTPException(502, f"poster fetch failed: {exc}") from exc

    return FileResponse(
        path,
        media_type="image/jpeg",
        headers={"Cache-Control": _IMMUTABLE},
    )
```

- [ ] **Step 5: Wire `poster_cache` into `AppContext`**

Modify `backend/app/context.py`. Read the file first to find where existing service attributes (`catdv`, `proxy_resolver`, …) are constructed in `AppContext.build()`. Add:

```python
from backend.app.services.poster_cache import PosterCache
...
# inside AppContext.build, after `data_dir` is known:
poster_cache_dir = Path(settings.data_dir) / "cache" / "posters"
ctx.poster_cache = PosterCache(poster_cache_dir)
```

And on the dataclass (or `__init__`):

```python
poster_cache: PosterCache | None = None
```

Match whatever pattern the file already uses (dataclass vs plain class) — do not change the surrounding style.

- [ ] **Step 6: Mount the router in `main.py`**

Add after the existing `media_router` mount (`backend/app/main.py:83-85`):

```python
from backend.app.routes.posters import router as posters_router

app.include_router(posters_router)
```

- [ ] **Step 7: Run the route tests**

```bash
.venv/bin/python -m pytest tests/integration/test_posters_route.py -v
```

Expected: all 3 pass.

- [ ] **Step 8: Run the full suite as a regression check**

```bash
.venv/bin/python -m pytest tests -q
```

Expected: all green.

- [ ] **Step 9: Commit**

```bash
git add backend/app/routes/posters.py backend/app/context.py backend/app/main.py tests/integration/test_posters_route.py
git commit -m "feat(posters): /api/poster/{clip_id} with disk cache and immutable headers"
```

---

## Task 5: Default page size 50 → 20

**Files:**
- Modify: `backend/app/routes/pages.py:62`
- Test: `tests/integration/test_routes_pages.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/integration/test_routes_pages.py`:

```python
def test_clips_list_default_limit_is_20(monkeypatch, tmp_path):
    with _make_client(monkeypatch, tmp_path) as client:
        fake = FakeArchive((_canonical(),))
        client.app.state.ctx.archive = fake
        r = client.get("/")
        assert r.status_code == 200
        assert fake.last_query.limit == 20
```

- [ ] **Step 2: Run test to verify it fails**

```bash
.venv/bin/python -m pytest tests/integration/test_routes_pages.py::test_clips_list_default_limit_is_20 -v
```

Expected: assertion failure — `assert 50 == 20`.

- [ ] **Step 3: Change the default**

Edit `backend/app/routes/pages.py:62`:

```python
    limit: int = 20,
```

- [ ] **Step 4: Run test to verify it passes**

```bash
.venv/bin/python -m pytest tests/integration/test_routes_pages.py -v
```

Expected: all green, including the existing tests (the `limit=10` override test still works because it sets an explicit `?limit=10`).

- [ ] **Step 5: Commit**

```bash
git add backend/app/routes/pages.py tests/integration/test_routes_pages.py
git commit -m "feat(clips-list): default page size 20"
```

---

## Task 6: Film-strip SVG fallback + CSS

**Files:**
- Create: `backend/app/static/film-strip.svg`
- Modify: `backend/app/static/app.css` (around line 307–334)

- [ ] **Step 1: Create the SVG**

Create `backend/app/static/film-strip.svg`:

```xml
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 36" fill="currentColor">
  <rect x="0" y="6" width="64" height="24" rx="2" fill="none"
        stroke="currentColor" stroke-width="2"/>
  <g>
    <rect x="4"  y="10" width="4" height="4"/>
    <rect x="12" y="10" width="4" height="4"/>
    <rect x="20" y="10" width="4" height="4"/>
    <rect x="28" y="10" width="4" height="4"/>
    <rect x="36" y="10" width="4" height="4"/>
    <rect x="44" y="10" width="4" height="4"/>
    <rect x="52" y="10" width="4" height="4"/>
    <rect x="4"  y="22" width="4" height="4"/>
    <rect x="12" y="22" width="4" height="4"/>
    <rect x="20" y="22" width="4" height="4"/>
    <rect x="28" y="22" width="4" height="4"/>
    <rect x="36" y="22" width="4" height="4"/>
    <rect x="44" y="22" width="4" height="4"/>
    <rect x="52" y="22" width="4" height="4"/>
  </g>
</svg>
```

- [ ] **Step 2: Replace old `.tbl` rules with new `.clip-list` rules in `app.css`**

In `backend/app/static/app.css`, locate lines 307–334 (the `.clips-region` / `.tbl` / `.tbl .thumb` / `.tbl .empty` block) and replace **only those lines** with:

```css
.clips-region { display: flex; flex-direction: column; min-height: 0; flex: 1; }
.tbl-scroll { overflow: auto; flex: 1; min-height: 0; }

.clip-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; }

.clip-row {
  display: grid;
  grid-template-columns: 32px 160px 1fr;
  gap: 12px;
  padding: 12px 16px;
  border-bottom: 1px solid var(--line);
  align-items: start;
}
.clip-row:hover { background: var(--hover); }
.clip-row--empty {
  display: block; text-align: center;
  color: var(--text-3); padding: 32px 0;
}

.clip-row__rail {
  display: flex; flex-direction: column; gap: 6px; align-items: center;
}

.clip-row__poster {
  display: block;
  width: 160px;
  aspect-ratio: 16 / 9;
  background: var(--panel-2, #1c1c1c);
  border-radius: 4px;
  overflow: hidden;
  text-decoration: none;
}
.clip-row__poster img {
  width: 100%; height: 100%;
  object-fit: cover; display: block;
}
.poster-fallback {
  display: block; width: 100%; height: 100%;
  background: var(--panel-2, #1c1c1c) url("/static/film-strip.svg") center/45% no-repeat;
  color: var(--text-4, #555);
  opacity: 0.6;
}

.clip-row__body { min-width: 0; }
.clip-row__title {
  margin: 0 0 4px;
  font-size: 13px; font-weight: 600;
  display: flex; flex-wrap: wrap; gap: 8px; align-items: baseline;
}
.clip-row__title a { color: var(--text); text-decoration: none; }
.clip-row__title a:hover { text-decoration: underline; }
.clip-row__title .meta {
  color: var(--text-3); font-weight: 400; font-size: 11.5px;
}

.clip-row__notes {
  margin: 0;
  color: var(--text-2, var(--text-3));
  font-size: 12.5px;
  line-height: 1.45;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.clip-row__notes.is-clamped {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.clip-row__more {
  margin-top: 2px;
  padding: 0;
  background: none; border: none;
  color: var(--accent, #6aaaff);
  font-size: 11.5px; cursor: pointer;
}
.clip-row__more:hover { text-decoration: underline; }
```

Leave the `.pager` section and everything else untouched.

- [ ] **Step 3: Verify CSS file is valid (no syntax error)**

```bash
.venv/bin/python -c "open('backend/app/static/app.css').read()"
```

Expected: no exception. (No project-level CSS linter is wired up; the smoke check is the manual page view in Task 8.)

- [ ] **Step 4: Commit**

```bash
git add backend/app/static/film-strip.svg backend/app/static/app.css
git commit -m "style(clips): media-row layout CSS and film-strip fallback"
```

---

## Task 7: Restructure templates

**Files:**
- Modify: `backend/app/templates/pages/_clips_tbody.html`
- Modify: `backend/app/templates/pages/clips.html`
- Test: `tests/integration/test_routes_pages.py`

- [ ] **Step 1: Write failing template-rendering tests**

Append to `tests/integration/test_routes_pages.py`:

```python
def _canonical_with(
    *, clip_id=12041, name="x",
    poster_id: int | None = None,
    notes: str | None = None,
    big_notes: str | None = None,
) -> CanonicalClip:
    pd: dict = {"ID": clip_id, "name": name}
    if poster_id is not None:
        pd["posterID"] = poster_id
    if notes is not None:
        pd["notes"] = notes
    if big_notes is not None:
        pd["bigNotes"] = big_notes
    base = _canonical(clip_id=clip_id, name=name)
    return dataclasses.replace(base, provider_data=pd)


def test_clips_list_renders_poster_img_when_poster_id_present(monkeypatch, tmp_path):
    with _make_client(monkeypatch, tmp_path) as client:
        clip = _canonical_with(clip_id=12041, name="C1", poster_id=882119)
        client.app.state.ctx.archive = FakeArchive((clip,))
        r = client.get("/")
        assert r.status_code == 200
        assert '/api/poster/12041?v=882119' in r.text
        assert 'loading="lazy"' in r.text


def test_clips_list_uses_fallback_when_no_poster_id(monkeypatch, tmp_path):
    with _make_client(monkeypatch, tmp_path) as client:
        clip = _canonical_with(clip_id=42, name="C2")
        client.app.state.ctx.archive = FakeArchive((clip,))
        r = client.get("/")
        assert r.status_code == 200
        assert "/api/poster/42" not in r.text
        assert "poster-fallback" in r.text


def test_clips_list_shows_notes_excerpt(monkeypatch, tmp_path):
    with _make_client(monkeypatch, tmp_path) as client:
        clip = _canonical_with(
            clip_id=7, name="C3", notes="LOV, STŘÍLENÍ, JELENI",
        )
        client.app.state.ctx.archive = FakeArchive((clip,))
        r = client.get("/")
        assert r.status_code == 200
        assert "LOV, STŘÍLENÍ, JELENI" in r.text
        assert "clip-row__notes" in r.text


def test_clips_list_renders_more_button_for_long_notes(monkeypatch, tmp_path):
    with _make_client(monkeypatch, tmp_path) as client:
        clip = _canonical_with(
            clip_id=8, name="C4",
            notes="line a\nline b\nline c with detail",
        )
        client.app.state.ctx.archive = FakeArchive((clip,))
        r = client.get("/")
        assert r.status_code == 200
        assert "clip-row__more" in r.text


def test_clips_list_no_more_button_for_short_notes(monkeypatch, tmp_path):
    with _make_client(monkeypatch, tmp_path) as client:
        clip = _canonical_with(clip_id=9, name="C5", notes="krátká")
        client.app.state.ctx.archive = FakeArchive((clip,))
        r = client.get("/")
        assert r.status_code == 200
        assert "clip-row__more" not in r.text
```

- [ ] **Step 2: Run the new tests to confirm they fail**

```bash
.venv/bin/python -m pytest tests/integration/test_routes_pages.py -v -k "poster or notes_excerpt or more_button or fallback"
```

Expected: all 5 fail (template still uses the old `<table>` markup).

- [ ] **Step 3: Replace `_clips_tbody.html`**

Replace the **entire** content of `backend/app/templates/pages/_clips_tbody.html` with:

```jinja
<div id="clips-region" class="clips-region">
  <ul class="clip-list">
    {% for c in clips %}
      <li class="clip-row"
          :class="{ 'is-expanded': expanded[{{ c.id }}] }">
        <div class="clip-row__rail" onclick="event.stopPropagation()">
          <input type="checkbox"
                 class="row-check"
                 name="clip_keys"
                 value="catdv/{{ c.id }}"
                 aria-label="Select clip {{ c.id }}">
          {% with cache = c.cache %}
            {% include "pages/_cache_badge.html" %}
          {% endwith %}
        </div>

        <a class="clip-row__poster" href="/clips/{{ c.id }}" aria-hidden="true" tabindex="-1">
          {% if c.poster_id %}
            <img loading="lazy" decoding="async"
                 src="/api/poster/{{ c.id }}?v={{ c.poster_id }}"
                 alt="">
          {% else %}
            <span class="poster-fallback"></span>
          {% endif %}
        </a>

        <div class="clip-row__body">
          <h3 class="clip-row__title">
            <a href="/clips/{{ c.id }}">{{ c.name }}</a>
            <span class="meta mono">
              {{ c.year or "—" }} ·
              {{ "%d:%02d"|format((c.duration_secs|int)//60, (c.duration_secs|int)%60) }} ·
              {{ c.marker_count }} mk
            </span>
          </h3>
          {% if c.notes_excerpt %}
            <p class="clip-row__notes"
               :class="{ 'is-clamped': !expanded[{{ c.id }}] }">{{ c.notes_excerpt }}</p>
            {% if c.notes_has_more %}
              <button type="button" class="clip-row__more"
                      @click="expanded[{{ c.id }}] = !expanded[{{ c.id }}]"
                      x-text="expanded[{{ c.id }}] ? 'Less' : 'More'">More</button>
            {% endif %}
          {% endif %}
        </div>
      </li>
    {% else %}
      <li class="clip-row clip-row--empty">No clips match.</li>
    {% endfor %}
  </ul>

  {% set _pq = 'q=' ~ (q|urlencode) ~ '&limit=' ~ limit %}
  {% if cache_filter and cache_filter != 'any' %}{% set _pq = _pq ~ '&cache=' ~ cache_filter %}{% endif %}
  {% if anno_filter and anno_filter != 'any' %}{% set _pq = _pq ~ '&anno=' ~ anno_filter %}{% endif %}
  <nav class="pager">
    {% if prev_offset is not none %}
      <a class="pg-btn" href="/?{{ _pq }}&offset={{ prev_offset }}">‹ Prev</a>
    {% else %}
      <span class="pg-btn disabled">‹ Prev</span>
    {% endif %}
    <span class="pg-meta mono">
      {% if clips %}{{ offset + 1 }}–{{ offset + clips|length }} of {{ total }}{% else %}0 of {{ total }}{% endif %}
    </span>
    {% if next_offset is not none %}
      <a class="pg-btn" href="/?{{ _pq }}&offset={{ next_offset }}">Next ›</a>
    {% else %}
      <span class="pg-btn disabled">Next ›</span>
    {% endif %}
  </nav>
</div>
```

- [ ] **Step 4: Wrap the include in `clips.html` with the Alpine expanded scope**

In `backend/app/templates/pages/clips.html`, locate line 94: `{% include "pages/_clips_tbody.html" %}`. Replace with:

```jinja
<div x-data="{ expanded: {} }">
  {% include "pages/_clips_tbody.html" %}
</div>
```

Keep the surrounding `<form>` and `<script>` unchanged. The outer `x-data="bulkSel()"` on `.page-clips` (line 7) stays — Alpine supports nested scopes.

- [ ] **Step 5: Run the template tests**

```bash
.venv/bin/python -m pytest tests/integration/test_routes_pages.py -v
```

Expected: all green, including the pre-existing `Abramcukova_Anna_09` / `1932` / `30.léta` assertions (they still substring-match in the new markup).

- [ ] **Step 6: Run the full suite**

```bash
.venv/bin/python -m pytest tests -q
```

Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add backend/app/templates/pages/_clips_tbody.html backend/app/templates/pages/clips.html tests/integration/test_routes_pages.py
git commit -m "feat(clips-ui): media-row layout with poster, notes excerpt, expand"
```

---

## Task 8: Manual verification against live CatDV

**Files:** none (verification only).

> The unit and integration suites do not exercise CSS rendering or browser-level lazy-loading. This task is the verification gate before declaring the redesign done.

- [ ] **Step 1: Verify CatDV is reachable (VPN up, server up)**

```bash
/sbin/ping -c1 192.168.1.41 || echo "VPN/server down — bring up WireGuard or ask Honza"
/usr/bin/curl -sS http://192.168.1.41:8080/catdv/api/info | /usr/bin/head -c 200
```

Expected: ping succeeds and `info` returns JSON with `serverVersion`.

- [ ] **Step 2: Verify no dev server is already running (license seat)**

```bash
/usr/sbin/lsof -nP -iTCP:8765 -sTCP:LISTEN
/bin/ps -ef | /usr/bin/grep -E '(uvicorn|backend\.app)' | /usr/bin/grep -v grep
```

If anything is listening on 8765 or already connected to the CatDV server, **reuse it** — do not spawn a second instance. If you must restart, send `SIGTERM` (not `SIGKILL`) to free the CatDV seat cleanly.

- [ ] **Step 3: Start the dev server in the background**

```bash
./run.sh
```

(Or whatever the project's standard dev-launch is — `run.sh` is the canonical entry point.)

- [ ] **Step 4: Open the clips list and verify visually**

Navigate to `http://localhost:8765/` in a browser. Verify:

- Default page shows 20 clips (look at pager: "1–20 of N").
- Each clip with a poster shows a 160×90 image; first viewport posters load over VPN, subsequent scroll loads continue (lazy load is working).
- Each clip with notes shows up to 2 lines; rows with long/multiline notes show a `More` button that toggles to `Less` and reveals the full text inline.
- Clips without a `posterID` show the gray film-strip glyph placeholder, with **no** `/api/poster/...` request in the network panel for that row.
- Filter `Cache = local`, search box, pager `Next ›` all still work (HTMX swap preserves the layout).
- Bulk-select checkbox still ticks; "Actions" button enables when ≥1 row is selected.
- Clicking the clip title navigates to `/clips/<id>`.

- [ ] **Step 5: Verify disk cache populated**

```bash
ls data/cache/posters/ | /usr/bin/head -5
```

Expected: a handful of `<clip_id>.jpg` files (the posters from the first page you scrolled through).

- [ ] **Step 6: Verify browser cache hit on reload**

Hard-reload the page (`Cmd+Shift+R` to bypass browser cache). Posters should re-download (visible in Network panel). Soft-reload (`Cmd+R`) — posters should serve from browser cache (Network panel shows them as "(disk cache)" / 200 from cache, no request to our server).

- [ ] **Step 7: Shut down the dev server gracefully**

```bash
/bin/kill -TERM $(/usr/sbin/lsof -t -iTCP:8765 -sTCP:LISTEN)
```

Confirm the server log shows `Application shutdown complete.` before the process exits. If only `Finished server process` shows up, the CatDV seat may still be held — see `CLAUDE.md` § "CatDV session discipline".

- [ ] **Step 8: Append a decisions entry**

Per `CLAUDE.md`, non-trivial design calls go in `docs/decisions.md`. Append a dated entry summarising:

- **Context:** Clips list was a dense text-only table; users had to open each clip to see a poster or notes.
- **Alternatives:** card grid (lower density); thin row with hover popover (no inline notes).
- **Choice:** wide media row with poster + 2-line notes excerpt and inline expand; new `/api/poster` route with disk + browser cache; page size 20.
- **Why:** `posterID`/`notes`/`bigNotes` already arrive in the list cache (zero extra CatDV calls); poster blobs are small enough for an unbounded local disk cache; `thumbnailIDs` deliberately deferred because they would require a per-clip detail fetch over the slow VPN.

```bash
git add docs/decisions.md
git commit -m "docs(decisions): clips list redesign — poster + notes per row"
```

---

## Self-Review

Spec → plan coverage check (done while writing this plan):

| Spec section | Covered by |
|---|---|
| View-model `poster_id`, `notes_excerpt`, `notes_has_more` | Task 1 |
| Default page size 20 | Task 5 |
| New `/api/poster/{clip_id}` route, disk cache, immutable headers | Tasks 3 + 4 |
| `CatdvClient.download_poster` with 401 retry | Task 2 |
| Per-`clip_id` asyncio lock to coalesce concurrent fetches | Task 3 |
| `_clips_tbody.html` restructure to `<ul>`/`<li class="clip-row">` | Task 7 |
| `clips.html` Alpine `x-data="{ expanded: {} }"` scope | Task 7 |
| CSS rules for media-row + line-clamp + fallback | Task 6 |
| Posterless fallback (no `<img>`, no upstream call) | Task 7 (template) + Task 4 (route still works in isolation) |
| Filter form / pager / bulk-select unchanged | Task 7 keeps them intact; Task 5 keeps the URL contract |
| Browser cache busting via `?v={poster_id}` | Task 4 (`v` ignored server-side) + Task 7 (`<img src>`) |
| Manual end-to-end verification | Task 8 |
| Risks: list-response variants without `posterID` | Handled by `poster_id is None` branch in template (Task 7) |

No placeholders, no TBDs, no "implement later" steps. All code blocks are complete. Type/method names are consistent across tasks: `download_poster`, `PosterCache.get_or_fetch`, `poster_cache`, `poster_id`, `notes_excerpt`, `notes_has_more`.
