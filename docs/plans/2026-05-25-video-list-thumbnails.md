# Video Lists: Thumbnails + Unified List Component — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give both video lists (the clips list at `/` and the cache list at `/cache`) locally-cached CatDV poster thumbnails, render them through one shared list component, and fix the row-alignment bug.

**Architecture:** A new `ThumbnailService` resolves a clip's `posterID` (via the archive provider's cached metadata) and lazily fetches the poster JPEG from CatDV (`GET /catdv/api/9/thumbnail/{id}`), caching it as a plain file. A new `GET /api/media/{clip_id}/thumb` route serves it or returns 404. The templates collapse into one `_video_list.html` scaffold that owns the shared chrome (checkbox, cache badge, thumbnail+name cell) while each page injects its own trailing columns via small partials.

**Tech Stack:** FastAPI, httpx, Jinja2, HTMX, Alpine.js, pytest (asyncio + `TestClient`), an in-process `FakeCatdv` test server.

**Spec:** `docs/specs/2026-05-25-video-list-thumbnails-unification-design.md`

---

## File Structure

**Backend (create):**
- `backend/app/services/thumbnail_service.py` — resolve posterID + fetch/cache the poster file.

**Backend (modify):**
- `backend/app/services/catdv_client.py` — add `download_thumbnail(...)`.
- `backend/app/context.py` — wire `ThumbnailService` onto `AppContext`.
- `backend/app/routes/media.py` — add the `/{clip_id}/thumb` route.
- `backend/app/ui/view_models.py` — add `thumb_url`, `select_value`, `row_href` to `clip_summary`.
- `backend/app/routes/cache.py` — build cache rows in the shared shape; drop the exact-bytes subline.

**Templates (create):**
- `backend/app/templates/pages/_video_list.html` — shared scaffold.
- `backend/app/templates/pages/_clips_head_cells.html`, `_clips_row_cells.html`.
- `backend/app/templates/pages/_cache_head_cells.html`, `_cache_row_cells.html`.

**Templates (modify):**
- `backend/app/templates/pages/_clips_tbody.html` — use the scaffold.
- `backend/app/templates/pages/_cache_inventory_table.html` — use the scaffold.
- `backend/app/static/app.css` — add `.vlist` + `.thumb` + shimmer/placeholder; fix `.row` collision; remove `.tbl`/`.cache-tbl`/`.exact`.

**Tests (create):**
- `tests/integration/test_catdv_client_thumbnail.py`
- `tests/unit/test_thumbnail_service.py`

**Tests (modify):**
- `tests/fakes/fake_catdv.py` — add a `thumbnail/{id}` endpoint + `thumbnails` store.
- `tests/integration/test_routes_media.py` — `/thumb` route tests.
- `tests/unit/test_view_models.py` — assert `thumb_url`.
- `tests/integration/test_context.py` — assert `thumbnail_service` is wired.

**Docs:**
- `docs/adr/NNNN-video-list-thumbnails-and-shared-component.md` + `docs/decisions.md`.

---

## Task 1: CatDV client `download_thumbnail` + fake endpoint

**Files:**
- Modify: `tests/fakes/fake_catdv.py`
- Modify: `backend/app/services/catdv_client.py:151-171` (add method after `download_proxy`)
- Test: `tests/integration/test_catdv_client_thumbnail.py`

- [ ] **Step 1: Add a thumbnail store + endpoint to the fake CatDV server**

In `tests/fakes/fake_catdv.py`, add a store in `__init__` next to `self.proxies` (line 26):

```python
        self.proxies: dict[int, bytes] = {}
        self.thumbnails: dict[int, bytes] = {}
```

Then register a route inside `_register_routes` (add after the `get_media` route, before the closing of the method, ~line 138):

```python
        @self.app.get("/catdv/api/9/thumbnail/{thumb_id}")
        async def get_thumbnail(thumb_id: int, request: Request):
            if request.cookies.get("JSESSIONID") != "fake-session":
                # Mirror CatDV: HTTP 200 with a JSON AUTH envelope, not 401.
                return self._envelope("AUTH")
            blob = self.thumbnails.get(thumb_id)
            if blob is None:
                return Response(status_code=404)
            return Response(content=blob, media_type="image/jpeg")
```

- [ ] **Step 2: Write the failing test**

Create `tests/integration/test_catdv_client_thumbnail.py`:

