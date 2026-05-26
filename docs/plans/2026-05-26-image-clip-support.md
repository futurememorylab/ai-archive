# Image (still) Clip Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make CatDV still-image clips viewable, cacheable, and annotatable by fetching their original file from `GET /media/{mediaID}?type=orig` instead of the non-existent video proxy.

**Architecture:** A clip is classified as an image by its `media.filePath` extension. `RestProxyResolver` and `ThumbnailService` branch on that: images download the original via a new `CatdvClient.download_original`; the resolver caches it as `{clip_id}.{ext}`, the thumbnail service downscales it to a cached poster with Pillow. The detail template renders `<img>` for images. The existing Gemini annotation pipeline is already media-agnostic (it MIME-guesses from the cached file path and skips the timecode anchor at `duration==0`), so no annotator change is needed.

**Tech Stack:** Python 3.12, FastAPI, httpx, aiosqlite, Jinja2/Alpine, Pillow (new), pytest + respx + an in-process fake CatDV server.

**Spec:** `docs/specs/2026-05-26-image-clip-support-design.md`

---

### Task 1: Image-kind helper

A single source of truth for "is this clip an image?", used by the resolver, thumbnail service, and view model. Top-level leaf module (mirrors `backend/app/timecode.py`) so every layer can import it without violating the import-linter contracts.

**Files:**
- Create: `backend/app/media_kind.py`
- Test: `tests/unit/test_media_kind.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_media_kind.py
import pytest

from backend.app.media_kind import is_image_path


@pytest.mark.parametrize(
    "path",
    [
        "/Volumes/ARECA/x/Abramcukova Anna 101.JPG",
        "photo.jpeg",
        "scan.PNG",
        "neg.tif",
        "neg.tiff",
        "anim.gif",
        "bitmap.bmp",
        "modern.webp",
        "iphone.heic",
    ],
)
def test_image_paths_are_images(path):
    assert is_image_path(path) is True


@pytest.mark.parametrize(
    "path",
    [
        "/Volumes/ARECA/x/ARNOLD Bogdan Sis 101.mov",
        "clip.mp4",
        "movie.mkv",
        "broadcast.mxf",
        "noext",
        "",
        None,
    ],
)
def test_non_image_paths_are_not_images(path):
    assert is_image_path(path) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/unit/test_media_kind.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'backend.app.media_kind'`

- [ ] **Step 3: Write minimal implementation**

```python
# backend/app/media_kind.py
"""Classify a media file as a still image vs. time-based media, by file
extension. The one source of truth shared by the proxy resolver, the
thumbnail service, and the clips view model.

Extension is authoritative here: CatDV reports stills with
``format = "Unknown"`` and ``duration = 0``, and the ``media.still`` flag
was observed ``false`` even on a real JPEG — so neither is reliable.
"""

from __future__ import annotations

from pathlib import PurePosixPath

IMAGE_EXTS: frozenset[str] = frozenset(
    {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".gif", ".bmp", ".webp", ".heic"}
)


def is_image_path(path: str | None) -> bool:
    """True if `path`'s extension names a still-image format."""
    if not path:
        return False
    return PurePosixPath(path).suffix.lower() in IMAGE_EXTS
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/unit/test_media_kind.py -v`
Expected: PASS (all parametrized cases)

- [ ] **Step 5: Commit**

```bash
git add backend/app/media_kind.py tests/unit/test_media_kind.py
git commit -m "feat(media): add is_image_path classifier"
```

---

### Task 2: `CatdvClient.download_original` + fake endpoint

Add the REST call that fetches a clip's original source file. Mirrors `download_proxy`'s auth/relogin/stream plumbing, but targets `GET /media/{mediaID}?type=orig` (verified to return the original image bytes; `type=proxy` 404s for stills).

**Files:**
- Modify: `backend/app/services/catdv_client.py` (add method after `download_proxy`, ~line 177)
- Modify: `tests/fakes/fake_catdv.py` (add `originals` dict + route)
- Test: `tests/integration/test_catdv_client_original.py`

- [ ] **Step 1: Extend the fake CatDV server**

