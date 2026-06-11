# Durable GCS-backed Thumbnail Cache — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give cloud thumbnails (CatDV clip thumbs + uploaded-clip posters) a durable, offline-safe GCS cache so they survive instance restarts and render while CatDV is disconnected — the same guarantee proxies got from ADR 0069.

**Architecture:** A thin `ThumbnailStore` (GCS-backed) is injected into the existing `ThumbnailService`. `/data` stays as a hot cache *in front of* GCS; GCS is the durable source of truth. The store's GET is **not** gated by the CatDV `is_online()` (GCS is a separate network), which is what delivers offline-serve + restart-durability. Wired only in `MEDIA_CACHE=ai_store` (cloud); `None` in local/dev → behavior unchanged.

**Tech Stack:** Python 3.13, FastAPI, `google-cloud-storage`, pytest/pytest-asyncio. Run Python via `.venv/bin/python`. Spec: `docs/specs/2026-06-11-cloud-thumbnail-cache-design.md`.

---

## File structure

| File | Responsibility | Action |
|---|---|---|
| `backend/app/services/gcs.py` | `download_thumb` / `upload_thumb` / `thumb_uri` blob ops at `thumbs/{id}.jpg` | Modify |
| `backend/app/services/thumbnail_store.py` | `ThumbnailStore` protocol + `GcsThumbnailStore` (async wrapper, error handling) | Create |
| `backend/app/services/thumbnail_service.py` | Consult durable store on `/data` miss; push on fetch; `push_durable()` | Modify |
| `backend/app/context.py` | Build `GcsThumbnailStore` in `ai_store` mode, inject into `ThumbnailService` | Modify |
| `backend/app/routes/studio.py` | Push uploaded poster to durable store at ingest (ai_store mode) | Modify |
| `backend/app/routes/media.py` | Uploaded `/thumb` branch: durable GET on `/data` miss | Modify |
| `tests/unit/test_gcs.py` | thumb blob op tests | Modify |
| `tests/unit/test_thumbnail_store.py` | store wrapper tests | Create |
| `tests/unit/test_thumbnail_service.py` | durable get/put/uploaded/regression tests | Modify |
| `tests/integration/test_studio_uploads_api.py` | poster durable-push tests | Modify |
| `tests/integration/test_thumb_durable_route.py` | uploaded `/thumb` durable-serve test | Create |

Branch: `cloud-run-deployment` (already checked out). Commit after each task.

---

### Task 1: GCS thumbnail blob operations

**Files:**
- Modify: `backend/app/services/gcs.py`
- Test: `tests/unit/test_gcs.py`

- [ ] **Step 1: Write the failing tests** — append to `tests/unit/test_gcs.py`:

```python
def test_thumb_uri_path():
    bucket = MagicMock(); bucket.name = "test-bucket"
    service = GcsService.__new__(GcsService); service._bucket = bucket
    assert service.thumb_uri(7) == "gs://test-bucket/thumbs/7.jpg"


def test_download_thumb_returns_false_when_absent(tmp_path: Path):
    bucket = MagicMock(); bucket.name = "test-bucket"
    bucket.get_blob.return_value = None
    service = GcsService.__new__(GcsService); service._bucket = bucket
    assert service.download_thumb(7, tmp_path / "7.jpg") is False


def test_download_thumb_writes_and_returns_true(tmp_path: Path):
    bucket = MagicMock(); bucket.name = "test-bucket"
    blob = MagicMock()
    blob.download_to_filename.side_effect = lambda p, **k: Path(p).write_bytes(b"\xff\xd8jpg")
    bucket.get_blob.return_value = blob
    service = GcsService.__new__(GcsService); service._bucket = bucket
    dest = tmp_path / "7.jpg"
    assert service.download_thumb(7, dest) is True
    assert dest.read_bytes() == b"\xff\xd8jpg"
    bucket.get_blob.assert_called_with("thumbs/7.jpg")


def test_download_thumb_false_on_empty_body(tmp_path: Path):
    bucket = MagicMock(); bucket.name = "test-bucket"
    blob = MagicMock()
    blob.download_to_filename.side_effect = lambda p, **k: Path(p).write_bytes(b"")
    bucket.get_blob.return_value = blob
    service = GcsService.__new__(GcsService); service._bucket = bucket
    dest = tmp_path / "7.jpg"
    assert service.download_thumb(7, dest) is False
    assert not dest.exists()


def test_upload_thumb_overwrites_unconditionally(tmp_path: Path):
    local = tmp_path / "7.jpg"; local.write_bytes(b"jpg")
    bucket = MagicMock(); bucket.name = "test-bucket"
    blob = MagicMock(); bucket.blob.return_value = blob
    service = GcsService.__new__(GcsService); service._bucket = bucket
    uri = service.upload_thumb(7, local)
    blob.upload_from_filename.assert_called_once_with(str(local), content_type="image/jpeg")
    assert uri == "gs://test-bucket/thumbs/7.jpg"
    bucket.blob.assert_called_with("thumbs/7.jpg")
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_gcs.py -v`
Expected: FAIL — `AttributeError: 'GcsService' object has no attribute 'thumb_uri'` / `download_thumb` / `upload_thumb`.