```python
from pathlib import Path

import pytest

from backend.app.services.catdv_client import CatdvClient
from tests.fakes.fake_catdv import running_fake_catdv


@pytest.mark.asyncio
async def test_download_thumbnail_writes_jpeg(tmp_path: Path):
    blob = b"\xff\xd8\xff" + b"JPEGDATA" * 16  # fake JPEG bytes
    with running_fake_catdv() as (base_url, fake):
        fake.thumbnails[9000] = blob
        out = tmp_path / "42.jpg"
        client = CatdvClient(base_url=base_url, username="klientAI", password="secret")
        async with client:
            await client.login()
            await client.download_thumbnail(9000, out)
        assert out.read_bytes() == blob


@pytest.mark.asyncio
async def test_download_thumbnail_missing_raises(tmp_path: Path):
    with running_fake_catdv() as (base_url, fake):
        out = tmp_path / "42.jpg"
        client = CatdvClient(base_url=base_url, username="klientAI", password="secret")
        async with client:
            await client.login()
            with pytest.raises(Exception):
                await client.download_thumbnail(1234, out)
        assert not out.exists() or out.stat().st_size == 0
```

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/bin/pytest tests/integration/test_catdv_client_thumbnail.py -v`
Expected: FAIL with `AttributeError: 'CatdvClient' object has no attribute 'download_thumbnail'`.

- [ ] **Step 4: Implement `download_thumbnail`**

In `backend/app/services/catdv_client.py`, add this method immediately after `download_proxy` (after line 171):

```python
    async def download_thumbnail(
        self, thumb_id: int, dest: Path, *, width: int | None = None, fmt: str = "jpg"
    ) -> None:
        """Stream a thumbnail/poster image to `dest`.

        Hits the singular image renderer `GET /api/9/thumbnail/{id}` (the
        plural `/thumbnails/{id}` is the JSON metadata endpoint — do not use
        it). When the session is missing CatDV answers HTTP 200 with a JSON
        AUTH envelope instead of image bytes; `_is_auth_envelope` catches that
        via the content-type so we re-login rather than writing JSON into a
        .jpg.
        """
        if not self._logged_in:
            await self.login()
        url = f"{self._base}/catdv/api/9/thumbnail/{thumb_id}"
        params: dict[str, str] = {"fmt": fmt}
        if width:
            params["width"] = str(width)

        async with self.http.stream("GET", url, params=params) as resp:
            if resp.status_code == 401 or _is_auth_envelope(resp):
                await self.login()
                async with self.http.stream("GET", url, params=params) as resp2:
                    resp2.raise_for_status()
                    await self._stream_to_file(resp2, dest, append=False, chunk_size=_DEFAULT_CHUNK)
                    return
            resp.raise_for_status()
            await self._stream_to_file(resp, dest, append=False, chunk_size=_DEFAULT_CHUNK)
```

Add a module-level constant near the top of the file (after the imports, before the class) if one does not already exist:

```python
_DEFAULT_CHUNK = 1 << 16
```

(`_stream_to_file` and `_is_auth_envelope` already exist in this module.)

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/pytest tests/integration/test_catdv_client_thumbnail.py -v`
Expected: PASS (2 passed).

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/catdv_client.py tests/fakes/fake_catdv.py tests/integration/test_catdv_client_thumbnail.py
git commit -m "feat(catdv): download_thumbnail client method + fake endpoint"
```

---

## Task 2: ThumbnailService (resolve posterID + cache)

**Files:**
- Create: `backend/app/services/thumbnail_service.py`
- Test: `tests/unit/test_thumbnail_service.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_thumbnail_service.py`:

```python
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.app.services.thumbnail_service import ThumbnailService


class _FakeArchive:
    def __init__(self, provider_data: dict):
        self._provider_data = provider_data

    async def get_clip(self, clip: str):
        return SimpleNamespace(provider_data=self._provider_data)


class _FakeCatdv:
    def __init__(self):
        self.calls: list[int] = []

    async def download_thumbnail(self, thumb_id, dest, **kw):
        self.calls.append(thumb_id)
        Path(dest).write_bytes(b"\xff\xd8\xffJPEG")


@pytest.mark.asyncio
async def test_cache_hit_skips_fetch(tmp_path: Path):
    (tmp_path / "42.jpg").write_bytes(b"cached")
    catdv = _FakeCatdv()
    svc = ThumbnailService(cache_dir=tmp_path, archive=_FakeArchive({"posterID": 9000}), catdv=catdv)
    out = await svc.get_or_fetch(42)
    assert out == tmp_path / "42.jpg"
    assert catdv.calls == []  # no fetch on hit


@pytest.mark.asyncio
async def test_online_miss_fetches_posterid(tmp_path: Path):
    catdv = _FakeCatdv()
    svc = ThumbnailService(cache_dir=tmp_path, archive=_FakeArchive({"posterID": 9000}), catdv=catdv)
    out = await svc.get_or_fetch(42)
    assert out == tmp_path / "42.jpg"
    assert out.read_bytes() == b"\xff\xd8\xffJPEG"
    assert catdv.calls == [9000]


@pytest.mark.asyncio
async def test_falls_back_to_thumbnail_ids(tmp_path: Path):
    catdv = _FakeCatdv()
    svc = ThumbnailService(
        cache_dir=tmp_path, archive=_FakeArchive({"thumbnailIDs": [5001, 5002]}), catdv=catdv
    )
    await svc.get_or_fetch(42)
    assert catdv.calls == [5001]


@pytest.mark.asyncio
async def test_no_poster_returns_none(tmp_path: Path):
    catdv = _FakeCatdv()
    svc = ThumbnailService(cache_dir=tmp_path, archive=_FakeArchive({}), catdv=catdv)
    assert await svc.get_or_fetch(42) is None
    assert catdv.calls == []