In `tests/fakes/fake_catdv.py`, add `self.originals: dict[int, bytes] = {}` next to the existing `self.proxies` line (~line 26), then register this route inside `_register_routes` (place it after the `get_media` route, ~line 141):

```python
        @self.app.get("/catdv/api/9/media/{media_id}")
        async def get_original_media(media_id: int, request: Request):
            # Mirror CatDV: missing session → HTTP 200 + AUTH envelope.
            if (
                time.time() < self.force_auth_until
                or request.cookies.get("JSESSIONID") != "fake-session"
            ):
                return self._envelope("AUTH")
            if request.query_params.get("type") != "orig":
                return Response(status_code=404)  # no proxy for stills
            blob = self.originals.get(media_id)
            if blob is None:
                return Response(status_code=404)
            return Response(content=blob, media_type="image/jpeg")
```

- [ ] **Step 2: Write the failing test**

```python
# tests/integration/test_catdv_client_original.py
import time
from pathlib import Path

import pytest

from backend.app.services.catdv_client import CatdvClient
from tests.fakes.fake_catdv import running_fake_catdv


@pytest.mark.asyncio
async def test_download_original_writes_bytes(tmp_path: Path):
    blob = b"\xff\xd8\xff" + b"IMGDATA" * 32
    with running_fake_catdv() as (base_url, fake):
        fake.originals[881519] = blob
        out = tmp_path / "888745.jpg"
        client = CatdvClient(base_url=base_url, username="klientAI", password="secret")
        async with client:
            await client.login()
            await client.download_original(881519, out)
        assert out.read_bytes() == blob


@pytest.mark.asyncio
async def test_download_original_missing_raises(tmp_path: Path):
    with running_fake_catdv() as (base_url, fake):
        out = tmp_path / "888745.jpg"
        client = CatdvClient(base_url=base_url, username="klientAI", password="secret")
        async with client:
            await client.login()
            with pytest.raises(Exception):
                await client.download_original(999999, out)


@pytest.mark.asyncio
async def test_download_original_reauths_then_streams(tmp_path: Path):
    blob = b"\xff\xd8\xff" + b"IMGDATA" * 32
    with running_fake_catdv() as (base_url, fake):
        fake.originals[881519] = blob
        out = tmp_path / "888745.jpg"
        client = CatdvClient(base_url=base_url, username="klientAI", password="secret")
        async with client:
            await client.login()
            fake.force_auth_until = time.time() + 0.001
            await client.download_original(881519, out)
        assert out.read_bytes() == blob
```

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/bin/pytest tests/integration/test_catdv_client_original.py -v`
Expected: FAIL with `AttributeError: 'CatdvClient' object has no attribute 'download_original'`

- [ ] **Step 4: Implement `download_original`**

In `backend/app/services/catdv_client.py`, add this method immediately after `download_proxy` (before `download_thumbnail`):

```python
    async def download_original(
        self, media_id: int, dest: Path, chunk_size: int = 1024 * 1024
    ) -> None:
        """Stream a clip's ORIGINAL source file (not the proxy) to `dest`.

        Used for stills, which have no generated proxy. Hits
        `GET /api/9/media/{media_id}?type=orig` (the `media_id` comes from
        the clip's `provider_data["media"]["ID"]`). `type` defaults to
        `proxy` server-side, which 404s for stills — `orig` is required.
        Same HTTP-200-AUTH-envelope guard as `download_proxy`.
        """
        if not self._logged_in:
            await self.login()
        url = f"{self._base}/catdv/api/9/media/{media_id}"
        params = {"type": "orig"}
        async with self.http.stream("GET", url, params=params) as resp:
            if resp.status_code == 401 or _is_auth_envelope(resp):
                await self.login()
                async with self.http.stream("GET", url, params=params) as resp2:
                    resp2.raise_for_status()
                    await self._stream_to_file(
                        resp2, dest, append=False, chunk_size=chunk_size
                    )
                    return
            resp.raise_for_status()
            await self._stream_to_file(resp, dest, append=False, chunk_size=chunk_size)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/pytest tests/integration/test_catdv_client_original.py -v`
Expected: PASS (3 tests). Also re-run the existing client tests to confirm no regression:
`.venv/bin/pytest tests/integration/test_catdv_client_thumbnail.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/catdv_client.py tests/fakes/fake_catdv.py tests/integration/test_catdv_client_original.py
git commit -m "feat(catdv): download_original via /media/{id}?type=orig"
```

---

### Task 3: RestProxyResolver image branch

Teach the resolver to detect images and cache the original as `{clip_id}{ext}` (e.g. `{id}.jpg`), keeping the video path on `{id}.mov`. A recorded-row fast path serves cache hits without a `get_clip` round-trip (and keeps offline image hits working). The `archive` reference is already passed by `build_resolver` in the `rest` branch — only the resolver needs to accept and use it.

**Files:**
- Modify: `backend/app/services/proxy_resolver.py` (`RestProxyResolver.__init__`, `path_for_clip_id`, `build_resolver` rest branch)
- Test: `tests/integration/test_rest_proxy_resolver_image.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_rest_proxy_resolver_image.py
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.app.repositories.proxy_cache import ProxyCacheRepo
from backend.app.services.proxy_resolver import RestProxyResolver