- [ ] **Step 3: Implement the methods** — add to `backend/app/services/gcs.py` inside `class GcsService` (after `delete`, before `signed_url`):

```python
    def thumb_uri(self, clip_id: int) -> str:
        return f"gs://{self._bucket.name}/thumbs/{clip_id}.jpg"

    def download_thumb(self, clip_id: int, dest: Path) -> bool:
        """Download thumbs/{clip_id}.jpg to dest. Return True only if the blob
        existed and a non-empty file was written; False on miss or empty body.
        Blocking (network) — call via asyncio.to_thread."""
        blob = self._bucket.get_blob(f"thumbs/{clip_id}.jpg")
        if blob is None:
            return False
        try:
            blob.download_to_filename(str(dest))
        except Exception:
            Path(dest).unlink(missing_ok=True)
            raise
        if dest.exists() and dest.stat().st_size > 0:
            return True
        dest.unlink(missing_ok=True)
        return False

    def upload_thumb(self, clip_id: int, local_path: Path) -> str:
        """Upload local_path to thumbs/{clip_id}.jpg, overwriting
        unconditionally. JPEGs are tiny, so overwriting on every write kills
        the stale-blob / clip-id-reuse risk (ADR 0070) at write time without
        an md5 compare. Blocking — call via asyncio.to_thread."""
        blob = self._bucket.blob(f"thumbs/{clip_id}.jpg")
        blob.upload_from_filename(str(local_path), content_type="image/jpeg")
        return f"gs://{self._bucket.name}/thumbs/{clip_id}.jpg"
```

(`from pathlib import Path` is already imported at the top of the file.)

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_gcs.py -v`
Expected: PASS (all, including the four pre-existing `upload_if_absent`/`delete` tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/gcs.py tests/unit/test_gcs.py
git commit -m "feat(gcs): thumbnail blob ops (download_thumb/upload_thumb at thumbs/{id}.jpg)"
```

---

### Task 2: ThumbnailStore protocol + GcsThumbnailStore wrapper

**Files:**
- Create: `backend/app/services/thumbnail_store.py`
- Test: `tests/unit/test_thumbnail_store.py`

- [ ] **Step 1: Write the failing tests** — create `tests/unit/test_thumbnail_store.py`:

```python
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from backend.app.services.thumbnail_store import GcsThumbnailStore


@pytest.mark.asyncio
async def test_get_delegates_and_returns_true(tmp_path: Path):
    gcs = MagicMock()
    gcs.download_thumb.return_value = True
    store = GcsThumbnailStore(gcs)
    assert await store.get(7, tmp_path / "7.jpg") is True
    gcs.download_thumb.assert_called_once_with(7, tmp_path / "7.jpg")


@pytest.mark.asyncio
async def test_get_returns_false_on_exception(tmp_path: Path):
    gcs = MagicMock()
    gcs.download_thumb.side_effect = RuntimeError("gcs down")
    store = GcsThumbnailStore(gcs)
    assert await store.get(7, tmp_path / "7.jpg") is False


@pytest.mark.asyncio
async def test_put_delegates(tmp_path: Path):
    gcs = MagicMock()
    store = GcsThumbnailStore(gcs)
    await store.put(7, tmp_path / "7.jpg")
    gcs.upload_thumb.assert_called_once_with(7, tmp_path / "7.jpg")


@pytest.mark.asyncio
async def test_put_swallows_exception(tmp_path: Path):
    gcs = MagicMock()
    gcs.upload_thumb.side_effect = RuntimeError("gcs down")
    store = GcsThumbnailStore(gcs)
    await store.put(7, tmp_path / "7.jpg")  # must NOT raise
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_thumbnail_store.py -v`
Expected: FAIL — `ModuleNotFoundError: backend.app.services.thumbnail_store`.