@pytest.mark.asyncio
async def test_offline_no_client_returns_none(tmp_path: Path):
    svc = ThumbnailService(cache_dir=tmp_path, archive=_FakeArchive({"posterID": 9000}), catdv=None)
    assert await svc.get_or_fetch(42) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/unit/test_thumbnail_service.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'backend.app.services.thumbnail_service'`.

- [ ] **Step 3: Implement the service**

Create `backend/app/services/thumbnail_service.py`:

```python
"""ThumbnailService — resolve a clip's poster image and cache it on disk.

Mirrors the proxy-cache pattern: poster JPEGs live as plain files at
`cache_dir/{clip_id}.jpg`. The poster id comes from the clip's cached
metadata (`posterID`, falling back to the first `thumbnailIDs` entry).
When offline (`catdv is None`) only already-cached files are served.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.app.archive.provider import ArchiveProvider
    from backend.app.services.catdv_client import CatdvClient

_log = logging.getLogger(__name__)


class ThumbnailService:
    def __init__(
        self,
        *,
        cache_dir: Path,
        archive: "ArchiveProvider",
        catdv: "CatdvClient | None" = None,
    ) -> None:
        self._cache_dir = cache_dir
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._archive = archive
        self._catdv = catdv

    def path_for(self, clip_id: int) -> Path:
        return self._cache_dir / f"{clip_id}.jpg"

    async def get_or_fetch(self, clip_id: int) -> Path | None:
        dest = self.path_for(clip_id)
        if dest.exists() and dest.stat().st_size > 0:
            return dest
        if self._catdv is None:
            return None

        try:
            clip = await self._archive.get_clip(str(clip_id))
        except Exception as exc:  # noqa: BLE001 — offline / not-found / transport
            _log.debug("thumb: get_clip(%s) failed: %s", clip_id, exc)
            return None

        thumb_id = clip.provider_data.get("posterID")
        if not thumb_id:
            ids = clip.provider_data.get("thumbnailIDs") or []
            thumb_id = ids[0] if ids else None
        if not thumb_id:
            return None

        try:
            await self._catdv.download_thumbnail(int(thumb_id), dest)
        except Exception as exc:  # noqa: BLE001 — transport / auth / 404
            _log.debug("thumb: download(%s) failed: %s", clip_id, exc)
            if dest.exists() and dest.stat().st_size == 0:
                dest.unlink(missing_ok=True)
            return None

        if dest.exists() and dest.stat().st_size > 0:
            return dest
        return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/unit/test_thumbnail_service.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/thumbnail_service.py tests/unit/test_thumbnail_service.py
git commit -m "feat(thumbnails): ThumbnailService resolves posterID and caches poster file"
```

---

## Task 3: Wire ThumbnailService into AppContext

**Files:**
- Modify: `backend/app/context.py:78-92` (add field), `:284-333` (build in archive subsystem)
- Test: `tests/integration/test_context.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/integration/test_context.py` (match the existing import + builder style in that file; it already builds an `AppContext` with `init_external=False` or via env — reuse whichever helper the file already defines). Add:

```python
@pytest.mark.asyncio
async def test_thumbnail_service_wired_when_catdv(monkeypatch, tmp_path):
    # Uses the same settings/build helper the rest of this file uses.
    ctx = await _build_ctx(monkeypatch, tmp_path)  # existing helper in this file
    try:
        assert ctx.thumbnail_service is not None
        assert ctx.thumbnail_service.path_for(42).name == "42.jpg"
    finally:
        await ctx.aclose()
```

> If `test_context.py` has no reusable `_build_ctx` helper, instead add the assertion inside the file's existing context-build test (search for `AppContext.build`) right after the context is built: `assert ctx.thumbnail_service is not None`.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/integration/test_context.py -k thumbnail -v`
Expected: FAIL with `AttributeError: 'AppContext' object has no attribute 'thumbnail_service'`.

- [ ] **Step 3: Add the field to AppContext**

In `backend/app/context.py`, add the import under the `TYPE_CHECKING` block (after line 17):

```python
    from backend.app.services.thumbnail_service import ThumbnailService
```

Add the dataclass field next to `proxy_resolver` (after line 82):

```python
    proxy_resolver: ProxyResolver | None = None
    thumbnail_service: ThumbnailService | None = None
```

- [ ] **Step 4: Build it in `_build_archive_subsystem`**

In `backend/app/context.py`, add the lazy import alongside the others near line 218:

```python
    from backend.app.services.proxy_resolver import build_resolver
    from backend.app.services.thumbnail_service import ThumbnailService
```

At the end of `_build_archive_subsystem`, immediately before `return _OnlineFlags(...)` (line 334), add:

```python
    # Thumbnail cache: plain JPEG files alongside the proxy cache. Pass the
    # CatDV client only when we actually have one (online or seat-recoverable);
    # in cache-only / fs modes the service still serves already-cached files.
    if use_catdv:
        ctx.thumbnail_service = ThumbnailService(
            cache_dir=settings.data_dir / "cache" / "thumbs",
            archive=ctx.archive,
            catdv=ctx.catdv,
        )
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/pytest tests/integration/test_context.py -k thumbnail -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/context.py tests/integration/test_context.py
git commit -m "feat(thumbnails): wire ThumbnailService into AppContext"
```

---

## Task 4: `/api/media/{clip_id}/thumb` route

**Files:**
- Modify: `backend/app/routes/media.py`
- Test: `tests/integration/test_routes_media.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/integration/test_routes_media.py`:

```python
def test_thumb_serves_jpeg(monkeypatch, tmp_path):
    app = _app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        ctx = client.app.state.ctx
        thumb = tmp_path / "42.jpg"
        thumb.write_bytes(b"\xff\xd8\xffJPEG")

        async def get_or_fetch(clip_id):
            assert clip_id == 42
            return thumb

        ctx.thumbnail_service = MagicMock(get_or_fetch=get_or_fetch)

        r = client.get("/api/media/42/thumb")
        assert r.status_code == 200
        assert r.headers["content-type"] == "image/jpeg"
        assert r.content == b"\xff\xd8\xffJPEG"


def test_thumb_404_when_unavailable(monkeypatch, tmp_path):
    app = _app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        ctx = client.app.state.ctx

        async def get_or_fetch(clip_id):
            return None

        ctx.thumbnail_service = MagicMock(get_or_fetch=get_or_fetch)

        r = client.get("/api/media/42/thumb")
        assert r.status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/integration/test_routes_media.py -k thumb -v`
Expected: FAIL with 404 for the first test (route not defined) — actually the route resolves to `stream_media` and 404s differently; the assertion `content-type == image/jpeg` will fail. Either way: FAIL.

- [ ] **Step 3: Add the route**

In `backend/app/routes/media.py`, add this route **above** `stream_media` (so the more specific path is registered first), after the `router` definition (line 12):

```python
from fastapi.responses import FileResponse, StreamingResponse  # already imported


@router.get("/{clip_id}/thumb")
async def stream_thumbnail(request: Request, clip_id: int):
    ctx = get_ctx(request)
    svc = getattr(ctx, "thumbnail_service", None)
    if svc is None:
        raise HTTPException(404, "thumbnails unavailable")
    path = await svc.get_or_fetch(clip_id)
    if path is None:
        raise HTTPException(404, "no thumbnail")
    return FileResponse(
        path,
        media_type="image/jpeg",
        headers={"Cache-Control": "public, max-age=86400"},
    )
```

(`FileResponse`, `Request`, `HTTPException`, and `get_ctx` are already imported at the top of the file.)

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/integration/test_routes_media.py -k thumb -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Regression — full media route file**

Run: `.venv/bin/pytest tests/integration/test_routes_media.py -v`
Expected: PASS (all, including the pre-existing stream tests — confirms route ordering didn't break `/{clip_id}`).

- [ ] **Step 6: Commit**

```bash
git add backend/app/routes/media.py tests/integration/test_routes_media.py
git commit -m "feat(thumbnails): GET /api/media/{clip_id}/thumb route"
```

- [ ] **Step 7 (manual, optional — live confirmation):**

This step needs the running dev server (which already holds a CatDV seat — do NOT start a second). With the server up on `:8765`, open the clips list in a browser and confirm real posters load. If posters are blank, try `thumbnailIDs[0]` instead of `posterID` by checking one clip's JSON: `curl -s http://localhost:8765/...` is not available for raw clips, so instead inspect the server log / add a temporary debug print in `ThumbnailService`. Resolution: whichever id yields image bytes is correct; the service already falls back from `posterID` to `thumbnailIDs[0]`. No code change expected.

---

## Task 5: `clip_summary` gains `thumb_url`, `select_value`, `row_href`

**Files:**
- Modify: `backend/app/ui/view_models.py:57-70`
- Test: `tests/unit/test_view_models.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_view_models.py` (it already imports `clip_summary` and builds a `CanonicalClip`; reuse that fixture/helper). Add:

```python
def test_clip_summary_has_thumb_and_nav_fields():
    clip = _canonical_clip(clip_id=42)  # existing helper in this file
    row = clip_summary(clip)
    assert row["thumb_url"] == "/api/media/42/thumb"
    assert row["select_value"] == "catdv/42"
    assert row["row_href"] == "/clips/42"
```

> If the file's helper is named differently, use the existing one. The clip key must be `("catdv", "42")` so the ids line up.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/unit/test_view_models.py -k thumb_and_nav -v`
Expected: FAIL with `KeyError: 'thumb_url'`.

- [ ] **Step 3: Add the keys**

In `backend/app/ui/view_models.py`, extend the dict returned by `clip_summary` (lines 62-70):

```python
    clip_id = int(clip.key[1])
    return {
        "id": clip_id,
        "name": _fix(clip.name),
        "duration_secs": clip.duration_secs,
        "year": _first_value(clip.fields.get(_YEAR_FIELD)),
        "decade": _first_value(clip.fields.get(_DECADE_FIELD)),
        "marker_count": len(clip.markers),
        "cache": cache_status_view(cache_status) if cache_status else None,
        "thumb_url": f"/api/media/{clip_id}/thumb",
        "select_value": f"{clip.key[0]}/{clip_id}",
        "row_href": f"/clips/{clip_id}",
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/unit/test_view_models.py -v`
Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add backend/app/ui/view_models.py tests/unit/test_view_models.py
git commit -m "feat(thumbnails): clip_summary exposes thumb_url + shared list keys"
```

---

## Task 6: Shared `_video_list.html` scaffold + `.vlist` CSS

This task introduces the shared component and its styles. Nothing consumes it yet, so verification is a syntax/render smoke check in Task 7/8. We still add the CSS now and fix the `.row` collision.

**Files:**
- Create: `backend/app/templates/pages/_video_list.html`
- Modify: `backend/app/static/app.css` (add `.vlist`, `.thumb`, shimmer, placeholder; fix `.row`)

- [ ] **Step 1: Create the scaffold partial**

Create `backend/app/templates/pages/_video_list.html`:

```html
{# Shared "list of videos" scaffold. Owns the chrome both lists must share:
   select-all + per-row checkbox, the cache-layer badge, and the
   thumbnail+name cell. Each page injects its own trailing columns.

   Params:
     rows         iterable of row view-models. Per-row keys consumed here:
                    select_value  (str)  checkbox value, e.g. "catdv/42"
                    cache         (dict) cache_status_view shape, or None
                    thumb_url     (str)  e.g. "/api/media/42/thumb"
                    name          (str)
                    name_sub      (str|None) optional muted subline
                    row_href      (str|None) clickable-row target
                    row_class     (str|None) extra <tr> class (e.g. "orphan")
                    row_bytes     (int|None) -> data-bytes on the checkbox
                  plus any keys the page's row_cells partial reads.
     head_cells   template path for the trailing <th>s
     row_cells    template path for the trailing <td>s (gets `row` in context)
     cache_label  badge-column header label (default "Cache")
     colspan      total column count for the empty-state row
     empty_msg    empty-state text (default "No clips match.")
#}
<table class="vlist">
  <thead>
    <tr>
      <th class="col-sel"><input type="checkbox" id="row-select-all" aria-label="Select all"></th>
      <th class="col-cache" title="metadata · media-local · media-ai">{{ cache_label | default("Cache") }}</th>
      <th class="col-name">Clip</th>
      {% include head_cells %}
    </tr>
  </thead>
  <tbody>
    {% for row in rows %}
    <tr class="vrow{% if row.row_class %} {{ row.row_class }}{% endif %}"
        {% if row.row_href %}onclick="location.href='{{ row.row_href }}'"{% endif %}>
      <td class="col-sel" onclick="event.stopPropagation()">
        <input type="checkbox" class="row-check" name="clip_keys"
               value="{{ row.select_value }}"
               {% if row.row_bytes is not none %}data-bytes="{{ row.row_bytes }}"{% endif %}
               aria-label="Select {{ row.name }}">
      </td>
      <td class="cell-cache" onclick="event.stopPropagation()">
        {% with cache = row.cache %}{% include "pages/_cache_badge.html" %}{% endwith %}
      </td>
      <td class="cell-name">
        <span class="namecell">
          <img class="thumb" loading="lazy" src="{{ row.thumb_url }}" alt=""
               onerror="this.classList.add('thumb--empty'); this.removeAttribute('src');">
          <span class="name-wrap">
            <span class="name">{{ row.name }}</span>
            {% if row.name_sub %}<span class="name-sub mono">{{ row.name_sub }}</span>{% endif %}
          </span>
        </span>
      </td>
      {% include row_cells %}
    </tr>
    {% else %}
    <tr><td colspan="{{ colspan | default(7) }}" class="empty">{{ empty_msg | default("No clips match.") }}</td></tr>
    {% endfor %}
  </tbody>
</table>
```

- [ ] **Step 2: Add `.vlist` styles and fix the `.row` collision**

In `backend/app/static/app.css`, add a new block (place it right after the existing `.tbl` block, after line 369):

```css
/* ─── shared video list (.vlist) ─────────────────────────────────────── */
.vlist { width: 100%; border-collapse: collapse; }
.vlist thead th {
  position: sticky; top: 0;
  background: var(--panel);
  text-align: left;
  font-size: 10.5px; letter-spacing: 0.06em; text-transform: uppercase;
  color: var(--text-3); font-weight: 600;
  padding: 8px 12px;
  border-bottom: 1px solid var(--line);
  white-space: nowrap;
}
.vlist thead th.num { text-align: right; }
.vlist tbody td {
  padding: 0 12px; height: 48px;
  border-bottom: 1px solid var(--line);
  vertical-align: middle;
  white-space: nowrap;
  font-size: 13px;
}
.vlist tbody td.num { text-align: right; }
.vlist tbody tr.vrow { cursor: default; }
.vlist tbody tr.vrow[onclick] { cursor: pointer; }
.vlist tbody tr.vrow:hover { background: var(--hover); }
.vlist .col-year, .vlist .col-decade, .vlist .col-dur, .vlist .col-mk { width: 90px; }
.vlist .namecell { display: flex; align-items: center; gap: 10px; min-width: 0; }
.vlist .name-wrap { display: flex; flex-direction: column; min-width: 0; }
.vlist .name { overflow: hidden; text-overflow: ellipsis; }
.vlist .name-sub { font-size: 11px; color: var(--text-3); margin-top: 2px; }
.vlist .empty { text-align: center; color: var(--text-3); padding: 32px 0; }

.vlist .thumb {
  width: 64px; height: 36px; flex: none; border-radius: 4px;
  object-fit: cover; background: #211e19;
}
/* loading shimmer while the <img> has a src but no pixels yet */
.vlist .thumb:not(.thumb--empty) {
  background: linear-gradient(90deg, #221f1a 0%, #2c2820 50%, #221f1a 100%);
  background-size: 200% 100%;
  animation: thumb-shimmer 1.2s ease-in-out infinite;
}
@keyframes thumb-shimmer { from { background-position: 200% 0; } to { background-position: -200% 0; } }
/* loaded image overrides the shimmer background with its own pixels;
   onerror adds .thumb--empty to show the quiet placeholder gradient */
.vlist .thumb[src] { animation: none; background: transparent; }
.vlist .thumb--empty {
  animation: none;
  background: linear-gradient(135deg, #2a221a, #4a3826 60%, #6b4d2e);
}
```

Now fix the `.row { display:flex }` collision at line 1006. Change the global helper so it cannot match a table row. Replace:

```css
.row { display: flex; align-items: center; gap: 8px; }
```

with:

```css
/* flex helper for non-table layouts; never targets table rows (see .vlist) */
.row:not(tr) { display: flex; align-items: center; gap: 8px; }
```

> This is the alignment fix: the clips table will no longer be turned into a flex container. (After Task 7 the clips rows use `.vrow`, but scoping the helper is the defensive fix and protects any other `class="row"` table usage.)

- [ ] **Step 3: Syntax check — templates compile**

Run: `.venv/bin/python -c "from jinja2 import Environment, FileSystemLoader; e=Environment(loader=FileSystemLoader('backend/app/templates')); e.get_template('pages/_video_list.html')"`
Expected: no output (compiles cleanly). If it raises a `TemplateSyntaxError`, fix the markup.

- [ ] **Step 4: Commit**

```bash
git add backend/app/templates/pages/_video_list.html backend/app/static/app.css
git commit -m "feat(ui): shared _video_list scaffold + .vlist styles; scope .row helper"
```

---

## Task 7: Migrate the clips list to the shared scaffold

**Files:**
- Create: `backend/app/templates/pages/_clips_head_cells.html`, `backend/app/templates/pages/_clips_row_cells.html`
- Modify: `backend/app/templates/pages/_clips_tbody.html`
- Test: `tests/integration/test_routes_pages.py`

- [ ] **Step 1: Create the clips trailing-cell partials**

Create `backend/app/templates/pages/_clips_head_cells.html`:

```html
<th class="col-year">Year</th>
<th class="col-decade">Decade</th>
<th class="col-dur">Duration</th>
<th class="col-mk">Markers</th>
```

Create `backend/app/templates/pages/_clips_row_cells.html`:

```html
<td class="mono col-year">{{ row.year or "—" }}</td>
<td class="col-decade">{{ row.decade or "—" }}</td>
<td class="mono col-dur">{{ "%d:%02d"|format((row.duration_secs|int)//60, (row.duration_secs|int)%60) }}</td>
<td class="mono col-mk">{{ row.marker_count }}</td>
```

- [ ] **Step 2: Rewrite `_clips_tbody.html` to use the scaffold**

Replace the entire `<table>...</table>` block in `backend/app/templates/pages/_clips_tbody.html` (lines 3-43) with a single include, keeping the surrounding `#clips-region` wrapper and the pager untouched. The file becomes:

```html
<div id="clips-region" class="clips-region">
  <div class="tbl-scroll">
    {% with rows = clips,
            head_cells = "pages/_clips_head_cells.html",
            row_cells = "pages/_clips_row_cells.html",
            cache_label = "Cache",
            colspan = 7,
            empty_msg = "No clips match." %}
      {% include "pages/_video_list.html" %}
    {% endwith %}
  </div>
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

> The clip rows already carry `select_value`, `cache`, `thumb_url`, `name`, `row_href` from Task 5's `clip_summary`. `row_class`, `name_sub`, and `row_bytes` are absent → the scaffold's `{% if %}` guards treat them as falsy/`none`, which is correct (clips rows aren't orphan-marked and carry no byte total). The pre-existing `bulkSel()` JS in `clips.html` selects `.row-check` (unchanged) and `#row-select-all` (rendered by the scaffold), so bulk select keeps working.

- [ ] **Step 3: Run the clips page regression test**

Run: `.venv/bin/pytest tests/integration/test_routes_pages.py -v`
Expected: PASS. (These tests stub the archive provider and assert the clips list renders 200 with clip names.)

- [ ] **Step 4: Add a thumbnail-presence assertion**

In `tests/integration/test_routes_pages.py`, find the test that fetches the clips list (search for `client.get("/")` or the list route) and add, after its existing `status_code == 200` assertion:

```python
        assert 'class="vlist"' in r.text
        assert '/api/media/12041/thumb' in r.text  # thumbnail img wired (clip id from _canonical)
```

> Use the clip id produced by that test's `_canonical(...)` helper (default `12041`). If the test uses a different id, match it.

- [ ] **Step 5: Run again to confirm**

Run: `.venv/bin/pytest tests/integration/test_routes_pages.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/templates/pages/_clips_head_cells.html backend/app/templates/pages/_clips_row_cells.html backend/app/templates/pages/_clips_tbody.html tests/integration/test_routes_pages.py
git commit -m "feat(ui): clips list renders via shared _video_list scaffold"
```

---

## Task 8: Migrate the cache inventory to the shared scaffold

**Files:**
- Modify: `backend/app/routes/cache.py` (add a `_cache_row` builder; use it for inventory rows)
- Create: `backend/app/templates/pages/_cache_head_cells.html`, `backend/app/templates/pages/_cache_row_cells.html`
- Modify: `backend/app/templates/pages/_cache_inventory_table.html`
- Test: `tests/integration/test_routes_cache.py`

- [ ] **Step 1: Add a shared-shape row builder in `cache.py`**

In `backend/app/routes/cache.py`, add this near `_status_for_template` (import `cache_status_view` at the top of the file: `from backend.app.ui.view_models import cache_status_view`):

```python
def _cache_row(status) -> dict:
    """Build a row view-model in the shared _video_list shape, plus the
    cache-specific columns the cache row_cells partial reads."""
    pid, cid = status.clip_key
    md, local, ai = status.layers
    is_orphan = not md.present
    local_bytes = int(local.size_bytes or 0)
    ai_bytes = int(ai.size_bytes or 0)
    return {
        "select_value": f"{pid}/{cid}",
        "cache": cache_status_view(status),
        "thumb_url": f"/api/media/{cid}/thumb",
        "name": status.name,
        "name_sub": f"{pid}/{cid}",
        "row_href": None,
        "row_class": "orphan" if is_orphan else None,
        "row_bytes": local_bytes + ai_bytes,
        # cache-specific (read by _cache_row_cells.html)
        "clip_pid": pid,
        "clip_cid": cid,
        "workspace": ", ".join(md.pinned_by_workspaces) if md.pinned_by_workspaces else "—",
        "local_bytes": local_bytes,
        "ai_bytes": ai_bytes,
    }
```

> `cache_status_view(status)` expects the live `CacheStatus` object (with `.layers`, `.clip_key`) — that is exactly what `statuses` holds before `_status_for_template`. Confirm `status.name` exists on the inspector status (it is used today in `_cache_inventory_table.html` as `status.name`). If `pinned_by_workspaces` holds ints, `", ".join(...)` needs `map(str, ...)`; the current template does `| join(", ")` on them, so reuse the same coercion the template relied on — wrap with `str`: `", ".join(str(w) for w in md.pinned_by_workspaces)`.

Then change the inventory row construction (line 225) from:

```python
        rows_for_template = [_status_for_template(s) for s in rows]
```

to:

```python
        rows_for_template = [_cache_row(s) for s in rows]
```

- [ ] **Step 2: Create the cache trailing-cell partials**

Create `backend/app/templates/pages/_cache_head_cells.html`:

```html
<th class="col-ws">Workspace</th>
<th class="num col-local">Local</th>
<th class="num col-ai">AI</th>
<th class="col-actions"></th>
```

Create `backend/app/templates/pages/_cache_row_cells.html` (single-line byte cells — the exact `comma B` subline is intentionally dropped):

```html
<td class="mono muted-2 col-ws">{{ row.workspace }}</td>
<td class="num mono col-local">
  {% if row.local_bytes %}{{ row.local_bytes | bytes_human }}{% else %}<span class="muted-2">—</span>{% endif %}
</td>
<td class="num mono col-ai">
  {% if row.ai_bytes %}{{ row.ai_bytes | bytes_human }}{% else %}<span class="muted-2">—</span>{% endif %}
</td>
<td class="row-actions col-actions" onclick="event.stopPropagation()">
  <button type="button" class="ra-btn"
          @click="bulkPrefetch([['{{ row.clip_pid }}','{{ row.clip_cid }}']])">Re-fetch</button>
  <button type="button" class="ra-btn ra-btn-danger"
          @click="bulkEvict([['{{ row.clip_pid }}','{{ row.clip_cid }}']])">Purge</button>
</td>
```

- [ ] **Step 3: Rewrite `_cache_inventory_table.html` to use the scaffold**

Replace the whole file `backend/app/templates/pages/_cache_inventory_table.html` with:

```html
{# Inventory table partial (All / Local / AI tabs). Renders the shared
   _video_list scaffold; the page template sets innerHTML on
   #cache-table-region, so this partial provides just the table. #}
{% if rows %}
{% with head_cells = "pages/_cache_head_cells.html",
        row_cells = "pages/_cache_row_cells.html",
        cache_label = "Cache",
        colspan = 7,
        empty_msg = "No entries match the current filter." %}
  {% include "pages/_video_list.html" %}
{% endwith %}
{% else %}
<p class="empty">No entries match the current filter.</p>
{% endif %}
```

- [ ] **Step 4: Carry over the orphan + action-button styling onto `.vlist`**

In `backend/app/static/app.css`, add to the `.vlist` block (after the `.vlist .empty` rule from Task 6):

```css
.vlist tr.vrow.orphan { background: color-mix(in srgb, var(--danger) 8%, transparent); }
.vlist .col-actions { width: auto; text-align: right; white-space: nowrap; }
.vlist .row-actions { opacity: 0; transition: opacity 80ms; }
.vlist tr.vrow:hover .row-actions,
.vlist tr.vrow:focus-within .row-actions { opacity: 1; }
.vlist .col-ws { max-width: 140px; overflow: hidden; text-overflow: ellipsis; }
```

> If `--danger` / `color-mix` aren't used elsewhere in this stylesheet, replace the orphan rule with the literal the old `.cache-tbl tr.cache-row.orphan` used — grep `app.css` for `cache-row.orphan` (around line 935) and reuse that exact `background:` value.

- [ ] **Step 5: Run the cache route regression tests**

Run: `.venv/bin/pytest tests/integration/test_routes_cache.py -v`
Expected: PASS. (They seed `clip_cache` rows and assert `/cache` + the HTMX inventory partial render 200.)

- [ ] **Step 6: Add a shared-scaffold assertion to the cache test**

In `tests/integration/test_routes_cache.py`, find the test that asserts the cache page / inventory renders (search for `client.get("/cache")`) and add after its `status_code == 200`:

```python
        assert 'class="vlist"' in r.text          # cache list uses the shared scaffold
        assert "/thumb" in r.text                  # thumbnail wired on cache rows
```

- [ ] **Step 7: Run again to confirm**

Run: `.venv/bin/pytest tests/integration/test_routes_cache.py -v`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add backend/app/routes/cache.py backend/app/templates/pages/_cache_head_cells.html backend/app/templates/pages/_cache_row_cells.html backend/app/templates/pages/_cache_inventory_table.html backend/app/static/app.css tests/integration/test_routes_cache.py
git commit -m "feat(ui): cache list renders via shared _video_list scaffold; drop exact-bytes subline"
```

---

## Task 9: Remove dead CSS + full-suite regression

**Files:**
- Modify: `backend/app/static/app.css`

- [ ] **Step 1: Confirm `.tbl` / `.cache-tbl` are unreferenced by templates**

Run: `grep -rn "class=\"tbl\b\|class=\"cache-tbl\|class=\"row\b\|cache-row\|clip-name-cell\|clip-name-text\|orphan-mark\|exact" backend/app/templates/`
Expected: no remaining references to `tbl`, `cache-tbl`, `cache-row`, `clip-name-cell`, `clip-name-text`, `orphan-mark`, or `exact` (the `tbl-scroll` wrapper class may remain — that's fine, it's a scroll container, not the old `.tbl` table).

> If any reference remains, it means a template still uses the old markup — go back and migrate it before deleting CSS.

- [ ] **Step 2: Delete the now-dead rules**

In `backend/app/static/app.css`, delete these blocks (verify by the selectors, not line numbers, since earlier tasks shifted lines):
- The old `.tbl` table rules: `.tbl { ... }`, `.tbl thead th { ... }`, `.tbl tbody td { ... }`, `.tbl tbody tr.row { ... }`, `.tbl tbody tr.row:hover { ... }`, `.tbl .col-year ...`, `.tbl .clip-name { ... }`, `.tbl .thumb { ... }`, `.tbl .empty { ... }`.
- The old `.cache-tbl` rules and `.exact` / `.clip-name-cell` / `.clip-name-text` / `.orphan-mark` / `.cache-row` rules.

Keep: `.tbl-scroll`, `.col-sel`/`.row-select`, `.col-cache`/`.cell-cache`, `.row-check`, `.lyr-*` legend dots (still used by the cache filter legend), `.pager`, and everything under `.vlist`.

- [ ] **Step 3: Full-suite regression**

Run: `.venv/bin/pytest -q`
Expected: PASS (whole suite green). If a test referenced an old class string, update the assertion to `vlist`.

- [ ] **Step 4: Lint**

Run: `.venv/bin/ruff check backend/app tests`
Expected: no errors. Fix any unused-import warnings (e.g. if `_status_for_template` or `_DictWrap` became unused after Task 8 — if so, delete them).

- [ ] **Step 5: Commit**

```bash
git add backend/app/static/app.css
git commit -m "refactor(ui): remove dead .tbl/.cache-tbl/.exact styles after scaffold migration"
```

---

## Task 10: ADR + decisions index

**Files:**
- Create: `docs/adr/NNNN-video-list-thumbnails-and-shared-component.md` (next number after the highest existing ADR)
- Modify: `docs/decisions.md`

- [ ] **Step 1: Find the next ADR number**

Run: `ls docs/adr | sort | tail -3`
Note the highest `NNNN`; the new file is `NNNN+1`.

- [ ] **Step 2: Write the ADR**

Create `docs/adr/NNNN-video-list-thumbnails-and-shared-component.md` (MADR-lite, matching existing ADRs):

```markdown
# NNNN. Unified video-list component + CatDV poster thumbnails

**Date:** 2026-05-25
**Status:** Accepted

## Context

The clips list (`/`) and the cache list (`/cache`) both show "a list of
videos" but were built as two independent tables (`.tbl` vs `.cache-tbl`),
so they looked unrelated and drifted. Neither showed thumbnails, and the
clips table's columns were misaligned because it rendered `<tr class="row">`
while a global `.row { display:flex }` helper turned each row into a flex
container.

## Alternatives

- **Thumbnails from ffmpeg frames of the cached proxy** — works offline but
  only for clips with a cached proxy (most rows would be placeholders).
- **Fully data-driven column spec for the shared list** — a flat
  `columns=[{value}]` config can't express the cache rows' layer-dot badge,
  hover action buttons, or per-row attributes without passing raw HTML
  through config, which defeats the uniformity it promises.
- **Shared CSS only, two table templates** — lowest risk but not "one
  component"; the two skeletons can drift again.

## Decision

- Thumbnails come from CatDV posters via `GET /catdv/api/9/thumbnail/{id}`
  (singular image renderer; the plural path is JSON metadata), cached as
  plain `cache/thumbs/{clip_id}.jpg` files and served by
  `GET /api/media/{clip_id}/thumb` with a graceful 404→placeholder fallback.
- Both lists render through one `pages/_video_list.html` scaffold that owns
  the shared chrome; each page injects only its trailing columns via small
  `head_cells` / `row_cells` partials.
- The cache list's exact-bytes (`comma B`) subline is dropped.
- The `.row` flex helper is scoped to `:not(tr)` to fix the alignment bug.

## Consequences

- One source of truth for list chrome; thumbnails are tiny, regenerable
  sidecar files with no DB table or eviction UI (out of scope).
- First cold view of a list fetches posters per-cell (small, lazy-loaded);
  subsequent views are served from the local cache.
```

- [ ] **Step 3: Update the decisions index**

In `docs/decisions.md`, add a row to the index table matching the existing format, pointing at the new ADR.

- [ ] **Step 4: Commit**

```bash
git add docs/adr docs/decisions.md
git commit -m "docs(adr): unified video-list component + CatDV poster thumbnails"
```

---

## Self-Review Notes (for the implementer)

- **Spec coverage:** thumbnails (Tasks 1-5) · shared component (Tasks 6-8) · alignment bug (Task 6 Step 2) · drop exact-bytes subline (Task 8) · ADR (Task 10). All spec sections map to a task.
- **Endpoint:** singular `/catdv/api/9/thumbnail/{id}` is used everywhere (client + fake + ADR). Do not use the plural path.
- **Type consistency:** `get_or_fetch(clip_id) -> Path | None` and `path_for(clip_id) -> Path` are used identically in the service, route, and tests. Row keys (`select_value`, `cache`, `thumb_url`, `name`, `name_sub`, `row_href`, `row_class`, `row_bytes`) are produced by both `clip_summary` (Task 5) and `_cache_row` (Task 8) and consumed by `_video_list.html` (Task 6).
- **CatDV seat discipline:** no task starts a server or POSTs `/session`; the one optional live check (Task 4 Step 7) reuses the already-running dev server. Automated tests use the in-process `FakeCatdv`.