class _FakeArchive:
    def __init__(self, provider_data: dict):
        self._pd = provider_data

    async def get_clip(self, clip_id: str):
        return SimpleNamespace(provider_data=self._pd)


class _FakeCatdv:
    def __init__(self):
        self.proxy_calls: list[int] = []
        self.original_calls: list[int] = []

    async def download_proxy(self, clip_id: int, dest: Path) -> None:
        self.proxy_calls.append(clip_id)
        dest.write_bytes(b"PROXY-OK!")  # noqa: ASYNC240

    async def download_original(self, media_id: int, dest: Path) -> None:
        self.original_calls.append(media_id)
        dest.write_bytes(b"\xff\xd8\xffJPEG-ORIGINAL")  # noqa: ASYNC240


IMAGE_PD = {"media": {"ID": 881519, "filePath": "/Volumes/ARECA/x/Anna 101.JPG"}}
VIDEO_PD = {"media": {"ID": 770000, "filePath": "/Volumes/ARECA/x/Bogdan 1.mov"}}


@pytest.mark.asyncio
async def test_image_clip_downloads_original_as_jpg(db, tmp_path):
    repo = ProxyCacheRepo()
    catdv = _FakeCatdv()
    resolver = RestProxyResolver(
        catdv=catdv,
        cache_dir=tmp_path / "cache",
        proxy_cache_repo=repo,
        db_provider=lambda: db,
        archive=_FakeArchive(IMAGE_PD),
    )
    path = await resolver.path_for_clip_id(888745)

    assert path.name == "888745.jpg"
    assert path.read_bytes() == b"\xff\xd8\xffJPEG-ORIGINAL"
    assert catdv.original_calls == [881519]
    assert catdv.proxy_calls == []
    row = await repo.get(db, 888745)
    assert row is not None
    assert row["file_path"] == str(path)


@pytest.mark.asyncio
async def test_image_clip_cache_hit_skips_get_clip(db, tmp_path):
    repo = ProxyCacheRepo()
    catdv = _FakeCatdv()
    resolver = RestProxyResolver(
        catdv=catdv,
        cache_dir=tmp_path / "cache",
        proxy_cache_repo=repo,
        db_provider=lambda: db,
        archive=_FakeArchive(IMAGE_PD),
    )
    await resolver.path_for_clip_id(888745)
    await resolver.path_for_clip_id(888745)
    assert catdv.original_calls == [881519]  # second call is a cache hit