- [ ] **Step 3: Implement the store** — create `backend/app/services/thumbnail_store.py`:

```python
"""ThumbnailStore — durable (GCS-backed) tier beneath ThumbnailService.

ThumbnailService caches poster JPEGs on /data, which on Cloud Run is an
ephemeral tmpfs wiped on every restart. This store gives them a durable home
in GCS (thumbs/{clip_id}.jpg, same bucket as proxies) so they survive restarts
and serve while CatDV is offline — GCS is a separate network from the CatDV
tunnel. Mirrors the proxy ai_store relationship (ADR 0069)."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Protocol

from backend.app.services.gcs import GcsService

log = logging.getLogger(__name__)


class ThumbnailStore(Protocol):
    async def get(self, clip_id: int, dest: Path) -> bool: ...

    async def put(self, clip_id: int, src: Path) -> None: ...


class GcsThumbnailStore:
    """GCS-backed ThumbnailStore. All blocking SDK calls run in a worker
    thread (CLAUDE.md: no sync I/O inside async def)."""

    def __init__(self, gcs: GcsService) -> None:
        self._gcs = gcs

    async def get(self, clip_id: int, dest: Path) -> bool:
        try:
            return await asyncio.to_thread(self._gcs.download_thumb, clip_id, dest)
        except Exception:  # noqa: BLE001 — transient GCS error ⇒ treat as a miss
            log.debug("thumb store: get(%s) failed", clip_id, exc_info=True)
            return False

    async def put(self, clip_id: int, src: Path) -> None:
        try:
            await asyncio.to_thread(self._gcs.upload_thumb, clip_id, src)
        except Exception:  # noqa: BLE001 — best-effort; never mask the served thumb
            log.warning("thumb store: put(%s) failed", clip_id, exc_info=True)
```

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_thumbnail_store.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/thumbnail_store.py tests/unit/test_thumbnail_store.py
git commit -m "feat(thumbnail): GcsThumbnailStore durable layer (async wrapper, miss/error → graceful)"
```

---

### Task 3: Wire the durable store into ThumbnailService

**Files:**
- Modify: `backend/app/services/thumbnail_service.py`
- Test: `tests/unit/test_thumbnail_service.py`

- [ ] **Step 1: Write the failing tests** — append to `tests/unit/test_thumbnail_service.py`:

```python
class _FakeDurable:
    def __init__(self, has: set[int] | None = None):
        self.has = has or set()
        self.get_calls: list[int] = []
        self.put_calls: list[int] = []

    async def get(self, clip_id, dest):
        self.get_calls.append(clip_id)
        if clip_id in self.has:
            Path(dest).write_bytes(b"\xff\xd8GCS")
            return True
        return False

    async def put(self, clip_id, src):
        self.put_calls.append(clip_id)


@pytest.mark.asyncio
async def test_durable_hit_serves_offline_without_catdv(tmp_path: Path):
    # /data miss + GCS hit while CatDV OFFLINE → served from GCS, no CatDV call.
    catdv = _FakeCatdv()
    durable = _FakeDurable(has={42})
    svc = ThumbnailService(
        cache_dir=tmp_path, archive=_FakeArchive({"posterID": 9000}),
        catdv=catdv, is_online_provider=lambda: False, durable_store=durable,
    )
    out = await svc.get_or_fetch(42)
    assert out == tmp_path / "42.jpg"
    assert out.read_bytes() == b"\xff\xd8GCS"
    assert catdv.calls == []
    assert durable.get_calls == [42]


