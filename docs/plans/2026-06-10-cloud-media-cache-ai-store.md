# Cloud media cache: AI-store-only — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** On GCP, route all proxy-media caching writes and playback reads through the AI store (GCS), so cached/uploaded clips survive Cloud Run's ephemeral disk; dev keeps local-disk behavior.

**Architecture:** Introduce a `MediaCacheBackend` protocol with two implementations — `LocalProxyBackend` (wraps today's `ProxyResolver` + `MediaLocator`) and `AiStoreBackend` (download-through-tunnel → `ensure_uploaded` → delete temp; playback via signed URL). A new `MEDIA_CACHE` setting selects one. The prefetcher, the media route, and the studio-upload ingest all depend on the backend instead of the raw resolver.

**Tech Stack:** Python 3.13, FastAPI, aiosqlite, Google Cloud Storage, pytest (TDD).

**Spec:** `docs/specs/2026-06-10-cloud-media-cache-ai-store-design.md`

---

## File structure

- **Create** `backend/app/services/media_cache.py` — the `MediaCacheBackend` protocol, `LocalProxyBackend`, `AiStoreBackend`, and `build_media_cache_backend()` factory. One responsibility: "where does a clip's proxy media live, and how do I populate it."
- **Modify** `backend/app/settings.py` — add `media_cache`, remove `playback_source`.
- **Modify** `backend/app/context.py` — construct the backend in the live build; expose `media_cache_backend` on `LiveCtx`; point `MediaPrefetcher` at it; drop the `media_locator` property's `playback_source` read.
- **Modify** `backend/app/services/media_prefetcher.py` — depend on `backend.ensure_cached` instead of `resolver.path_for_clip_id`.
- **Modify** `backend/app/routes/media.py` — `stream_media` resolves via `backend.locate`.
- **Modify** `backend/app/routes/studio.py` — upload ingest uploads to GCS in `ai_store` mode.
- **Modify** `deploy/cloudrun.env.yaml` — `PLAYBACK_SOURCE` → `MEDIA_CACHE: "ai_store"`.
- **Create** `docs/adr/0069-cloud-media-cache-ai-store.md` — record the deviation + rename.
- **Reused as-is:** `media_locator.py` (`LocalFile`, `RemoteUrl`, `MediaNotAvailable`, `MediaLocator`, `SIGNED_URL_TTL_S`), `archive/ai_store.py`, `archive/ai_stores/gcs/adapter.py`, `services/gcs.py`, `uploaded_ids.is_uploaded`.

Run tests with `.venv/bin/python -m pytest` (per global rules). The full suite needs `--ignore` of the 3 `PIL`/`radon` modules absent from this venv; per-file runs below are unaffected.

---

## Task 1: `MEDIA_CACHE` setting (replaces `PLAYBACK_SOURCE`)

**Files:**
- Modify: `backend/app/settings.py:25-47`
- Test: `tests/unit/test_settings_media_cache.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_settings_media_cache.py
from backend.app.settings import Settings


def _minimal(**over):
    base = dict(
        catdv_base_url="http://x",
        catdv_catalog_id=1,
        gcp_project_id="p",
        gcs_bucket_name="b",
    )
    base.update(over)
    return Settings(**base)


def test_media_cache_defaults_to_local():
    assert _minimal().media_cache == "local"


def test_media_cache_accepts_ai_store():
    assert _minimal(media_cache="ai_store").media_cache == "ai_store"


def test_playback_source_is_removed():
    assert not hasattr(_minimal(), "playback_source")
```

- [ ] **Step 2: Run it, expect fail**

Run: `.venv/bin/python -m pytest tests/unit/test_settings_media_cache.py -v`
Expected: FAIL (`media_cache` missing / `playback_source` still present).

- [ ] **Step 3: Edit `settings.py`**

Remove the `playback_source` field + its comment (lines ~43-47). Add, in the same media region:

```python
    # Proxy-media cache + playback backend. "local" (dev): download to
    # the local proxy cache and serve from disk, GCS as read fallback.
    # "ai_store" (cloud, ephemeral disk): cache writes upload to GCS and
    # playback redirects to signed URLs; the local proxy cache is unused.
    # See docs/specs/2026-06-10-cloud-media-cache-ai-store-design.md.
    media_cache: Literal["local", "ai_store"] = "local"
```

- [ ] **Step 4: Find and fix `playback_source` references**

Run: `grep -rn "playback_source" backend/ tests/`
For each non-test hit, replace per Task 4 (the `context.py:348` read is handled there). For test hits asserting `playback_source`, update to `media_cache`. Expected after fixing: only `media_cache` remains.

- [ ] **Step 5: Run it, expect pass**

Run: `.venv/bin/python -m pytest tests/unit/test_settings_media_cache.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/settings.py tests/unit/test_settings_media_cache.py
git commit -m "feat: add MEDIA_CACHE setting, remove PLAYBACK_SOURCE"
```

---

## Task 2: `MediaCacheBackend` protocol + `LocalProxyBackend`

**Files:**
- Create: `backend/app/services/media_cache.py`
- Test: `tests/unit/test_local_proxy_backend.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_local_proxy_backend.py
import pytest
from pathlib import Path
from backend.app.services.media_cache import LocalProxyBackend
from backend.app.services.media_locator import LocalFile, RemoteUrl


class _Resolver:
    def __init__(self, path=None, raise_exc=None):
        self._path, self._raise, self.calls = path, raise_exc, []

    async def path_for_clip_id(self, clip_id):
        self.calls.append(clip_id)
        if self._raise:
            raise self._raise
        return self._path


class _AiStore:
    def __init__(self, ref=None):
        self._ref = ref

    async def status(self, key):
        return self._ref


class _Gcs:
    def signed_url(self, handle, *, expires_s):
        return f"https://signed/{handle}"


@pytest.mark.asyncio
async def test_ensure_cached_downloads_via_resolver(tmp_path):
    r = _Resolver(path=tmp_path / "1.mov")
    b = LocalProxyBackend(resolver=r, ai_store=_AiStore(), gcs=_Gcs())
    await b.ensure_cached(1)
    assert r.calls == [1]


@pytest.mark.asyncio
async def test_locate_prefers_local_file(tmp_path):
    p = tmp_path / "1.mov"
    p.write_bytes(b"x")
    b = LocalProxyBackend(resolver=_Resolver(path=p), ai_store=_AiStore(), gcs=_Gcs())
    located = await b.locate(1)
    assert located == LocalFile(p)


@pytest.mark.asyncio
async def test_locate_falls_back_to_gcs(tmp_path):
    class _Ref:
        handle = "gs://bucket/clips/1.mov"
    b = LocalProxyBackend(
        resolver=_Resolver(raise_exc=RuntimeError("not on disk")),
        ai_store=_AiStore(ref=_Ref()),
        gcs=_Gcs(),
    )
    located = await b.locate(1)
    assert isinstance(located, RemoteUrl)
    assert located.url == "https://signed/gs://bucket/clips/1.mov"
```

- [ ] **Step 2: Run it, expect fail**

Run: `.venv/bin/python -m pytest tests/unit/test_local_proxy_backend.py -v`
Expected: FAIL (`media_cache` module missing).

- [ ] **Step 3: Create `media_cache.py` with the protocol + `LocalProxyBackend`**

```python
# backend/app/services/media_cache.py
"""MediaCacheBackend -- the single authority for proxy-media caching and
playback location. Two backends, selected by settings.media_cache:

- LocalProxyBackend (dev): download to the local proxy cache, serve from
  disk, GCS signed URL as read fallback (today's behavior).
- AiStoreBackend (cloud): cache writes upload to the AI store (GCS) and
  the local staging file is deleted; playback is a signed URL. The local
  proxy cache is never used for reads.

ensure_cached() may need CatDV (the tunnel) on a miss; locate() never
does -- it depends only on the store index + URL signing, so cached
clips stay playable when CatDV is offline.
"""

from __future__ import annotations

import asyncio
import logging
import mimetypes
from pathlib import Path
from typing import Protocol

from backend.app.archive.model import ClipKey
from backend.app.services.media_locator import (
    LocalFile,
    MediaLocator,
    MediaNotAvailable,
    RemoteUrl,
    SIGNED_URL_TTL_S,
)
from backend.app.uploaded_ids import is_uploaded

log = logging.getLogger(__name__)

_DEFAULT_MIME = "video/quicktime"


def _clip_key(clip_id: int) -> ClipKey:
    return ("uploaded" if is_uploaded(clip_id) else "catdv", str(clip_id))


class MediaCacheBackend(Protocol):
    async def ensure_cached(self, clip_id: int) -> None: ...

    async def locate(self, clip_id: int) -> LocalFile | RemoteUrl | None: ...


class LocalProxyBackend:
    """Dev backend: local proxy cache first, GCS signed URL as fallback."""

    def __init__(self, *, resolver, ai_store, gcs) -> None:
        self._resolver = resolver
        self._locator = MediaLocator(
            proxy_resolver=resolver,
            ai_store=ai_store,
            gcs_service=gcs,
            prefer="local",
        )

    async def ensure_cached(self, clip_id: int) -> None:
        await self._resolver.path_for_clip_id(clip_id)

    async def locate(self, clip_id: int) -> LocalFile | RemoteUrl | None:
        try:
            return await self._locator.locate(clip_id)
        except MediaNotAvailable:
            return None
```

- [ ] **Step 4: Run it, expect pass**

Run: `.venv/bin/python -m pytest tests/unit/test_local_proxy_backend.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/media_cache.py tests/unit/test_local_proxy_backend.py
git commit -m "feat: MediaCacheBackend protocol + LocalProxyBackend"
```

---

## Task 3: `AiStoreBackend`

**Files:**
- Modify: `backend/app/services/media_cache.py`
- Test: `tests/unit/test_ai_store_backend.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_ai_store_backend.py
import pytest
from backend.app.services.media_cache import AiStoreBackend
from backend.app.services.media_locator import RemoteUrl


class _Ref:
    handle = "gs://bucket/clips/5.mov"


class _AiStore:
    def __init__(self, status_ref=None):
        self._status_ref = status_ref
        self.uploaded = []

    async def status(self, key):
        return self._status_ref

    async def ensure_uploaded(self, key, path, mime):
        self.uploaded.append((key, path, mime))
        return _Ref()


class _Resolver:
    def __init__(self, path):
        self._path, self.calls = path, []

    async def path_for_clip_id(self, clip_id):
        self.calls.append(clip_id)
        return self._path


class _Gcs:
    def signed_url(self, handle, *, expires_s):
        return f"https://signed/{handle}"


class _ProxyCacheRepo:
    def __init__(self):
        self.deleted = []

    async def delete(self, db, clip_id):
        self.deleted.append(clip_id)


@pytest.mark.asyncio
async def test_ensure_cached_status_hit_skips_download(tmp_path):
    r = _Resolver(tmp_path / "5.mov")
    b = AiStoreBackend(
        rest_resolver=r, ai_store=_AiStore(status_ref=_Ref()),
        gcs=_Gcs(), proxy_cache_repo=_ProxyCacheRepo(), db_provider=lambda: None,
    )
    await b.ensure_cached(5)
    assert r.calls == []  # dedup fast-path: no tunnel hit


@pytest.mark.asyncio
async def test_ensure_cached_uploads_then_deletes_temp(tmp_path):
    p = tmp_path / "5.mov"
    p.write_bytes(b"video")
    store, repo, r = _AiStore(), _ProxyCacheRepo(), _Resolver(p)
    b = AiStoreBackend(
        rest_resolver=r, ai_store=store, gcs=_Gcs(),
        proxy_cache_repo=repo, db_provider=lambda: None,
    )
    await b.ensure_cached(5)
    assert store.uploaded and store.uploaded[0][0] == ("catdv", "5")
    assert not p.exists()           # temp deleted
    assert repo.deleted == [5]      # proxy_cache row removed


@pytest.mark.asyncio
async def test_ensure_cached_deletes_temp_on_upload_failure(tmp_path):
    p = tmp_path / "5.mov"
    p.write_bytes(b"video")

    class _Boom(_AiStore):
        async def ensure_uploaded(self, key, path, mime):
            raise RuntimeError("gcs down")

    b = AiStoreBackend(
        rest_resolver=_Resolver(p), ai_store=_Boom(), gcs=_Gcs(),
        proxy_cache_repo=_ProxyCacheRepo(), db_provider=lambda: None,
    )
    with pytest.raises(RuntimeError):
        await b.ensure_cached(5)
    assert not p.exists()           # temp still cleaned up


@pytest.mark.asyncio
async def test_locate_returns_signed_url_on_status_hit():
    b = AiStoreBackend(
        rest_resolver=_Resolver(None), ai_store=_AiStore(status_ref=_Ref()),
        gcs=_Gcs(), proxy_cache_repo=_ProxyCacheRepo(), db_provider=lambda: None,
    )
    located = await b.locate(5)
    assert located == RemoteUrl("https://signed/gs://bucket/clips/5.mov")


@pytest.mark.asyncio
async def test_locate_returns_none_on_miss():
    b = AiStoreBackend(
        rest_resolver=_Resolver(None), ai_store=_AiStore(status_ref=None),
        gcs=_Gcs(), proxy_cache_repo=_ProxyCacheRepo(), db_provider=lambda: None,
    )
    assert await b.locate(5) is None
```

- [ ] **Step 2: Run it, expect fail**

Run: `.venv/bin/python -m pytest tests/unit/test_ai_store_backend.py -v`
Expected: FAIL (`AiStoreBackend` undefined).

- [ ] **Step 3: Append `AiStoreBackend` to `media_cache.py`**

```python
class AiStoreBackend:
    """Cloud backend: cache writes upload to the AI store (GCS); the local
    staging file is deleted after upload. Playback is a signed URL. The
    local proxy cache is never consulted for reads."""

    def __init__(
        self, *, rest_resolver, ai_store, gcs, proxy_cache_repo, db_provider
    ) -> None:
        self._resolver = rest_resolver
        self._ai_store = ai_store
        self._gcs = gcs
        self._proxy_cache_repo = proxy_cache_repo
        self._db_provider = db_provider

    async def ensure_cached(self, clip_id: int) -> None:
        key = _clip_key(clip_id)
        if await self._ai_store.status(key) is not None:
            return  # already in GCS -- no tunnel hit (status-first fast-path)

        path: Path = await self._resolver.path_for_clip_id(clip_id)
        try:
            mime = mimetypes.guess_type(str(path))[0] or _DEFAULT_MIME
            await self._ai_store.ensure_uploaded(key, path, mime)
        finally:
            # Keep peak ephemeral-disk usage to a single proxy: drop the
            # staging file + its proxy_cache row even on upload failure.
            await asyncio.to_thread(path.unlink, True)  # missing_ok=True
            await self._proxy_cache_repo.delete(self._db_provider(), clip_id)

    async def locate(self, clip_id: int) -> LocalFile | RemoteUrl | None:
        ref = await self._ai_store.status(_clip_key(clip_id))
        if ref is None or not ref.handle.startswith("gs://"):
            return None
        url = await asyncio.to_thread(
            self._gcs.signed_url, ref.handle, expires_s=SIGNED_URL_TTL_S
        )
        return RemoteUrl(url)
```

- [ ] **Step 4: Run it, expect pass**

Run: `.venv/bin/python -m pytest tests/unit/test_ai_store_backend.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/media_cache.py tests/unit/test_ai_store_backend.py
git commit -m "feat: AiStoreBackend (cache->GCS, playback via signed URL)"
```

---

## Task 4: Factory + wire onto `LiveCtx`

**Files:**
- Modify: `backend/app/services/media_cache.py` (add `build_media_cache_backend`)
- Modify: `backend/app/context.py` (LiveCtx field/property + construction; drop `playback_source` read)
- Test: `tests/unit/test_media_cache_factory.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_media_cache_factory.py
from backend.app.services.media_cache import (
    build_media_cache_backend, LocalProxyBackend, AiStoreBackend,
)


class _R:
    async def path_for_clip_id(self, c): ...
class _S:
    async def status(self, k): ...
class _G:
    def signed_url(self, h, *, expires_s): ...
class _Repo:
    async def delete(self, db, c): ...


def _mk(mode):
    return build_media_cache_backend(
        media_cache=mode, resolver=_R(), ai_store=_S(), gcs=_G(),
        proxy_cache_repo=_Repo(), db_provider=lambda: None,
    )


def test_local_mode_builds_local_backend():
    assert isinstance(_mk("local"), LocalProxyBackend)


def test_ai_store_mode_builds_ai_store_backend():
    assert isinstance(_mk("ai_store"), AiStoreBackend)
```

- [ ] **Step 2: Run it, expect fail**

Run: `.venv/bin/python -m pytest tests/unit/test_media_cache_factory.py -v`
Expected: FAIL (`build_media_cache_backend` undefined).

- [ ] **Step 3: Add the factory to `media_cache.py`**

```python
def build_media_cache_backend(
    *, media_cache, resolver, ai_store, gcs, proxy_cache_repo, db_provider
) -> MediaCacheBackend:
    if media_cache == "ai_store":
        return AiStoreBackend(
            rest_resolver=resolver, ai_store=ai_store, gcs=gcs,
            proxy_cache_repo=proxy_cache_repo, db_provider=db_provider,
        )
    return LocalProxyBackend(resolver=resolver, ai_store=ai_store, gcs=gcs)
```

- [ ] **Step 4: Run it, expect pass**

Run: `.venv/bin/python -m pytest tests/unit/test_media_cache_factory.py -v`
Expected: PASS.

- [ ] **Step 5: Wire onto `LiveCtx` in `context.py`**

Add an import near the other service imports (~line 72):

```python
from backend.app.services.media_cache import MediaCacheBackend, build_media_cache_backend
```

Add a field to the `LiveCtx` dataclass (next to `media_prefetcher`, ~line 223):

```python
    media_cache_backend: MediaCacheBackend | None = None
```

Replace the `media_locator` property (lines ~339-348) — it loses its `playback_source` read; keep `MediaLocator` available for `LocalProxyBackend` but the route no longer calls this property. Delete the property and instead expose:

```python
    @property
    def media_cache_backend_or_none(self) -> "MediaCacheBackend | None":
        return self.media_cache_backend
```

(If other call sites still use `media_locator`, run `grep -rn "media_locator" backend/` and migrate them to `media_cache_backend.locate`; there should be only `routes/media.py`, handled in Task 6.)

- [ ] **Step 6: Construct the backend in the live build**

In `_build_sync_subsystem` (`context.py`, just before `LiveCtx(...)` is assembled, near the `media_prefetcher` block ~line 735), add:

```python
    media_cache_backend = None
    if arch.proxy_resolver is not None:
        media_cache_backend = build_media_cache_backend(
            media_cache=settings.media_cache,
            resolver=arch.proxy_resolver,
            ai_store=arch.ai_store,
            gcs=arch._gcs_service if hasattr(arch, "_gcs_service") else arch.gcs_service,
            proxy_cache_repo=core.proxy_cache_repo,
            db_provider=lambda: core.db,
        )
```

(Use the field name the `_ArchiveSubsystem` actually exposes for the GCS service — check with `grep -n "gcs_service" backend/app/context.py`; the `LiveCtx` field is `_gcs_service`.) Pass `media_cache_backend=media_cache_backend` into the `LiveCtx(...)` constructor call.

- [ ] **Step 7: Run the context tests**

Run: `.venv/bin/python -m pytest tests/unit/test_context_delegation.py tests/unit/test_media_cache_factory.py -v`
Expected: PASS (fix any `playback_source`/`media_locator` reference the run surfaces).

- [ ] **Step 8: Commit**

```bash
git add backend/app/services/media_cache.py backend/app/context.py tests/unit/test_media_cache_factory.py
git commit -m "feat: build media cache backend and expose on LiveCtx"
```

---

## Task 5: Point `MediaPrefetcher` at the backend

**Files:**
- Modify: `backend/app/services/media_prefetcher.py:29-115`
- Modify: `backend/app/context.py` (prefetcher construction ~line 736-742)
- Test: `tests/unit/test_media_prefetcher.py` (existing — extend or adjust)

- [ ] **Step 1: Write/adjust the failing test**

```python
# tests/unit/test_media_prefetcher_backend.py
import pytest
from backend.app.services.media_prefetcher import MediaPrefetcher


class _Queue:
    def __init__(self, row):
        self._row, self.done = row, []

    async def claim_next(self, db):
        r, self._row = self._row, None
        return r

    async def mark_done(self, db, rid, bytes_downloaded):
        self.done.append((rid, bytes_downloaded))

    async def mark_error(self, db, rid, msg):
        self.done.append((rid, "error", msg))


class _Backend:
    def __init__(self):
        self.cached = []

    async def ensure_cached(self, clip_id):
        self.cached.append(clip_id)


@pytest.mark.asyncio
async def test_tick_calls_backend_ensure_cached():
    q = _Queue({"id": 1, "provider_clip_id": "42"})
    backend = _Backend()
    pf = MediaPrefetcher(queue_repo=q, backend=backend, db_provider=lambda: None)
    cid = await pf.tick_once()
    assert cid == 42
    assert backend.cached == [42]
    assert q.done == [(1, 0)]
```

- [ ] **Step 2: Run it, expect fail**

Run: `.venv/bin/python -m pytest tests/unit/test_media_prefetcher_backend.py -v`
Expected: FAIL (`MediaPrefetcher` takes `resolver`, not `backend`).

- [ ] **Step 3: Edit `media_prefetcher.py`**

Change the constructor param `resolver` → `backend` (and the stored `self._resolver` → `self._backend`); update the docstring's "calling `resolver.path_for_clip_id`" to "calling `backend.ensure_cached`". Replace the body of `tick_once` (lines ~108-114):

```python
        try:
            await self._backend.ensure_cached(clip_id_int)
            await self._queue.mark_done(db, rid, bytes_downloaded=0)
        except Exception as exc:  # noqa: BLE001
            log.warning("prefetch failed for clip %s: %s", clip_id_int, exc)
            await self._queue.mark_error(db, rid, str(exc))
        return clip_id_int
```

(The backend now owns sizing/dedup; the queue row records completion, not bytes. If a byte count is wanted later, add `ensure_cached -> int`; YAGNI for now — record 0.)

- [ ] **Step 4: Update construction in `context.py`**

In the `media_prefetcher` block (~line 736), keep the cache-only guard but pass the backend:

```python
    media_prefetcher: MediaPrefetcher | None = None
    _inner_resolver = getattr(arch.proxy_resolver, "inner", arch.proxy_resolver)
    if (
        arch.proxy_resolver is not None
        and media_cache_backend is not None
        and not isinstance(_inner_resolver, LocalCacheOnlyResolver)
    ):
        media_prefetcher = MediaPrefetcher(
            queue_repo=core.prefetch_queue_repo,
            backend=media_cache_backend,
            db_provider=lambda: core.db,
            tick_interval_s=float(settings.prefetch_tick_interval_s),
        )
```

- [ ] **Step 5: Run prefetcher tests, expect pass**

Run: `.venv/bin/python -m pytest tests/unit/test_media_prefetcher_backend.py tests/unit/test_media_prefetcher.py -v`
Expected: PASS (update any existing prefetcher test that passed `resolver=`).

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/media_prefetcher.py backend/app/context.py tests/unit/test_media_prefetcher_backend.py
git commit -m "refactor: prefetcher caches via MediaCacheBackend.ensure_cached"
```

---

## Task 6: `stream_media` resolves via the backend

**Files:**
- Modify: `backend/app/routes/media.py:65-126`
- Test: `tests/integration/test_media_route_backend.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_media_route_backend.py
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from backend.app.routes.media import router
from backend.app.services.media_locator import RemoteUrl, LocalFile
from backend.app import deps


class _Backend:
    def __init__(self, located):
        self._located = located

    async def locate(self, clip_id):
        return self._located


def _client(located, uploaded=False, tmp=None, monkeypatch=None):
    app = FastAPI()
    app.include_router(router)
    live = type("L", (), {"media_cache_backend": _Backend(located)})()
    app.dependency_overrides[deps.get_live_ctx] = lambda: live
    monkeypatch.setattr("backend.app.routes.media.is_uploaded", lambda c: uploaded)
    return TestClient(app)


def test_remote_url_returns_307(monkeypatch):
    c = _client(RemoteUrl("https://storage.googleapis.com/x"), monkeypatch=monkeypatch)
    r = c.get("/api/media/5", follow_redirects=False)
    assert r.status_code == 307
    assert r.headers["location"] == "https://storage.googleapis.com/x"


def test_miss_returns_404(monkeypatch):
    c = _client(None, monkeypatch=monkeypatch)
    r = c.get("/api/media/5")
    assert r.status_code == 404
```

- [ ] **Step 2: Run it, expect fail**

Run: `.venv/bin/python -m pytest tests/integration/test_media_route_backend.py -v`
Expected: FAIL (route still calls `media_locator`/`is_uploaded` local branch).

- [ ] **Step 3: Rewrite `stream_media`**

Replace the body of `stream_media` (`media.py:66-126`) so resolution goes through the backend; keep the local file-serving + range logic for the `LocalFile` case:

```python
@router.get("/{clip_id}")
async def stream_media(request: Request, clip_id: int):
    ctx = get_live_ctx(request)
    backend = ctx.media_cache_backend
    if backend is None:
        raise HTTPException(503, "media cache backend unavailable")
    located = await backend.locate(clip_id)
    if located is None:
        raise HTTPException(404, f"clip {clip_id} not available")
    if isinstance(located, RemoteUrl):
        return RedirectResponse(located.url, status_code=307)
    path = located.path  # LocalFile

    mime = mimetypes.guess_type(str(path))[0] or "video/quicktime"
    size = path.stat().st_size  # sync-io-ok: single metadata call on the stream path
    # ... unchanged range/StreamingResponse/FileResponse block from lines 91-126 ...
```

Keep lines 91-126 (range parsing, `_stream`, `StreamingResponse`, `FileResponse`) verbatim. Remove the `is_uploaded`/`get_core_ctx` block (lines 67-76) and the `MediaNotAvailable` import-based branch (the backend returns `None` instead). Drop now-unused imports (`get_core_ctx`, `MediaNotAvailable`, `is_uploaded`, `Path`) if no longer referenced — let the lint/test run confirm.

- [ ] **Step 4: Run it, expect pass**

Run: `.venv/bin/python -m pytest tests/integration/test_media_route_backend.py -v`
Expected: PASS.

- [ ] **Step 5: Run the existing media route tests**

Run: `.venv/bin/python -m pytest tests/ -k media -v`
Expected: PASS (migrate any test that asserted the old `is_uploaded` local-serving path to drive the backend).

- [ ] **Step 6: Commit**

```bash
git add backend/app/routes/media.py tests/integration/test_media_route_backend.py
git commit -m "refactor: stream_media resolves via MediaCacheBackend.locate"
```

---

## Task 7: Studio upload ingest uploads to GCS in `ai_store` mode

**Files:**
- Modify: `backend/app/routes/studio.py:193-201`
- Test: `tests/integration/test_studio_upload_ai_store.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_studio_upload_ai_store.py
# Drives the upload handler with media_cache="ai_store" and asserts the
# uploaded file is pushed to the AI store (ensure_uploaded called with the
# ("uploaded", <id>) key + the on-disk dest path).
#
# Build the test client the same way the existing studio upload tests do
# (see tests/integration/test_studio_upload*.py for the app + ctx fixture);
# override settings.media_cache="ai_store" and inject a fake ai_store that
# records ensure_uploaded calls. Assert exactly one call whose key[0] ==
# "uploaded" and whose path == data_dir/cache/uploads/<id><ext>.
```

(Mirror the construction in the nearest existing `tests/integration/test_studio_upload*.py`; do not invent a new harness.)

- [ ] **Step 2: Run it, expect fail**

Run: `.venv/bin/python -m pytest tests/integration/test_studio_upload_ai_store.py -v`
Expected: FAIL (ingest never calls `ensure_uploaded`).

- [ ] **Step 3: Edit `studio.py` after the `proxy_cache_repo.record` call (line 201)**

```python
    if s.media_cache == "ai_store":
        # Cloud: the local file is ephemeral. Push it to the AI store so
        # playback (signed URL) survives instance restarts. ctx is the
        # LiveCtx here (uploads require live wiring); ai_store is always set.
        await ctx.ai_store.ensure_uploaded(
            ("uploaded", str(clip_id)), dest, mime
        )
```

(Confirm `ctx` in this handler is a `LiveCtx` with `ai_store`; if it is a `CoreCtx`, fetch the live ctx for this branch via the same dependency the rest of the studio live actions use. Check with `grep -n "get_live_ctx\|get_core_ctx\|ai_store" backend/app/routes/studio.py`.)

- [ ] **Step 4: Run it, expect pass**

Run: `.venv/bin/python -m pytest tests/integration/test_studio_upload_ai_store.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/routes/studio.py tests/integration/test_studio_upload_ai_store.py
git commit -m "feat: studio uploads push to AI store in ai_store mode"
```

---

## Task 8: Flip cloud config + ADR + full green

**Files:**
- Modify: `deploy/cloudrun.env.yaml:31-32`
- Create: `docs/adr/0069-cloud-media-cache-ai-store.md`
- Modify: `docs/decisions.md` (index row)

- [ ] **Step 1: Edit `deploy/cloudrun.env.yaml`**

Replace the `PLAYBACK_SOURCE` line with:

```yaml
# Cloud uses the AI store (GCS) as the only proxy-media cache; local disk
# is ephemeral. Cache writes upload to GCS; playback redirects to signed
# URLs. See docs/specs/2026-06-10-cloud-media-cache-ai-store-design.md.
MEDIA_CACHE: "ai_store"
```

- [ ] **Step 2: Write ADR 0069**

```markdown
# 0069. Cloud media cache: AI-store-only on GCP

**Date:** 2026-06-10
**Status:** Accepted

## Context
Phase 4 of the Cloud Run deployment shipped GCS *read* (signed-URL
playback) but left caching writing to the ephemeral local proxy cache, so
proxies died on every instance restart and the GCS read path was starved.

## Alternatives
- Keep local fallback (Phase-4 design): simplest, but proxies never
  persist on ephemeral Cloud Run disk.
- Write-through (local + GCS): durable, but keeps a useless local copy and
  doubles disk pressure on a 1 GiB instance.
- AI-store-only on cloud (chosen): one `MediaCacheBackend` boundary,
  selected by `MEDIA_CACHE`.

## Decision
Introduce `MediaCacheBackend` (`ensure_cached`/`locate`) with
`LocalProxyBackend` (dev) and `AiStoreBackend` (cloud). Cloud caching
downloads through the tunnel, uploads to GCS, deletes the staging file;
playback is a signed URL; the local proxy cache is unused. `PLAYBACK_SOURCE`
is folded into `MEDIA_CACHE` and removed.

## Consequences
+ Cached/uploaded clips survive instance restarts; CatDV hit once per clip.
+ `locate()` needs no CatDV, so playback works while the tunnel is down.
- Large proxies stage transiently on RAM-backed `/data`; one-at-a-time +
  delete-after-upload bounds peak usage to a single proxy. Bump memory if
  proxies exceed the headroom.
```

- [ ] **Step 3: Add the index row to `docs/decisions.md`**

Append a row: `| 0069 | Cloud media cache: AI-store-only on GCP | Accepted | 2026-06-10 |` (match the table's column shape).

- [ ] **Step 4: Full suite + import contracts**

Run: `.venv/bin/python -m pytest --ignore=<the 3 PIL/radon modules>` then `lint-imports`
Expected: all green, 5 contracts kept.

- [ ] **Step 5: Commit**

```bash
git add deploy/cloudrun.env.yaml docs/adr/0069-cloud-media-cache-ai-store.md docs/decisions.md
git commit -m "feat: flip Cloud Run to MEDIA_CACHE=ai_store + ADR 0069"
```

---

## Deploy & manual acceptance (after all tasks green)

1. Rebuild the image from HEAD via Cloud Build (`gcloud builds submit --tag europe-west3-docker.pkg.dev/catdav/catdv-annotator/app:<sha> --region=europe-west3 .`) and `gcloud run deploy` (same flags as the workflow; `--project=catdav`).
2. Walk the 6 **Manual acceptance flows** in the spec — especially flow 3 (playback still 307 after an instance restart) and flow 5 (cached clip plays with CatDV disconnected).

## Self-review notes (coverage)

- Spec §"`MediaCacheBackend`" → Tasks 2, 3. §"call sites collapse" → Tasks 5 (prefetcher), 6 (route), 7 (uploads). §"Settings" → Tasks 1, 8. §"Offline contract" → Task 4 wiring (`LiveCtx` always has `ai_store`/`gcs`; `get_live_ctx` 503s only when `init_external=False`) + Task 6. §"Error handling" → Task 3 (`finally` cleanup) + Task 5 (queue retry). §"Testing" → per-task tests + Task 8 full suite. §"Manual acceptance flows" → Deploy section.
- Open implementation checks flagged inline (grep for the GCS-service field name in `context.py`; confirm the studio handler's ctx type; migrate existing `media_locator`/`playback_source`/`resolver=`-passing tests). These are existing-code lookups, not new design.