@pytest.mark.asyncio
async def test_video_clip_still_uses_proxy_mov(db, tmp_path):
    repo = ProxyCacheRepo()
    catdv = _FakeCatdv()
    resolver = RestProxyResolver(
        catdv=catdv,
        cache_dir=tmp_path / "cache",
        proxy_cache_repo=repo,
        db_provider=lambda: db,
        archive=_FakeArchive(VIDEO_PD),
    )
    path = await resolver.path_for_clip_id(888894)
    assert path.name == "888894.mov"
    assert path.read_bytes() == b"PROXY-OK!"
    assert catdv.proxy_calls == [888894]
    assert catdv.original_calls == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/integration/test_rest_proxy_resolver_image.py -v`
Expected: FAIL — `RestProxyResolver.__init__` got an unexpected keyword argument `archive`.

- [ ] **Step 3: Implement the resolver changes**

In `backend/app/services/proxy_resolver.py`:

Add the import near the top (after the existing imports):

```python
from backend.app.media_kind import is_image_path
```

Replace `RestProxyResolver.__init__` to accept `archive`:

```python
    def __init__(
        self,
        catdv,
        cache_dir: Path,
        *,
        proxy_cache_repo: ProxyCacheRepo | None = None,
        db_provider: Callable[[], aiosqlite.Connection] | None = None,
        archive=None,
    ) -> None:
        self._catdv = catdv
        self._cache_dir = cache_dir
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._repo = proxy_cache_repo
        self._db_provider = db_provider
        self._archive = archive
```

Replace the whole `path_for_clip_id` method with:

```python
    async def path_for_clip_id(self, clip_id: int) -> Path:
        # Cache hit by recorded row — avoids a get_clip round-trip and works
        # for both image ({id}.jpg) and video ({id}.mov) files. Also repairs
        # legacy rows with NULL provider_* columns.
        if self._repo is not None and self._db_provider is not None:
            conn = self._db_provider()
            existing = await self._repo.get(conn, clip_id)
            if existing is not None:
                cached = Path(existing["file_path"])
                if cached.exists() and cached.stat().st_size > 0:
                    if existing.get("provider_id"):
                        await self._repo.touch(conn, clip_id)
                    else:
                        await self._repo.record(
                            conn,
                            clip_id=clip_id,
                            file_path=str(cached),
                            size_bytes=cached.stat().st_size,
                            etag=None,
                            provider_id="catdv",
                            provider_clip_id=str(clip_id),
                        )
                    return cached

        dest, download = await self._dest_and_downloader(clip_id)
        if not dest.exists() or dest.stat().st_size == 0:
            await download()

        if self._repo is not None and self._db_provider is not None:
            conn = self._db_provider()
            await self._repo.record(
                conn,
                clip_id=clip_id,
                file_path=str(dest),
                size_bytes=dest.stat().st_size,
                etag=None,
                provider_id="catdv",
                provider_clip_id=str(clip_id),
            )
        return dest

    async def _dest_and_downloader(self, clip_id: int):
        """Return (dest_path, async download callable) for this clip.

        Images → original file at {id}{ext} via download_original; everything
        else → web proxy at {id}.mov via download_proxy. Falls back to the
        video path when no archive is wired (preserves legacy behaviour).
        """
        if self._archive is not None:
            clip = await self._archive.get_clip(str(clip_id))
            media = clip.provider_data.get("media") or {}
            file_path = media.get("filePath")
            media_id = media.get("ID")
            if is_image_path(file_path) and media_id is not None:
                ext = Path(file_path).suffix.lower()
                dest = self._cache_dir / f"{clip_id}{ext}"
                mid = int(media_id)

                async def _dl_image() -> None:
                    await self._catdv.download_original(mid, dest)

                return dest, _dl_image

        dest = self._cache_dir / f"{clip_id}.mov"

        async def _dl_video() -> None:
            await self._catdv.download_proxy(clip_id, dest)

        return dest, _dl_video