@pytest.mark.asyncio
async def test_durable_miss_online_fetches_and_puts(tmp_path: Path):
    catdv = _FakeCatdv()
    durable = _FakeDurable(has=set())
    svc = ThumbnailService(
        cache_dir=tmp_path, archive=_FakeArchive({"posterID": 9000}),
        catdv=catdv, durable_store=durable,
    )
    out = await svc.get_or_fetch(42)
    assert out == tmp_path / "42.jpg"
    assert catdv.calls == [9000]
    assert durable.put_calls == [42]


@pytest.mark.asyncio
async def test_durable_miss_offline_returns_none(tmp_path: Path):
    catdv = _FakeCatdv()
    durable = _FakeDurable(has=set())
    svc = ThumbnailService(
        cache_dir=tmp_path, archive=_FakeArchive({"posterID": 9000}),
        catdv=catdv, is_online_provider=lambda: False, durable_store=durable,
    )
    assert await svc.get_or_fetch(42) is None
    assert catdv.calls == []
    assert durable.put_calls == []


@pytest.mark.asyncio
async def test_data_hit_skips_durable(tmp_path: Path):
    (tmp_path / "42.jpg").write_bytes(b"local")
    durable = _FakeDurable(has={42})
    svc = ThumbnailService(
        cache_dir=tmp_path, archive=_FakeArchive({"posterID": 9000}),
        catdv=_FakeCatdv(), durable_store=durable,
    )
    out = await svc.get_or_fetch(42)
    assert out.read_bytes() == b"local"
    assert durable.get_calls == []


@pytest.mark.asyncio
async def test_no_durable_store_unchanged_behavior(tmp_path: Path):
    catdv = _FakeCatdv()
    svc = ThumbnailService(
        cache_dir=tmp_path, archive=_FakeArchive({"posterID": 9000}),
        catdv=catdv, is_online_provider=lambda: False,
    )
    assert await svc.get_or_fetch(42) is None


@pytest.mark.asyncio
async def test_uploaded_clip_served_from_durable(tmp_path: Path):
    from backend.app.uploaded_ids import to_clip_id
    cid = to_clip_id(3)
    durable = _FakeDurable(has={cid})
    svc = ThumbnailService(
        cache_dir=tmp_path, archive=_FakeArchive({}), catdv=None, durable_store=durable,
    )
    out = await svc.get_or_fetch(cid)
    assert out == svc.path_for(cid)
    assert durable.get_calls == [cid]


@pytest.mark.asyncio
async def test_push_durable_forwards(tmp_path: Path):
    durable = _FakeDurable()
    svc = ThumbnailService(
        cache_dir=tmp_path, archive=_FakeArchive({}), catdv=None, durable_store=durable,
    )
    p = tmp_path / "x.jpg"; p.write_bytes(b"jpg")
    await svc.push_durable(99, p)
    assert durable.put_calls == [99]
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_thumbnail_service.py -v`
Expected: FAIL — `TypeError: ThumbnailService.__init__() got an unexpected keyword argument 'durable_store'`.

- [ ] **Step 3: Implement** — four edits in `backend/app/services/thumbnail_service.py`:

**3a.** Add the type import under the existing `TYPE_CHECKING` block (after the `CatdvClient` import line):

```python
if TYPE_CHECKING:
    from backend.app.archive.provider import ArchiveProvider
    from backend.app.services.catdv_client import CatdvClient
    from backend.app.services.thumbnail_store import ThumbnailStore
```

**3b.** Add the constructor parameter and field. Change the `__init__` signature to add `durable_store` (after `metadata_cached_provider`):

```python
        metadata_cached_provider: (
            Callable[[int], bool] | Callable[[int], Awaitable[bool]] | None
        ) = None,
        durable_store: ThumbnailStore | None = None,
    ) -> None:
```

and at the end of `__init__`, after `self._metadata_cached = metadata_cached_provider`:

```python
        # Durable GCS-backed tier (cloud only). When set, a /data miss falls
        # through to GCS *before* giving up — and GCS access is NOT gated by
        # the CatDV is_online() closure, so cached thumbs serve offline and
        # across restarts. None in local/dev mode → behavior unchanged.
        self._durable = durable_store