```

Then update the `rest` branch of `build_resolver` to forward `archive`:

```python
    if source == "rest":
        if cache_dir is None or catdv_client is None:
            raise ValueError("rest source requires catdv_client and cache_dir")
        return RestProxyResolver(
            catdv=catdv_client,
            cache_dir=cache_dir,
            proxy_cache_repo=proxy_cache_repo,
            db_provider=db_provider,
            archive=archive,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/integration/test_rest_proxy_resolver_image.py tests/integration/test_rest_proxy_resolver_records.py tests/unit/test_proxy_resolver_factory.py -v`
Expected: PASS — new image tests pass AND all four existing `test_rest_proxy_resolver_records.py` tests still pass (the recorded-row fast path preserves the download/record/backfill semantics for the `archive=None` video case).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/proxy_resolver.py tests/integration/test_rest_proxy_resolver_image.py
git commit -m "feat(resolver): fetch image originals as {id}.{ext}"
```

---

### Task 4: Pillow dependency + downscaled image poster

Add Pillow and extend `ThumbnailService` so an image with no CatDV poster/thumbnail gets a cached downscaled poster (`{clip_id}.jpg`, max 480px long edge) built from its original. Pillow runs off the event loop; decode failures degrade to the existing placeholder.

**Files:**
- Modify: `pyproject.toml` (add `pillow` to `[project] dependencies`)
- Modify: `backend/app/services/thumbnail_service.py`
- Test: `tests/unit/test_thumbnail_service_image.py`

- [ ] **Step 1: Add Pillow and install it**

In `pyproject.toml`, add to the `dependencies = [ ... ]` list (alongside `jinja2`, `ftfy`, etc.):

```toml
  "pillow>=10.4",
```

Then install into the project venv:

Run: `.venv/bin/pip install 'pillow>=10.4'`
Expected: `Successfully installed pillow-...`

- [ ] **Step 2: Write the failing test**

```python
# tests/unit/test_thumbnail_service_image.py
import io
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from backend.app.services.thumbnail_service import ThumbnailService


class _FakeArchive:
    def __init__(self, provider_data: dict):
        self._pd = provider_data

    async def get_clip(self, clip_id: str):
        return SimpleNamespace(provider_data=self._pd)


def _make_jpeg_bytes(w: int, h: int) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (200, 30, 30)).save(buf, format="JPEG")
    return buf.getvalue()


class _OriginalCatdv:
    def __init__(self, blob: bytes):
        self._blob = blob
        self.original_calls: list[int] = []

    async def download_original(self, media_id: int, dest: Path) -> None:
        self.original_calls.append(media_id)
        Path(dest).write_bytes(self._blob)

    async def download_thumbnail(self, thumb_id, dest, **kw):  # unused here
        raise AssertionError("should not fetch a CatDV thumbnail for a still")


IMAGE_PD = {
    "posterID": None,
    "thumbnailIDs": [],
    "media": {"ID": 881519, "filePath": "/Volumes/ARECA/x/Anna 101.JPG"},
}


@pytest.mark.asyncio
async def test_builds_downscaled_poster_for_still(tmp_path: Path):
    catdv = _OriginalCatdv(_make_jpeg_bytes(1000, 800))
    svc = ThumbnailService(cache_dir=tmp_path, archive=_FakeArchive(IMAGE_PD), catdv=catdv)
    out = await svc.get_or_fetch(888745)
    assert out == tmp_path / "888745.jpg"
    assert catdv.original_calls == [881519]
    with Image.open(out) as im:
        assert max(im.size) <= 480
    # the temp original is cleaned up
    assert not (tmp_path / "888745.jpg.orig").exists()


@pytest.mark.asyncio
async def test_undecodable_original_returns_none(tmp_path: Path):
    catdv = _OriginalCatdv(b"this is not an image")
    svc = ThumbnailService(cache_dir=tmp_path, archive=_FakeArchive(IMAGE_PD), catdv=catdv)
    out = await svc.get_or_fetch(888745)
    assert out is None
    assert not (tmp_path / "888745.jpg").exists()


@pytest.mark.asyncio
async def test_non_image_with_no_poster_returns_none(tmp_path: Path):
    catdv = _OriginalCatdv(_make_jpeg_bytes(10, 10))
    pd = {"media": {"ID": 1, "filePath": "/Volumes/ARECA/x/clip.mov"}}
    svc = ThumbnailService(cache_dir=tmp_path, archive=_FakeArchive(pd), catdv=catdv)
    out = await svc.get_or_fetch(123)
    assert out is None
    assert catdv.original_calls == []
```

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/bin/pytest tests/unit/test_thumbnail_service_image.py -v`
Expected: FAIL — `get_or_fetch` returns `None` for the still (no image branch yet), so `test_builds_downscaled_poster_for_still` fails.

- [ ] **Step 4: Implement the image-poster branch**

In `backend/app/services/thumbnail_service.py`, add imports at the top:

```python
import asyncio
```

and

```python
from backend.app.media_kind import is_image_path
```

Add this module-level helper (after the `_log = logging.getLogger(__name__)` line):

```python
def _downscale_to_jpeg(src: Path, dst: Path, max_edge: int) -> None:
    """Open `src`, scale so its long edge ≤ max_edge, save JPEG to `dst`.
    Synchronous (Pillow); call via asyncio.to_thread."""
    from PIL import Image

    with Image.open(src) as im:
        im = im.convert("RGB")
        im.thumbnail((max_edge, max_edge))
        im.save(dst, format="JPEG", quality=85)
```

In `get_or_fetch`, replace the early return when there's no thumbnail id:

```python
        thumb_id = clip.provider_data.get("posterID")
        if not thumb_id:
            ids = clip.provider_data.get("thumbnailIDs") or []
            thumb_id = ids[0] if ids else None
        if not thumb_id:
            return await self._build_image_poster(clip, dest)
```

Add the builder method to the class:

```python
    async def _build_image_poster(self, clip, dest: Path) -> Path | None:
        """For a still with no CatDV poster: fetch the original and downscale
        it to a cached JPEG poster. Returns None (→ placeholder) for non-image
        clips or any decode failure."""
        media = clip.provider_data.get("media") or {}
        file_path = media.get("filePath")
        media_id = media.get("ID")
        if not is_image_path(file_path) or media_id is None:
            return None
        tmp = dest.with_suffix(dest.suffix + ".orig")
        try:
            await self._catdv.download_original(int(media_id), tmp)
            await asyncio.to_thread(_downscale_to_jpeg, tmp, dest, 480)
        except Exception as exc:  # noqa: BLE001 — transport / decode / unsupported
            _log.debug("thumb: image poster build failed for %s: %s", dest.stem, exc)
            dest.unlink(missing_ok=True)
            return None
        finally:
            tmp.unlink(missing_ok=True)
        if dest.exists() and dest.stat().st_size > 0:
            return dest
        return None
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/unit/test_thumbnail_service_image.py tests/unit/test_thumbnail_service.py -v`
Expected: PASS — new image tests pass AND the existing thumbnail tests still pass (`test_no_poster_returns_none` uses empty `provider_data`, so `media` is missing → `is_image_path(None)` is False → returns None).

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml backend/app/services/thumbnail_service.py tests/unit/test_thumbnail_service_image.py
git commit -m "feat(thumbnails): downscaled poster for stills via Pillow"
```

---

### Task 5: View-model `kind`

Expose `kind` ∈ {`"image"`, `"video"`} on the clip-detail view model so the template can choose the right viewer element.

**Files:**
- Modify: `backend/app/ui/view_models.py`
- Test: `tests/unit/test_view_models.py` (add cases)

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_view_models.py`:

```python
def test_clip_detail_kind_image():
    from datetime import UTC, datetime

    from backend.app.archive.model import CanonicalClip, MediaRef
    from backend.app.ui.view_models import clip_detail

    clip = CanonicalClip(
        key=("catdv", "888745"),
        name="Anna 101.JPG",
        duration_secs=0.0,
        fps=10.0,
        markers=(),
        fields={},
        notes={},
        media=MediaRef(mime_type="Unknown", size_bytes=None, cached_path=None, upstream_handle=""),
        provider_data={"media": {"ID": 881519, "filePath": "/x/Anna 101.JPG"}},
        fetched_at=datetime.now(UTC),
    )
    assert clip_detail(clip)["clip"]["kind"] == "image"


def test_clip_detail_kind_video():
    from datetime import UTC, datetime

    from backend.app.archive.model import CanonicalClip, MediaRef
    from backend.app.ui.view_models import clip_detail

    clip = CanonicalClip(
        key=("catdv", "888894"),
        name="Bogdan 1.mov",
        duration_secs=31.0,
        fps=25.0,
        markers=(),
        fields={},
        notes={},
        media=MediaRef(mime_type="video/quicktime", size_bytes=None, cached_path=None, upstream_handle=""),
        provider_data={"media": {"ID": 770000, "filePath": "/x/Bogdan 1.mov"}},
        fetched_at=datetime.now(UTC),
    )
    assert clip_detail(clip)["clip"]["kind"] == "video"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/unit/test_view_models.py -k kind -v`
Expected: FAIL with `KeyError: 'kind'`

- [ ] **Step 3: Implement**

In `backend/app/ui/view_models.py`, add the import:

```python
from backend.app.media_kind import is_image_path
```

Add a helper near `_format_summary`:

```python
def _media_kind(provider_data: dict[str, Any]) -> str:
    media = provider_data.get("media") or {}
    return "image" if is_image_path(media.get("filePath")) else "video"
```

In `clip_detail`, add `kind` to the returned `clip` dict (right after `"format": ...`):

```python
            "kind": _media_kind(clip.provider_data),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/unit/test_view_models.py -v`
Expected: PASS (new kind tests + existing view-model tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/ui/view_models.py tests/unit/test_view_models.py
git commit -m "feat(ui): expose clip kind on detail view model"
```

---

### Task 6: Detail template `<img>` branch + `.mov`-assumption audit + browser check

Render `<img>` for image clips, `<video>` for video. The `<img>` deliberately has **no** `x-ref="video"`, so the Alpine `player()` no-ops (every method guards `if (!v) return;`, and `init()` early-returns). The duration-gated transport/timeline/Live button already hide themselves for stills.

**Files:**
- Modify: `backend/app/templates/pages/clip_detail.html` (the `.viewer` block, ~lines 92–107)

- [ ] **Step 1: Audit `.mov` assumptions (verification, no code expected)**

Confirm the cache/eviction code reads the recorded `file_path` rather than reconstructing `{id}.mov`:

Run: `grep -rn '\.mov' backend/app/services/cache_inspector.py backend/app/services/lru_eviction.py backend/app/services/proxy_cache_reconciler.py`
Expected: **no matches.** (These read `proxy_cache.file_path`, so variable image extensions are already handled.) If any match appears, update that spot to use the stored path and note it in the commit.

Also confirm the GCS blob name (`gcs.py` `clips/{id}.mov`) is intentionally left as-is — it is cosmetic because Gemini consumes the passed `mime_type`, not the blob name (see spec §7). No change.

- [ ] **Step 2: Edit the template**

In `backend/app/templates/pages/clip_detail.html`, replace the single `<video ...></video>` element inside `<div class="viewer">` with:

```html
      {% if clip.kind == "image" %}
      <img class="video"
           src="{{ clip.media_url }}"
           alt="{{ clip.name }}"
           @dblclick="$el.requestFullscreen && $el.requestFullscreen()">
      {% else %}
      <video x-ref="video"
             class="video"
             src="{{ clip.media_url }}"
             preload="metadata"
             @dblclick="$refs.video.requestFullscreen && $refs.video.requestFullscreen()"></video>
      {% endif %}
```

- [ ] **Step 3: Verify the seat is free before launching the dev server**

Per `CLAUDE.md` seat discipline, check nothing already holds the single CatDV seat:

Run:
```bash
/usr/sbin/lsof -nP -iTCP:8765 -sTCP:LISTEN
/bin/ps -ef | /usr/bin/grep -E '(uvicorn|backend\.app)' | /usr/bin/grep -v grep
```
Expected: if a server is already listening on 8765, **reuse it** — do not start a second. Otherwise start one: `./run.sh` (or the project's documented launch command).

- [ ] **Step 4: Browser verification (golden path)**

Open an image clip detail page, e.g. `http://127.0.0.1:8765/clips/888745`. Confirm:
- the original photo renders in the viewer as an image (not a black/blank video element),
- no timeline/transport/Live controls appear (duration 0),
- the clips list at `/` shows a real (downscaled) thumbnail for image rows,
- a video clip (e.g. one of the `.mov` clips) still plays normally with its transport.

If you cannot run the UI in this environment, say so explicitly rather than claiming success.

- [ ] **Step 5: Shut the dev server down gracefully (if you started one)**

Run: `/bin/kill -TERM <pid>` and confirm `Application shutdown complete.` appears in the log (releases the CatDV seat). **Never** `kill -9`.

- [ ] **Step 6: Commit**

```bash
git add backend/app/templates/pages/clip_detail.html
git commit -m "feat(ui): render <img> viewer for image clips"
```

---

### Task 7: Full suite, lint, ADR

**Files:**
- Create: `docs/adr/0026-image-clip-support-via-original-media.md`
- Modify: `docs/decisions.md` (index row)

- [ ] **Step 1: Run the full test suite + pre-commit**

Run: `.venv/bin/pytest -q`
Expected: PASS (no regressions).
Run: `.venv/bin/pre-commit run --all-files` (runs ruff, basedpyright, import-linter)
Expected: PASS. In particular import-linter must stay green — `backend/app/media_kind.py` is a leaf module imported by services and ui, which the contracts allow.

- [ ] **Step 2: Write the ADR**

Create `docs/adr/0026-image-clip-support-via-original-media.md`:

```markdown
# 0026. Image (still) clip support via original-media fetch

**Date:** 2026-05-26
**Status:** Accepted

## Context

Still-image clips were inaccessible: blank viewer, failed "cache", no
annotation. Investigation showed these stills were imported with
`pragafilm.generuj.proxy = false`, so CatDV generated no proxy, poster, or
thumbnail. The app only ever used the clip-scoped proxy path
(`GET /clips/{id}/media`), which 404s for stills, and always rendered a
`<video>` element. The app runs on a separate host from CatDV, so the
originals on `/Volumes/ARECA/...` are not reachable via the filesystem.

## Alternatives

- **Full-width poster** — rejected: stills have no poster/thumbnail at all.
- **Filesystem read of the original** — rejected: the media volume is not
  (and will not be) mounted on the app host; deployment stays split-host.
- **Re-enable proxy/poster generation in CatDV** — rejected: CatDV-admin
  work, and transcoding a still into a video proxy is pointless.

## Decision

Fetch the original over REST: `GET /api/9/media/{mediaID}?type=orig`
(verified to return the original JPEG; `type=proxy` 404s for stills).
`mediaID` is `clip.provider_data.media.ID`. Classify a clip as an image by
its `media.filePath` extension (authoritative — `media.still` was observed
`false` on a real JPEG). The resolver caches the original as `{id}.{ext}`,
the thumbnail service downscales it to a cached poster with Pillow, and the
detail template renders `<img>`. The existing Gemini pipeline is unchanged:
it MIME-guesses from the cached path and already skips the timecode anchor
at `duration == 0`.

## Consequences

- New runtime dependency: Pillow (smallest self-contained option; rejected
  pyvips/Wand/ffmpeg, which need native libs/binaries).
- The GCS blob name stays `clips/{id}.mov` for images — cosmetic only, since
  Gemini consumes the passed `mime_type`, not the blob name.
- Formats Pillow can't decode natively (e.g. HEIC) degrade to the list
  placeholder; the detail `<img>` still points at the original bytes.
- Whole-image annotation only (no timecode markers); the prompt/target-map
  content for stills is configured separately by the operator.
```

- [ ] **Step 3: Update the decisions index**

In `docs/decisions.md`, append a row to the index table:

```markdown
| 0026 | 2026-05-26 | [Image (still) clip support via original-media fetch](./adr/0026-image-clip-support-via-original-media.md) |
```

- [ ] **Step 4: Commit**

```bash
git add docs/adr/0026-image-clip-support-via-original-media.md docs/decisions.md
git commit -m "docs(adr): 0026 image clip support via original-media fetch"
```

---

## Self-review notes

- **Spec coverage:** §kind→Task 1/5; §fetch original→Task 2; §resolver branch→Task 3; §serve (no route change)→verified in Task 3/6 (`guess_type` on `.jpg`); §detail viewer→Task 6; §list thumbnail→Task 4; §annotation unchanged→relies on Tasks 1–3 (no annotator edit, per spec §7); §`.mov` audit→Task 6 Step 1; §testing→each task's tests.
- **No annotator change** is intentional: once Task 3 makes the resolver return `{id}.jpg`, `annotator.py:139` guesses `image/jpeg` and `_render_prompt` already no-ops at `duration==0`.
- **Method/signature consistency:** `is_image_path` (Task 1) is the only classifier, reused verbatim in Tasks 3/4/5; `CatdvClient.download_original(media_id, dest)` (Task 2) is the exact signature called by the resolver (Task 3) and thumbnail service (Task 4); the fake's `download_original` test doubles match it.
```