```

**3c.** In `get_or_fetch`, insert the durable GET immediately after the `/data` hit check and **before** the `is_uploaded` check. The current lines are:

```python
        if dest.exists() and dest.stat().st_size > 0:  # sync-io-ok: pre-existing, tracked for the tier-4 async-io pass
            return dest
        if is_uploaded(clip_id):
```

Insert between them:

```python
        if dest.exists() and dest.stat().st_size > 0:  # sync-io-ok: pre-existing, tracked for the tier-4 async-io pass
            return dest
        if self._durable is not None and await self._durable.get(clip_id, dest):
            # GCS hit — works even when CatDV is offline or this is an upload.
            return dest
        if is_uploaded(clip_id):
```

**3d.** Push to the durable store after a successful CatDV fetch. Change the tail of `get_or_fetch` (the current `download_thumbnail` success block):

```python
        if dest.exists() and dest.stat().st_size > 0:  # sync-io-ok: pre-existing, tracked for the tier-4 async-io pass
            return dest
        return None
```

to:

```python
        if dest.exists() and dest.stat().st_size > 0:  # sync-io-ok: pre-existing, tracked for the tier-4 async-io pass
            if self._durable is not None:
                await self._durable.put(clip_id, dest)
            return dest
        return None
```

**3e.** Cover the image-poster path too. Change the call site `return await self._build_image_poster(clip, dest)` to pass `clip_id`:

```python
        if not thumb_id:
            return await self._build_image_poster(clip_id, clip, dest)
```

update the method signature:

```python
    async def _build_image_poster(self, clip_id: int, clip, dest: Path) -> Path | None:
```

and its success tail:

```python
        if dest.exists() and dest.stat().st_size > 0:  # sync-io-ok: pre-existing, tracked for the tier-4 async-io pass
            if self._durable is not None:
                await self._durable.put(clip_id, dest)
            return dest
        return None
```

**3f.** Add the `push_durable` method (used by the studio upload route). Place it after `get_or_fetch`:

```python
    async def push_durable(self, clip_id: int, src: Path) -> None:
        """Mirror a poster already written to /data into the durable GCS
        store. No-op when no durable store is wired (local/dev mode)."""
        if self._durable is not None:
            await self._durable.put(clip_id, src)
```

- [ ] **Step 4: Run to verify all thumbnail tests pass**

Run: `.venv/bin/python -m pytest tests/unit/test_thumbnail_service.py tests/unit/test_thumbnail_service_image.py tests/unit/test_thumbnail_uploaded_guard.py -v`
Expected: PASS (new tests + all pre-existing, including the image-poster test that exercises the changed `_build_image_poster` signature).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/thumbnail_service.py tests/unit/test_thumbnail_service.py
git commit -m "feat(thumbnail): consult durable GCS store on /data miss + push on fetch"
```

---

### Task 4: Build & inject the store in ai_store mode (context wiring)

**Files:**
- Modify: `backend/app/context.py` (the `ThumbnailService(...)` construction inside `_build_archive_subsystem`, ~line 640)

- [ ] **Step 1: Edit the wiring.** Find the block that builds `thumbnail_service` (inside `if use_catdv:`). Immediately **before** the `thumbnail_service = ThumbnailService(` call, add:

```python
        durable_thumb_store = None
        if settings.media_cache == "ai_store":
            from backend.app.services.thumbnail_store import GcsThumbnailStore

            durable_thumb_store = GcsThumbnailStore(gcs_service)
```

then add the new kwarg to the constructor call:

```python
        thumbnail_service = ThumbnailService(
            cache_dir=settings.data_dir / "cache" / "thumbs",
            archive=archive,
            catdv=catdv,
            is_online_provider=_is_online,
            metadata_cached_provider=_has_clip_metadata,
            durable_store=durable_thumb_store,
        )
```

(`gcs_service` is already in scope — built earlier in this function. `settings.media_cache` is the same field `build_media_cache_backend` reads.)

- [ ] **Step 2: Verify it imports and the suite is unaffected**

Run: `.venv/bin/python -c "import backend.app.context"`
Expected: no error.
Run: `.venv/bin/python -m pytest tests/unit/test_media_cache_factory.py tests/unit/test_settings_media_cache.py -v`
Expected: PASS (sanity that ai_store-mode settings still resolve).

- [ ] **Step 3: Run lint-imports** (the change adds an intra-`services` import, which is allowed):

Run: `.venv/bin/python -m lint_imports 2>/dev/null || lint-imports`
Expected: contracts kept (0 broken).

- [ ] **Step 4: Commit**

```bash
git add backend/app/context.py
git commit -m "feat(context): inject GcsThumbnailStore into ThumbnailService in ai_store mode"
```

*Note: full end-to-end wiring (real GcsService instantiation) is proven by Manual Acceptance Flows 2 & 3 in the spec — it needs live GCS creds and isn't unit-tested, by the same convention as the existing media-cache wiring.*

---

### Task 5: Push uploaded poster to GCS at ingest

**Files:**
- Modify: `backend/app/routes/studio.py` (the `if poster is not None:` block, ~line 212)
- Test: `tests/integration/test_studio_uploads_api.py`

- [ ] **Step 1: Write the failing tests** — append to `tests/integration/test_studio_uploads_api.py` (the helpers `_make_app_ai_store`, `install_live_ctx`, `TestClient`, `AsyncMock`, `MagicMock`, `Path` are already imported/defined in this file):

```python
def test_upload_poster_ai_store_pushes_durable(monkeypatch, tmp_path):
    app, data_dir = _make_app_ai_store(monkeypatch, tmp_path)
    fake_thumb = MagicMock()
    fake_thumb.push_durable = AsyncMock()
    fake_ai_store = MagicMock()
    fake_ai_store.ensure_uploaded = AsyncMock(return_value=MagicMock())

    with TestClient(app) as client:
        install_live_ctx(client.app, ai_store=fake_ai_store, thumbnail_service=fake_thumb)
        r = client.post(
            "/api/studio/uploads",
            files={
                "file": ("clip.mp4", b"mp4-content", "video/mp4"),
                "poster": ("p.jpg", b"\xff\xd8jpg", "image/jpeg"),
            },
        )
        assert r.status_code == 201, r.text
        clip_id = r.json()["clip_id"]

    fake_thumb.push_durable.assert_called_once()
    args = fake_thumb.push_durable.call_args.args
    assert args[0] == clip_id
    assert Path(args[1]) == data_dir / "cache" / "thumbs" / f"{clip_id}.jpg"


def test_upload_poster_local_mode_no_durable_push(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.delenv("MEDIA_CACHE", raising=False)
    monkeypatch.delenv("CATDV_PASSWORD", raising=False)
    from backend.app import main as main_mod
    importlib.reload(main_mod)
    app = main_mod.app

    fake_thumb = MagicMock()
    fake_thumb.push_durable = AsyncMock()

    with TestClient(app) as client:
        install_live_ctx(client.app, thumbnail_service=fake_thumb)
        r = client.post(
            "/api/studio/uploads",
            files={
                "file": ("clip.mp4", b"mp4-content", "video/mp4"),
                "poster": ("p.jpg", b"\xff\xd8jpg", "image/jpeg"),
            },
        )
        assert r.status_code == 201, r.text

    fake_thumb.push_durable.assert_not_called()
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/integration/test_studio_uploads_api.py -k poster -v`
Expected: FAIL — `push_durable` never called (the ai_store test asserts called_once).

- [ ] **Step 3: Implement** — in `backend/app/routes/studio.py`, extend the poster block. After the existing `await asyncio.to_thread(_write_poster)` line, add:

```python
        await asyncio.to_thread(_write_poster)

        if s.media_cache == "ai_store":
            # Cloud: /data is ephemeral — mirror the poster into the durable
            # GCS store so it survives instance restarts (parallels the proxy
            # push above). Additive to the local write.
            live = request.app.state.live_ctx
            if live is not None and live.thumbnail_service is not None:
                await live.thumbnail_service.push_durable(clip_id, thumb_dest)
```

(`s.media_cache`, `request`, `clip_id`, `thumb_dest` are all already in scope in this handler.)

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/bin/python -m pytest tests/integration/test_studio_uploads_api.py -v`
Expected: PASS (new poster tests + all pre-existing upload tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/routes/studio.py tests/integration/test_studio_uploads_api.py
git commit -m "feat(studio): push uploaded poster to durable GCS store in ai_store mode"
```

---

### Task 6: Serve uploaded posters from GCS after a /data miss

**Files:**
- Modify: `backend/app/routes/media.py` (the `if is_uploaded(clip_id):` branch in `stream_thumbnail`)
- Test: `tests/integration/test_thumb_durable_route.py` (create)

- [ ] **Step 1: Write the failing test** — create `tests/integration/test_thumb_durable_route.py`:

```python
import importlib
from unittest.mock import AsyncMock, MagicMock

from fastapi.testclient import TestClient

from tests._helpers.live_ctx import install_live_ctx


def _make_app_ai_store(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("MEDIA_CACHE", "ai_store")
    monkeypatch.delenv("CATDV_PASSWORD", raising=False)
    from backend.app import main as main_mod
    importlib.reload(main_mod)
    return main_mod.app, tmp_path


def test_uploaded_thumb_served_from_durable_after_restart(monkeypatch, tmp_path):
    # Simulates a restarted instance: /data/cache/thumbs is empty, but the
    # poster is in GCS. The route must fall through to the durable store.
    app, data_dir = _make_app_ai_store(monkeypatch, tmp_path)
    from backend.app.uploaded_ids import to_clip_id
    cid = to_clip_id(5)
    poster_file = data_dir / "cache" / "thumbs" / f"{cid}.jpg"

    async def _fake_get_or_fetch(clip_id):
        # Mimics ThumbnailService pulling from GCS into /data, then returning it.
        poster_file.parent.mkdir(parents=True, exist_ok=True)
        poster_file.write_bytes(b"\xff\xd8GCS")
        return poster_file

    fake_thumb = MagicMock()
    fake_thumb.get_or_fetch = AsyncMock(side_effect=_fake_get_or_fetch)

    with TestClient(app) as client:
        install_live_ctx(client.app, thumbnail_service=fake_thumb)
        r = client.get(f"/api/media/{cid}/thumb")

    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "image/jpeg"
    assert r.content == b"\xff\xd8GCS"
    fake_thumb.get_or_fetch.assert_called_once_with(cid)


def test_uploaded_thumb_404_when_durable_misses(monkeypatch, tmp_path):
    app, data_dir = _make_app_ai_store(monkeypatch, tmp_path)
    from backend.app.uploaded_ids import to_clip_id
    cid = to_clip_id(6)

    fake_thumb = MagicMock()
    fake_thumb.get_or_fetch = AsyncMock(return_value=None)

    with TestClient(app) as client:
        install_live_ctx(client.app, thumbnail_service=fake_thumb)
        r = client.get(f"/api/media/{cid}/thumb")

    assert r.status_code == 404
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/integration/test_thumb_durable_route.py -v`
Expected: FAIL — first test gets 404 (current uploaded branch returns `_thumb_404` on `/data` miss, never consulting the durable store).

- [ ] **Step 3: Implement** — in `backend/app/routes/media.py`, replace the uploaded branch body. The current branch:

```python
    if is_uploaded(clip_id):
        core = get_core_ctx(request)
        path = core.settings.data_dir / "cache" / "thumbs" / f"{clip_id}.jpg"
        if not path.exists() or path.stat().st_size == 0:  # sync-io-ok: uploaded poster lookup, tracked for the tier-4 async-io pass
            return _thumb_404("no thumbnail")
        return FileResponse(
            path,
            media_type="image/jpeg",
            headers={"Cache-Control": "public, max-age=86400"},
        )
```

becomes:

```python
    if is_uploaded(clip_id):
        core = get_core_ctx(request)
        path = core.settings.data_dir / "cache" / "thumbs" / f"{clip_id}.jpg"
        if path.exists() and path.stat().st_size > 0:  # sync-io-ok: uploaded poster lookup, tracked for the tier-4 async-io pass
            return FileResponse(
                path,
                media_type="image/jpeg",
                headers={"Cache-Control": "public, max-age=86400"},
            )
        # /data miss (e.g. tmpfs wiped by a restart): fall through to the
        # durable GCS-backed store via the live thumbnail service, if wired.
        # GCS access doesn't need CatDV, so this works while disconnected.
        live = request.app.state.live_ctx
        if live is not None and live.thumbnail_service is not None:
            durable_path = await live.thumbnail_service.get_or_fetch(clip_id)
            if durable_path is not None:
                return FileResponse(
                    durable_path,
                    media_type="image/jpeg",
                    headers={"Cache-Control": "public, max-age=86400"},
                )
        return _thumb_404("no thumbnail")
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/integration/test_thumb_durable_route.py -v`
Expected: PASS (both tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/routes/media.py tests/integration/test_thumb_durable_route.py
git commit -m "feat(media): serve uploaded posters from durable GCS store on /data miss"
```

---

### Task 7: Full verification

**Files:** none (verification only)

- [ ] **Step 1: Run the full unit + integration suite**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: all green (baseline was 1327 passed; this adds ~19 tests, no failures).

- [ ] **Step 2: Run the architecture guards explicitly**

Run: `.venv/bin/python -m pytest tests/unit/test_no_sync_fs_in_async.py tests/unit/test_context_delegation.py tests/unit/test_templates_shared.py -v`
Expected: PASS — confirms no un-pragma'd sync I/O was added in an `async def` and the context split is intact.

- [ ] **Step 3: Run lint-imports**

Run: `lint-imports`
Expected: `Contracts: N kept, 0 broken.`

- [ ] **Step 4: Update the decisions index + ADR**

Add an ADR `docs/adr/0071-durable-thumbnail-cache.md` (MADR-lite: Context / Alternatives / Decision / Consequences — covers "thumbnails got the same GCS treatment as proxies; no DB index because GCS is the index, unlike proxies' ai_store_files; /data is a hot cache; overwrite-on-put for stale-blob safety"). Add its row to the table in `docs/decisions.md`.

```bash
git add docs/adr/0071-durable-thumbnail-cache.md docs/decisions.md
git commit -m "docs: ADR 0071 durable GCS-backed thumbnail cache"
```

- [ ] **Step 5: Deploy & walk the manual acceptance flows**

Build + deploy per the handover cheatsheet (`docs/plans/2026-06-10-cloud-media-cache-HANDOVER.md`), then walk Manual Acceptance Flows 1–5 from the spec against the live service. Flow 2 (CatDV thumb survives restart) and Flow 3 (uploaded poster survives restart) are the durability proofs. Mind the single CatDV seat: Connect/Disconnect via the API, always Disconnect while the tunnel is up.

---

## Self-review notes

- **Spec coverage:** ThumbnailStore (Task 2) ✓; GcsThumbnailStore at `thumbs/{id}.jpg` (Tasks 1–2) ✓; `/data` hot cache + GCS backfill not gated by `is_online()` (Task 3, step 3c) ✓; CatDV-fetch → GCS push (Task 3, 3d/3e) ✓; uploaded poster push at ingest (Task 5) ✓; uploaded poster served from GCS on `/data` miss (Task 6) ✓; mode-gated wiring, `None` in local (Task 4) ✓; unconditional-overwrite put for stale-blob safety (Task 1) ✓; all blocking SDK calls in `asyncio.to_thread` (Task 2) ✓; local-mode regression guard (Task 3 `test_no_durable_store_unchanged_behavior`, Task 5 local test) ✓; the headline offline test (Task 3 `test_durable_hit_serves_offline_without_catdv`) ✓.
- **Type consistency:** `ThumbnailStore.get(clip_id, dest) -> bool` / `put(clip_id, src) -> None` used identically in store, service (`self._durable`), and fakes. `download_thumb`/`upload_thumb`/`thumb_uri` signatures match between `gcs.py` and the store wrapper.
- **No placeholders:** every step has runnable code/commands and expected output.
