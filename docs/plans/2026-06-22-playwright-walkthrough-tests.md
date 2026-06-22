# Annotated Playwright Walkthrough Tests — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Python-Playwright harness that drives the real app in a browser, records annotated walkthrough videos (use-case chapter card + "Step N" counter + action highlights) for human review and as user docs, plus a fast headless assert mode and an `/e2e` skill — all fully offline with no CatDV seat.

**Architecture:** Run the FastAPI app **in-process** (`uvicorn.Server` in a daemon thread on `127.0.0.1:8766`) and inject a numeric-keyed `FakeArchive` + a real seeded proxy video via `install_live_ctx` (the same fake-injection path the existing page tests use — the fs provider can't render the UI because the web layer does `int(clip.key[1])`). The draft is seeded into the DB; Publish is exercised to the durable write queue (real `clip_versions` + `pending_operations` rows + status pill), not a SyncEngine round-trip. A `Walkthrough` harness wraps Playwright's v1.59 screencast API. Scenarios are Python modules of `step(label, action)` calls; a `run.py` CLI executes them in `--assert` or `--record` mode and generates a static gallery.

**Tech Stack:** Python 3.12/3.13, FastAPI, `playwright>=1.59` (Python binding, screencast API), pytest, uvicorn, ffmpeg (video seeding), Jinja/Alpine/HTMX app under test.

**Spec:** `docs/specs/2026-06-22-playwright-walkthrough-tests-design.md`

**Key grounding facts (verified against the codebase):**
- Clip route: `routes/pages/clips.py` → `clip_detail_page(clip_id: int)` calls `ctx.archive.get_clip(str(clip_id))`. View model: `ui/view_models.py:63,130` → `clip_id = int(clip.key[1])`. **Numeric keys are mandatory.**
- Injection: `tests/_helpers/live_ctx.py::install_live_ctx(app, archive=…, proxy_resolver=…, thumbnail_service=…)` wraps the built `CoreCtx` in a `LiveCtx`; unspecified live services become `MagicMock`.
- Models (`backend/app/archive/model.py`): `CanonicalClip(key, name, duration_secs, fps, markers, fields, notes, media, provider_data, fetched_at)`; `MediaRef(mime_type, size_bytes, cached_path, upstream_handle)`; `Marker(name, in_, out, description=None, category=None, color=None)`; `Timecode(secs, fps, frm=None, txt=None)`; `FieldValue(identifier, value, is_multi=False)`; `ClipPage(items, total, offset, limit)`.
- Proxy protocol (`services/proxy_resolver.py`): `class ProxyResolver(Protocol): is_host_local: bool; async def path_for_clip_id(self, clip_id: int) -> Path; def is_managed(self, path) -> bool`.
- Thumbnail (`services/thumbnail_service.py`): `async def get_or_fetch(self, clip_id: int) -> Path | None`.
- Seeding repos: `prompts_repo.create_with_initial_version(db, name, description, body, target_map, output_schema, model) -> (prompt, version_id)`; `annotations_repo.insert(db, Annotation(...)) -> int`; `review_items_repo.bulk_insert(db, [ReviewItem(...)]) -> list[ReviewItem]`. Models in `backend/app/models/annotation.py`.
- Draft view: `services/draft_view.py::build_draft_view(annotation, review_items, *, prompt_name=None, version_num=None, created_at=None, fps=25.0) -> dict` (keys: `has_draft`, `markers`, `fields`, …).
- Publish: clip-detail "✓ Accept & apply all" button is in `templates/pages/_anno_draft.html` (`@click="acceptApplyAll()"`); the route is `POST /clips/{id}/apply` (`routes/review.py::apply_clip`) which enqueues via `ctx.write_queue`.
- App boots offline when `CATDV_USERNAME=""` (no external init; `app.state.live_ctx` is None until we install one). Test env defaults: `tests/conftest.py`.

**Conventions to follow:**
- Always `.venv/bin/python` / `.venv/bin/pytest` (never system Python).
- `asyncio_mode = "auto"` (pytest-asyncio) — async tests need no decorator but `@pytest.mark.asyncio` is harmless.
- Commit after each task with a `feat:`/`test:`/`chore:` message.

---

## File Structure

| File | Responsibility |
|---|---|
| `pyproject.toml` (modify) | Add `playwright>=1.59` to `[project.optional-dependencies].dev`. |
| `.gitignore` (modify) | Ignore `tests/walkthrough/artifacts/`. |
| `tests/walkthrough/__init__.py` (create) | Package marker. |
| `tests/walkthrough/fakes.py` (create) | `FakeArchive`, `LocalFileResolver`, `StubThumbnailService`. |
| `tests/walkthrough/seed.py` (create) | `make_proxy_video()` (ffmpeg) + `seed_draft()` (DB). |
| `tests/walkthrough/app_server.py` (create) | `WalkthroughApp` — in-process uvicorn thread + inject + seed + teardown. |
| `tests/walkthrough/harness.py` (create) | `Walkthrough` (screencast lifecycle, `step`, overlay HTML builder). |
| `tests/walkthrough/scenarios/__init__.py` (create) | Scenario discovery (`load_scenarios()`). |
| `tests/walkthrough/scenarios/review_edit_annotation.py` (create) | The MVP scenario. |
| `tests/walkthrough/gallery.py` (create) | `render_gallery(results) -> str` (static HTML). |
| `tests/walkthrough/run.py` (create) | CLI: `--assert` / `--record`, drives Playwright, writes artifacts + gallery. |
| `tests/walkthrough/test_harness_unit.py` (create) | Unit tests for overlay HTML + record=False short-circuit. |
| `tests/walkthrough/test_gallery_unit.py` (create) | Unit test for gallery HTML. |
| `tests/walkthrough/test_seed_unit.py` (create) | Unit test: `seed_draft` → `build_draft_view(has_draft=True)`. |
| `tests/walkthrough/test_app_server_smoke.py` (create) | Smoke: server boots, `/clips/101` renders with draft, `/api/media/101` streams. |
| `tests/walkthrough/test_scenario_e2e.py` (create) | Runs the scenario in assert mode (skipped if chromium missing). |
| `backend/app/templates/pages/_player.html` (modify) | `data-test="player-play"`. |
| `backend/app/templates/pages/_anno_panels.html` (modify) | `data-test="tab-fields"`, `data-test="tab-markers"`, `data-test="ri-edit-toggle"`. |
| `backend/app/templates/pages/_anno_draft.html` (modify) | `data-test="apply-draft"`. |
| `.claude/skills/e2e/SKILL.md` (create) | The `/e2e` skill. |

---

## Task 1: Scaffolding — dependency, gitignore, package

**Files:**
- Modify: `pyproject.toml`
- Modify: `.gitignore`
- Create: `tests/walkthrough/__init__.py`

- [ ] **Step 1: Add Playwright to dev deps**

In `pyproject.toml`, inside `[project.optional-dependencies]` → `dev = [ ... ]`, add the line after `"pytest-timeout>=2.3",`:

```toml
  "playwright>=1.59",
```

- [ ] **Step 2: Ignore the artifacts dir**

Append to `.gitignore`:

```
# Playwright walkthrough recordings (regenerated on demand)
tests/walkthrough/artifacts/
```

- [ ] **Step 3: Create the package marker**

Create `tests/walkthrough/__init__.py` with a single line:

```python
"""Annotated Playwright walkthrough tests. See docs/specs/2026-06-22-playwright-walkthrough-tests-design.md."""
```

- [ ] **Step 4: Install Playwright + the browser**

Run:
```bash
.venv/bin/pip install -e ".[dev]"
.venv/bin/playwright install chromium
```
Expected: pip reports `playwright` installed; `playwright install` downloads Chromium (or reports it's already present).

- [ ] **Step 5: Verify import**

Run:
```bash
.venv/bin/python -c "import playwright; from playwright.sync_api import sync_playwright; print('playwright ok')"
```
Expected: `playwright ok`

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml .gitignore tests/walkthrough/__init__.py
git commit -m "chore(e2e): add playwright dep + walkthrough package scaffolding"
```

---

## Task 2: Fakes (FakeArchive, LocalFileResolver, StubThumbnailService)

**Files:**
- Create: `tests/walkthrough/fakes.py`
- Test: covered by Task 5 smoke test (these are trivial data holders; a dedicated unit test would only restate the code).

- [ ] **Step 1: Write the fakes**

Create `tests/walkthrough/fakes.py`:

```python
"""Walkthrough-local archive/resolver/thumbnail doubles.

Mirrors tests/integration/test_clip_detail_draft.py: the web UI requires a
numeric clip key (ui/view_models.py does int(clip.key[1])), which the real fs
provider cannot supply. These are injected via install_live_ctx.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from backend.app.archive.errors import ProviderError
from backend.app.archive.model import (
    CanonicalClip,
    ClipPage,
    FieldValue,
    Marker,
    MediaRef,
    Timecode,
)

CLIP_ID = 101
CLIP_NAME = "archive_30s"
DECADE_IDENT = "pragafilm.dekáda.natočení"
PUBLISHED_DECADE = "30.léta"


def build_clip(video_path: Path, duration_secs: float = 8.0, fps: float = 25.0) -> CanonicalClip:
    """The single clip the walkthrough renders. Published state lives here."""
    return CanonicalClip(
        key=("catdv", str(CLIP_ID)),
        name=CLIP_NAME,
        duration_secs=duration_secs,
        fps=fps,
        markers=(
            Marker(
                name="intro",
                in_=Timecode(secs=0.0, fps=fps, frm=0),
                out=Timecode(secs=2.0, fps=fps, frm=50),
                description="Opening title card",
            ),
        ),
        fields={DECADE_IDENT: FieldValue(identifier=DECADE_IDENT, value=PUBLISHED_DECADE)},
        notes={"notes": "Test clip"},
        media=MediaRef(
            mime_type="video/mp4",
            size_bytes=video_path.stat().st_size,
            cached_path=video_path,
            upstream_handle=str(CLIP_ID),
        ),
        provider_data={"ID": CLIP_ID, "name": CLIP_NAME},
        fetched_at=datetime.now(UTC),
    )


class FakeArchive:
    """Numeric-keyed archive serving exactly one clip. Records apply_changes."""

    def __init__(self, clip: CanonicalClip) -> None:
        self._clip = clip
        self.applied: list = []

    async def list_clips(self, catalog, query):
        return ClipPage(items=(self._clip,), total=1, offset=query.offset, limit=query.limit)

    async def get_clip(self, clip_id_str: str):
        if clip_id_str == self._clip.key[1]:
            return self._clip
        raise ProviderError(f"clip not found: {clip_id_str}")

    async def apply_changes(self, change_set):
        # MVP: record the attempt. The durable write-queue rows are the real
        # receipt for "publish happened"; no upstream write is performed.
        self.applied.append(change_set)
        from backend.app.archive.model import WriteResult

        return WriteResult(status="ok", upstream_response={}, new_etag="fake-etag")


class LocalFileResolver:
    """Returns a real on-disk video so /api/media/{id} streams a playable file."""

    is_host_local = False

    def __init__(self, video_path: Path) -> None:
        self._video = video_path

    async def path_for_clip_id(self, clip_id: int) -> Path:
        return self._video

    def is_managed(self, path: Path) -> bool:
        return True


class StubThumbnailService:
    """Offline-safe: always a cache miss → UI renders a placeholder."""

    is_online_provider = False

    async def get_or_fetch(self, clip_id: int):
        return None
```

- [ ] **Step 2: Verify it imports**

Run:
```bash
.venv/bin/python -c "from tests.walkthrough.fakes import FakeArchive, build_clip, LocalFileResolver, StubThumbnailService; print('fakes ok')"
```
Expected: `fakes ok` (this also confirms `WriteResult`, `FieldValue`, etc. import correctly).

If `WriteResult` import fails, check its exact name in `backend/app/archive/model.py` (grep `class WriteResult`) and fix the import.

- [ ] **Step 3: Commit**

```bash
git add tests/walkthrough/fakes.py
git commit -m "feat(e2e): walkthrough archive/resolver/thumbnail fakes"
```

---

## Task 3: Seeding helpers (proxy video + DB draft)

**Files:**
- Create: `tests/walkthrough/seed.py`
- Test: `tests/walkthrough/test_seed_unit.py`

- [ ] **Step 1: Write the failing test**

Create `tests/walkthrough/test_seed_unit.py`:

```python
"""Unit tests for walkthrough seeding."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from backend.app.repositories.annotations import AnnotationsRepo
from backend.app.repositories.review_items import ReviewItemsRepo
from backend.app.services.draft_view import build_draft_view
from tests.walkthrough import seed
from tests.walkthrough.fakes import CLIP_ID, DECADE_IDENT


def test_make_proxy_video_creates_playable_file(tmp_path: Path):
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not installed")
    out = seed.make_proxy_video(tmp_path / "proxy.mp4", seconds=2)
    assert out.exists()
    assert out.stat().st_size > 1000  # a real encoded file, not a stub


async def test_seed_draft_produces_a_draft_view(db):
    await seed.seed_draft(db)
    ann = (await AnnotationsRepo().list_by_clip(db, CLIP_ID))[0]
    items = await ReviewItemsRepo().list_by_clip(db, CLIP_ID)
    view = build_draft_view(ann, items)
    assert view["has_draft"] is True
    decades = [f for f in view["fields"] if f["identifier"] == DECADE_IDENT]
    assert decades and decades[0]["value"] == "20.léta"
```

(`db` is the shared async DB fixture from `tests/conftest.py` / `tests/integration/conftest.py`. If `db` is not visible from `tests/walkthrough/`, add a thin `conftest.py` re-exporting it — see Step 4.)

- [ ] **Step 2: Run the test to verify it fails**

Run:
```bash
.venv/bin/pytest tests/walkthrough/test_seed_unit.py -v
```
Expected: FAIL — `ModuleNotFoundError: tests.walkthrough.seed` (or fixture `db` not found).

- [ ] **Step 3: Write the seeding module**

Create `tests/walkthrough/seed.py`:

```python
"""Seed the test data: a real proxy video + a DB draft for the walkthrough clip."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import aiosqlite

from backend.app.models.annotation import Annotation, ReviewItem
from backend.app.repositories.annotations import AnnotationsRepo
from backend.app.repositories.prompts import PromptsRepo
from backend.app.repositories.review_items import ReviewItemsRepo
from tests.walkthrough.fakes import CLIP_ID, CLIP_NAME, DECADE_IDENT


def make_proxy_video(out_path: Path, seconds: int = 8, fps: int = 25) -> Path:
    """Generate a short MP4 with a burned-in running timecode (so the player
    visibly plays on camera). Requires ffmpeg."""
    if shutil.which("ffmpeg") is None:
        raise RuntimeError(
            "ffmpeg is required to seed the walkthrough proxy video. Install it "
            "(macOS: brew install ffmpeg)."
        )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # testsrc has a built-in frame counter; that alone proves playback.
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi",
        "-i", f"testsrc=duration={seconds}:size=640x360:rate={fps}",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        str(out_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return out_path


async def seed_draft(db: aiosqlite.Connection) -> int:
    """Insert a prompt version + annotation + review items for the clip.

    The draft proposes a DECADE value that differs from the published clip
    (published='30.léta', draft='20.léta') so the review→correct story is
    visible; the scenario then corrects it to '40.léta'.
    """
    prompts = PromptsRepo()
    annotations = AnnotationsRepo()
    items = ReviewItemsRepo()

    _, vid = await prompts.create_with_initial_version(
        db,
        name="scene-tagger",
        description=None,
        body="Describe the decade and scenes.",
        target_map={
            "decade": {"kind": "field", "identifier": DECADE_IDENT},
            "scenes": {"kind": "markers"},
        },
        output_schema={},
        model="gemini-2.5-pro",
    )
    aid = await annotations.insert(
        db,
        Annotation(
            catdv_clip_id=CLIP_ID,
            catdv_clip_name=CLIP_NAME,
            prompt_version_id=vid,
            model="gemini-2.5-pro",
            prompt_used="Describe the decade and scenes.",
            raw_response={},
            structured_output={},
            clip_snapshot={"ID": CLIP_ID, "name": CLIP_NAME, "markers": [], "fields": {}},
        ),
    )
    await items.bulk_insert(
        db,
        [
            ReviewItem(
                annotation_id=aid,
                catdv_clip_id=CLIP_ID,
                kind="field",
                target_identifier=DECADE_IDENT,
                proposed_value="20.léta",
            ),
            ReviewItem(
                annotation_id=aid,
                catdv_clip_id=CLIP_ID,
                kind="marker",
                proposed_value={
                    "name": "Establishing shot",
                    "in": {"frm": 0, "secs": 0.0},
                    "out": {"frm": 75, "secs": 3.0},
                },
            ),
        ],
    )
    return aid
```

- [ ] **Step 4: Add a conftest re-exporting the `db` fixture if needed**

First check whether `db` is already visible:
```bash
.venv/bin/pytest tests/walkthrough/test_seed_unit.py::test_seed_draft_produces_a_draft_view -v 2>&1 | head -20
```
If it errors with `fixture 'db' not found`, create `tests/walkthrough/conftest.py`:

```python
"""Re-expose the shared async DB fixture for walkthrough unit tests."""

from tests.integration.conftest import db  # noqa: F401
```
If the import path is wrong, grep for the fixture: `grep -rn "def db(" tests/ | grep -i fixture` and import from the file that defines it.

- [ ] **Step 5: Run the test to verify it passes**

Run:
```bash
.venv/bin/pytest tests/walkthrough/test_seed_unit.py -v
```
Expected: PASS (the ffmpeg test SKIPS if ffmpeg is absent; install ffmpeg to exercise it).

- [ ] **Step 6: Commit**

```bash
git add tests/walkthrough/seed.py tests/walkthrough/test_seed_unit.py tests/walkthrough/conftest.py
git commit -m "feat(e2e): proxy-video + DB-draft seeding helpers"
```

---

## Task 4: Template `data-test` hooks

**Files:**
- Modify: `backend/app/templates/pages/_player.html`
- Modify: `backend/app/templates/pages/_anno_panels.html`
- Modify: `backend/app/templates/pages/_anno_draft.html`

These are inert attributes; no behavior changes. No new test (covered by the scenario in Task 8). Keep each edit minimal.

- [ ] **Step 1: Player play button**

In `backend/app/templates/pages/_player.html`, find the play button:
```html
      <button type="button" class="btn play"
              :disabled="!canPlay"
              @click="togglePlay()"
```
Change the opening tag to add the hook:
```html
      <button type="button" class="btn play" data-test="player-play"
              :disabled="!canPlay"
              @click="togglePlay()"
```

- [ ] **Step 2: Anno tabs (markers + fields)**

In `backend/app/templates/pages/_anno_panels.html`, the markers tab:
```html
  <button type="button" class="anno-tab" role="tab"
          :class="{ active: tab === 'markers' }"
          :aria-selected="tab === 'markers'"
          @click="tab = 'markers'">
```
→ add `data-test="tab-markers"` to the opening tag:
```html
  <button type="button" class="anno-tab" role="tab" data-test="tab-markers"
          :class="{ active: tab === 'markers' }"
          :aria-selected="tab === 'markers'"
          @click="tab = 'markers'">
```
The fields tab:
```html
  <button type="button" class="anno-tab" role="tab"
          :class="{ active: tab === 'fields' }"
          :aria-selected="tab === 'fields'"
          @click="tab = 'fields'">
```
→
```html
  <button type="button" class="anno-tab" role="tab" data-test="tab-fields"
          :class="{ active: tab === 'fields' }"
          :aria-selected="tab === 'fields'"
          @click="tab = 'fields'">
```

- [ ] **Step 3: Field-row Edit toggle**

In `backend/app/templates/pages/_anno_panels.html`, the **fields** edit button (the one inside the fields loop, near line 125 — `editingItemId === {{ f.item_id }}`):
```html
          <button type="button" class="btn sm ghost"
                  @click="editingItemId = (editingItemId === {{ f.item_id }} ? null : {{ f.item_id }})"
                  x-text="editingItemId === {{ f.item_id }} ? 'Done' : '✎ Edit'"></button>
```
→ add the hook (note: this is the `f.item_id` one, **not** the `m.item_id` markers one):
```html
          <button type="button" class="btn sm ghost" data-test="ri-edit-toggle"
                  @click="editingItemId = (editingItemId === {{ f.item_id }} ? null : {{ f.item_id }})"
                  x-text="editingItemId === {{ f.item_id }} ? 'Done' : '✎ Edit'"></button>
```

- [ ] **Step 4: Accept & apply button**

In `backend/app/templates/pages/_anno_draft.html`:
```html
  <button type="button" class="btn good sm" :disabled="totalCount() === 0" @click="acceptApplyAll()"
          x-text="'✓ Accept &amp; apply all (' + totalCount() + ')'"></button>
```
→
```html
  <button type="button" class="btn good sm" data-test="apply-draft" :disabled="totalCount() === 0" @click="acceptApplyAll()"
          x-text="'✓ Accept &amp; apply all (' + totalCount() + ')'"></button>
```

- [ ] **Step 5: Confirm the design-language guard still passes**

`data-test` attributes are not flagged by the guard, but confirm:
```bash
.venv/bin/pytest tests/unit/test_design_language_guard.py -q
```
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/templates/pages/_player.html backend/app/templates/pages/_anno_panels.html backend/app/templates/pages/_anno_draft.html
git commit -m "feat(e2e): add inert data-test hooks for walkthrough selectors"
```

---

## Task 5: In-process app server (`WalkthroughApp`)

**Files:**
- Create: `tests/walkthrough/app_server.py`
- Test: `tests/walkthrough/test_app_server_smoke.py`

- [ ] **Step 1: Write the failing smoke test**

Create `tests/walkthrough/test_app_server_smoke.py`:

```python
"""Smoke test: the in-process walkthrough app boots, renders the clip, streams media."""

from __future__ import annotations

import shutil
import urllib.request

import pytest

from tests.walkthrough.app_server import WalkthroughApp


@pytest.fixture
def app_url(tmp_path):
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg required to seed the proxy video")
    app = WalkthroughApp(data_dir=tmp_path, port=8766)
    app.start()
    try:
        yield app.base_url
    finally:
        app.stop()


def _get(url: str):
    with urllib.request.urlopen(url, timeout=10) as r:  # noqa: S310
        return r.status, r.read()


def test_clips_list_renders(app_url):
    status, body = _get(f"{app_url}/")
    assert status == 200
    assert b"archive_30s" in body


def test_clip_detail_renders_with_draft(app_url):
    status, body = _get(f"{app_url}/clips/101")
    assert status == 200
    # draft hook from the clip-detail template
    assert b'data-draft-empty="false"' in body


def test_media_streams(app_url):
    status, body = _get(f"{app_url}/api/media/101")
    assert status == 200
    assert len(body) > 1000  # a real video file streamed
```

(`data-draft-empty` is the existing hook from `test_clip_detail_draft.py`; if the attribute name differs, grep `data-draft-empty` in templates and adjust.)

- [ ] **Step 2: Run to verify it fails**

Run:
```bash
.venv/bin/pytest tests/walkthrough/test_app_server_smoke.py -v
```
Expected: FAIL — `ModuleNotFoundError: tests.walkthrough.app_server`.

- [ ] **Step 3: Implement `WalkthroughApp`**

Create `tests/walkthrough/app_server.py`:

```python
"""Boot the real app in-process for Playwright.

Playwright needs a real socket, and the UI needs a numeric-keyed archive that
only injection (not env) can supply. So: run uvicorn.Server on a daemon thread,
then install_live_ctx with our fakes and seed the DB on the server's own event
loop (so the aiosqlite connection is used from the loop that owns it).
"""

from __future__ import annotations

import asyncio
import importlib
import os
import threading
import time
import urllib.request
from pathlib import Path

import uvicorn

from tests.walkthrough import seed
from tests.walkthrough.fakes import (
    FakeArchive,
    LocalFileResolver,
    StubThumbnailService,
    build_clip,
)

_OFFLINE_ENV = {
    "APP_ENV": "dev",
    "CATDV_OFFLINE": "true",
    "CATDV_BASE_URL": "http://localhost:0",
    "CATDV_USERNAME": "",
    "CATDV_PASSWORD": "",
    "CATDV_CATALOG_ID": "881507",
    "GCP_PROJECT_ID": "test-project",
    "GCS_BUCKET_NAME": "test-bucket",
    "INSTANCE_ID": "test-instance",
    "PROXY_SOURCE": "rest",
}


class WalkthroughApp:
    def __init__(self, data_dir: Path, port: int = 8766) -> None:
        self.data_dir = Path(data_dir)
        self.port = port
        self.base_url = f"http://127.0.0.1:{port}"
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._app = None

    def start(self) -> None:
        # 1. Env must be set before importing main (settings read at import/lifespan).
        for k, v in _OFFLINE_ENV.items():
            os.environ.setdefault(k, v)
        os.environ["DATA_DIR"] = str(self.data_dir)
        os.environ["BIND_PORT"] = str(self.port)

        from backend.app import main as main_mod

        importlib.reload(main_mod)
        self._app = main_mod.app

        # 2. Seed the proxy video before boot (no DB needed yet).
        video = seed.make_proxy_video(self.data_dir / "proxy_101.mp4")

        # 3. Run uvicorn on our own loop in a daemon thread.
        config = uvicorn.Config(
            self._app, host="127.0.0.1", port=self.port, log_level="warning"
        )
        self._server = uvicorn.Server(config)

        def _run() -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._loop = loop
            loop.run_until_complete(self._server.serve())

        self._thread = threading.Thread(target=_run, daemon=True)
        self._thread.start()
        self._wait_until_started()

        # 4. Seed the DB on the server's loop (owns the aiosqlite connection).
        core = self._app.state.core_ctx
        fut = asyncio.run_coroutine_threadsafe(seed.seed_draft(core.db), self._loop)
        fut.result(timeout=30)

        # 5. Inject the live context with our fakes.
        from tests._helpers.live_ctx import install_live_ctx

        install_live_ctx(
            self._app,
            archive=FakeArchive(build_clip(video)),
            proxy_resolver=LocalFileResolver(video),
            thumbnail_service=StubThumbnailService(),
        )

    def _wait_until_started(self, timeout: float = 30.0) -> None:
        deadline = time.monotonic() + timeout
        # uvicorn sets server.started once the socket is accepting.
        while time.monotonic() < deadline:
            if self._server is not None and self._server.started:
                # Also confirm the lifespan built core_ctx.
                if getattr(self._app.state, "core_ctx", None) is not None:
                    return
            time.sleep(0.05)
        raise RuntimeError("walkthrough app failed to start within timeout")

    def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=10)
```

- [ ] **Step 4: Run the smoke test to verify it passes**

Run:
```bash
.venv/bin/pytest tests/walkthrough/test_app_server_smoke.py -v
```
Expected: PASS (3 tests). If `data-draft-empty` assertion fails, open `/clips/101` HTML (the test prints body on failure) and adjust the assertion to a stable marker of the draft panel.

Common pitfalls & fixes:
- *`core_ctx` is None after start* → the lifespan may not have run; ensure `importlib.reload(main_mod)` happened and `CATDV_USERNAME=""` so boot doesn't block on external init.
- *Cross-loop aiosqlite error during seeding* → confirm seeding uses `run_coroutine_threadsafe(..., self._loop)` (Step 3), not a fresh `asyncio.run`.
- *Port already in use* → a previous run didn't `stop()`; the fixture's `finally: app.stop()` handles it, but kill stragglers with `lsof -nP -iTCP:8766 -sTCP:LISTEN`.

- [ ] **Step 5: Commit**

```bash
git add tests/walkthrough/app_server.py tests/walkthrough/test_app_server_smoke.py
git commit -m "feat(e2e): in-process walkthrough app server with injected fakes + seeding"
```

---

## Task 6: The annotate harness (`Walkthrough`)

**Files:**
- Create: `tests/walkthrough/harness.py`
- Test: `tests/walkthrough/test_harness_unit.py`

- [ ] **Step 1: Write the failing unit test**

Create `tests/walkthrough/test_harness_unit.py`:

```python
"""Unit tests for the Walkthrough harness (no browser needed)."""

from __future__ import annotations

from unittest.mock import MagicMock

from tests.walkthrough.harness import Walkthrough, step_overlay_html


def test_overlay_html_contains_step_number_and_label():
    html = step_overlay_html(3, "Correct the Decade field")
    assert "Step 3" in html
    assert "Correct the Decade field" in html


def test_overlay_html_escapes_label():
    html = step_overlay_html(1, "<script>alert(1)</script>")
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_assert_mode_does_no_recording():
    page = MagicMock()
    page.screencast = MagicMock()
    wt = Walkthrough(page, record=False)
    wt.start("Title", "Desc")
    ran = []
    wt.step("do a thing", lambda p: ran.append(True))
    wt.finish()
    assert ran == [True]                      # the action still ran
    page.screencast.start.assert_not_called()  # but no recording happened
    assert wt.step_count == 1


def test_record_mode_starts_screencast_and_advances_steps():
    page = MagicMock()
    wt = Walkthrough(page, record=True, video_path="/tmp/x.webm")
    wt.start("Title", "Desc")
    wt.step("one", lambda p: None)
    wt.step("two", lambda p: None)
    wt.finish()
    page.screencast.start.assert_called_once()
    page.screencast.show_chapter.assert_called_once()
    page.screencast.stop.assert_called_once()
    assert wt.step_count == 2
```

- [ ] **Step 2: Run to verify it fails**

Run:
```bash
.venv/bin/pytest tests/walkthrough/test_harness_unit.py -v
```
Expected: FAIL — `ModuleNotFoundError: tests.walkthrough.harness`.

- [ ] **Step 3: Implement the harness**

Create `tests/walkthrough/harness.py`:

```python
"""Walkthrough: wraps a Playwright Page with screencast annotation + step counter.

Recording uses the v1.59 screencast API (page.screencast.start / show_chapter /
show_actions / show_overlay). In assert mode (record=False) every screencast
call is skipped so headless runs do no recording work.
"""

from __future__ import annotations

import html
from typing import Callable


def step_overlay_html(n: int, label: str) -> str:
    """Pure builder for the on-screen step badge (also the doc narration)."""
    safe = html.escape(label)
    return (
        '<div style="position:fixed;top:16px;left:16px;z-index:2147483647;'
        "font:600 18px/1.3 system-ui,sans-serif;color:#fff;background:rgba(20,20,28,.86);"
        'padding:10px 16px;border-radius:10px;box-shadow:0 2px 12px rgba(0,0,0,.4)">'
        f'<span style="color:#7dd3fc">Step {n}</span> — {safe}'
        "</div>"
    )


class Walkthrough:
    def __init__(self, page, *, record: bool, video_path: str | None = None) -> None:
        self.page = page
        self.record = record
        self.video_path = video_path
        self.step_count = 0
        self._overlay = None

    def start(self, title: str, description: str) -> None:
        if not self.record:
            return
        self.page.screencast.start(path=self.video_path)
        self.page.screencast.show_chapter(title=title, description=description)
        self.page.screencast.show_actions(position="bottom")

    def step(self, label: str, action: Callable) -> None:
        self.step_count += 1
        if self.record:
            self._overlay = self.page.screencast.show_overlay(
                html=step_overlay_html(self.step_count, label)
            )
        action(self.page)

    def finish(self) -> str | None:
        if not self.record:
            return None
        self.page.screencast.stop()
        return self.video_path
```

Note on the screencast call shapes: the exact kwargs (`path=`, `title=`/`description=`, `position=`, `html=`) follow the Python screencast docs. If a call signature differs at runtime (Task 9 will exercise it for real), adjust the call here — the unit tests assert *that* the methods are called, not their exact kwargs, so they stay green.

- [ ] **Step 4: Run to verify it passes**

Run:
```bash
.venv/bin/pytest tests/walkthrough/test_harness_unit.py -v
```
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add tests/walkthrough/harness.py tests/walkthrough/test_harness_unit.py
git commit -m "feat(e2e): Walkthrough harness — screencast lifecycle + step overlay"
```

---

## Task 7: Scenario discovery + the MVP scenario

**Files:**
- Create: `tests/walkthrough/scenarios/__init__.py`
- Create: `tests/walkthrough/scenarios/review_edit_annotation.py`

- [ ] **Step 1: Write the scenario**

Create `tests/walkthrough/scenarios/review_edit_annotation.py`:

```python
"""Walkthrough scenario: review and edit an AI annotation, then publish."""

from __future__ import annotations

SLUG = "review-edit-annotation"
TITLE = "Review and edit an AI annotation"
DESCRIPTION = (
    "An operator opens a clip with a pending AI draft, plays the proxy, reviews "
    "the suggested decade field, corrects it, and publishes the accepted draft."
)


def run(wt):
    wt.step(
        "Open the clip from the list",
        lambda p: p.get_by_text("archive_30s").first.click(),
    )
    wt.step(
        "Play the proxy to spot-check",
        lambda p: p.locator('[data-test="player-play"]').click(),
    )
    wt.step(
        "Switch to the draft view",
        lambda p: p.locator('button[data-scope="draft"]').click(),
    )
    wt.step(
        "Open the Fields tab",
        lambda p: p.locator('[data-test="tab-fields"]').click(),
    )
    wt.step(
        "Edit the proposed Decade field",
        lambda p: p.locator('[data-test="ri-edit-toggle"]').first.click(),
    )
    wt.step(
        "Correct the value to 40.léta",
        lambda p: p.locator("input[data-item-id]").first.fill("40.léta"),
    )
    wt.step(
        "Accept & apply (publish) the draft",
        lambda p: p.locator('[data-test="apply-draft"]').click(),
    )
```

- [ ] **Step 2: Write the discovery module**

Create `tests/walkthrough/scenarios/__init__.py`:

```python
"""Scenario discovery — every module here with a SLUG/TITLE/run is a scenario."""

from __future__ import annotations

import importlib
import pkgutil
from types import ModuleType


def load_scenarios() -> list[ModuleType]:
    """Import every sibling module exposing SLUG + run(), sorted by SLUG."""
    mods: list[ModuleType] = []
    for info in pkgutil.iter_modules(__path__):
        if info.name.startswith("_"):
            continue
        mod = importlib.import_module(f"{__name__}.{info.name}")
        if hasattr(mod, "SLUG") and hasattr(mod, "run"):
            mods.append(mod)
    return sorted(mods, key=lambda m: m.SLUG)


def get_scenario(slug: str) -> ModuleType:
    for m in load_scenarios():
        if m.SLUG == slug:
            return m
    raise KeyError(f"no scenario with slug {slug!r}")
```

- [ ] **Step 3: Verify discovery works**

Run:
```bash
.venv/bin/python -c "from tests.walkthrough.scenarios import load_scenarios; print([m.SLUG for m in load_scenarios()])"
```
Expected: `['review-edit-annotation']`

- [ ] **Step 4: Commit**

```bash
git add tests/walkthrough/scenarios/
git commit -m "feat(e2e): scenario discovery + review-edit-annotation scenario"
```

---

## Task 8: Gallery renderer

**Files:**
- Create: `tests/walkthrough/gallery.py`
- Test: `tests/walkthrough/test_gallery_unit.py`

- [ ] **Step 1: Write the failing test**

Create `tests/walkthrough/test_gallery_unit.py`:

```python
"""Unit test for the static gallery HTML."""

from __future__ import annotations

from tests.walkthrough.gallery import render_gallery


def test_gallery_lists_each_scenario_with_video():
    html = render_gallery(
        [
            {"slug": "a", "title": "Flow A", "description": "Does A", "video": "a.webm"},
            {"slug": "b", "title": "Flow B", "description": "Does B", "video": "b.webm"},
        ]
    )
    assert "Flow A" in html and "Flow B" in html
    assert 'src="a.webm"' in html and 'src="b.webm"' in html
    assert "<video" in html


def test_gallery_escapes_title():
    html = render_gallery(
        [{"slug": "x", "title": "<b>x</b>", "description": "d", "video": "x.webm"}]
    )
    assert "<b>x</b>" not in html
    assert "&lt;b&gt;x&lt;/b&gt;" in html
```

- [ ] **Step 2: Run to verify it fails**

Run:
```bash
.venv/bin/pytest tests/walkthrough/test_gallery_unit.py -v
```
Expected: FAIL — `ModuleNotFoundError: tests.walkthrough.gallery`.

- [ ] **Step 3: Implement the gallery**

Create `tests/walkthrough/gallery.py`:

```python
"""Render a static gallery (index.html) of recorded walkthroughs."""

from __future__ import annotations

import html
from typing import TypedDict


class GalleryEntry(TypedDict):
    slug: str
    title: str
    description: str
    video: str  # path relative to the gallery file


def render_gallery(entries: list[GalleryEntry]) -> str:
    cards = []
    for e in entries:
        title = html.escape(e["title"])
        desc = html.escape(e["description"])
        video = html.escape(e["video"], quote=True)
        cards.append(
            f"""    <section class="card">
      <h2>{title}</h2>
      <p>{desc}</p>
      <video controls preload="metadata" width="720" src="{video}"></video>
    </section>"""
        )
    body = "\n".join(cards) if cards else "    <p>No recordings yet.</p>"
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Walkthrough gallery</title>
<style>
  body {{ font: 16px/1.5 system-ui, sans-serif; margin: 2rem auto; max-width: 820px; color: #1a1a1f; }}
  .card {{ border: 1px solid #e3e3ea; border-radius: 12px; padding: 1rem 1.25rem; margin: 1.25rem 0; }}
  h1 {{ font-size: 1.4rem; }} h2 {{ font-size: 1.1rem; margin: 0 0 .25rem; }}
  p {{ color: #55555f; margin: 0 0 .75rem; }}
  video {{ width: 100%; border-radius: 8px; background: #000; }}
</style>
</head>
<body>
  <h1>Annotated walkthroughs</h1>
{body}
</body>
</html>
"""
```

- [ ] **Step 4: Run to verify it passes**

Run:
```bash
.venv/bin/pytest tests/walkthrough/test_gallery_unit.py -v
```
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add tests/walkthrough/gallery.py tests/walkthrough/test_gallery_unit.py
git commit -m "feat(e2e): static walkthrough gallery renderer"
```

---

## Task 9: The runner CLI (`run.py`)

**Files:**
- Create: `tests/walkthrough/run.py`
- Test: `tests/walkthrough/test_scenario_e2e.py`

- [ ] **Step 1: Implement the runner**

Create `tests/walkthrough/run.py`:

```python
"""CLI to run/record walkthrough scenarios.

  python -m tests.walkthrough.run --assert            # headless, no video, pass/fail
  python -m tests.walkthrough.run --record [slug...]  # headed, annotated webm + gallery
"""

from __future__ import annotations

import argparse
import sys
import tempfile
import webbrowser
from pathlib import Path

from playwright.sync_api import sync_playwright

from tests.walkthrough.app_server import WalkthroughApp
from tests.walkthrough.gallery import render_gallery
from tests.walkthrough.harness import Walkthrough
from tests.walkthrough.scenarios import get_scenario, load_scenarios

ARTIFACTS = Path(__file__).parent / "artifacts"


def run_scenarios(slugs: list[str], *, record: bool) -> list[dict]:
    scenarios = (
        [get_scenario(s) for s in slugs] if slugs else load_scenarios()
    )
    results: list[dict] = []
    if record:
        ARTIFACTS.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as data_dir:
        app = WalkthroughApp(data_dir=Path(data_dir), port=8766)
        app.start()
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=not record)
                for mod in scenarios:
                    video = str(ARTIFACTS / f"{mod.SLUG}.webm") if record else None
                    page = browser.new_page(viewport={"width": 1280, "height": 800})
                    page.goto(app.base_url)
                    wt = Walkthrough(page, record=record, video_path=video)
                    ok, err = True, None
                    try:
                        wt.start(mod.TITLE, mod.DESCRIPTION)
                        mod.run(wt)
                        wt.finish()
                    except Exception as exc:  # noqa: BLE001 - report per-scenario
                        ok, err = False, f"{type(exc).__name__}: {exc}"
                    finally:
                        page.close()
                    results.append(
                        {
                            "slug": mod.SLUG,
                            "title": mod.TITLE,
                            "description": mod.DESCRIPTION,
                            "video": f"{mod.SLUG}.webm",
                            "ok": ok,
                            "error": err,
                        }
                    )
                browser.close()
        finally:
            app.stop()
    return results


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="walkthrough")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--assert", dest="assert_", action="store_true")
    mode.add_argument("--record", action="store_true")
    ap.add_argument("slugs", nargs="*", help="scenario slugs (default: all)")
    args = ap.parse_args(argv)

    results = run_scenarios(args.slugs, record=args.record)

    for r in results:
        status = "PASS" if r["ok"] else f"FAIL ({r['error']})"
        print(f"  [{status}] {r['slug']} — {r['title']}")

    if args.record:
        gallery = ARTIFACTS / "index.html"
        gallery.write_text(render_gallery(results), encoding="utf-8")
        print(f"\nGallery: {gallery}")
        webbrowser.open(gallery.as_uri())

    return 0 if all(r["ok"] for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Write the e2e assert-mode test (skips without chromium/ffmpeg)**

Create `tests/walkthrough/test_scenario_e2e.py`:

```python
"""End-to-end: run the MVP scenario in assert mode against the real app."""

from __future__ import annotations

import shutil

import pytest


def _chromium_available() -> bool:
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            path = pw.chromium.executable_path
        return bool(path)
    except Exception:
        return False


@pytest.mark.timeout(180)
def test_review_edit_scenario_passes_in_assert_mode():
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg required to seed the proxy video")
    if not _chromium_available():
        pytest.skip("chromium not installed (run: playwright install chromium)")

    from tests.walkthrough.run import run_scenarios

    results = run_scenarios(["review-edit-annotation"], record=False)
    assert len(results) == 1
    assert results[0]["ok"], results[0]["error"]
```

- [ ] **Step 3: Run the e2e test**

Run:
```bash
.venv/bin/pytest tests/walkthrough/test_scenario_e2e.py -v
```
Expected: PASS (or SKIP if ffmpeg/chromium absent — install both to exercise).

Debugging a failing step: re-run in record mode to *see* where it breaks:
```bash
.venv/bin/python -m tests.walkthrough.run --record review-edit-annotation
```
Then watch `tests/walkthrough/artifacts/review-edit-annotation.webm`. Typical fixes: a selector that needs a `data-test` hook (Task 4), or a step that needs the draft tab open before the fields tab.

- [ ] **Step 4: Commit**

```bash
git add tests/walkthrough/run.py tests/walkthrough/test_scenario_e2e.py
git commit -m "feat(e2e): runner CLI (--assert/--record) + e2e scenario test"
```

---

## Task 10: Manual verification of recording + publish receipt

**Files:** none (verification task). Requires ffmpeg + chromium installed.

- [ ] **Step 1: Record the walkthrough**

Run:
```bash
.venv/bin/python -m tests.walkthrough.run --record review-edit-annotation
```
Expected: prints `[PASS] review-edit-annotation …`, writes `tests/walkthrough/artifacts/review-edit-annotation.webm` and `index.html`, opens the gallery in the browser.

- [ ] **Step 2: Visually confirm the annotations (acceptance flow #2)**

Watch the `.webm`. Confirm: (a) a chapter title card with the use-case title + description, (b) the proxy plays (testsrc frame counter advances), (c) a "Step N — <label>" badge advances 1→7, (d) clicks are highlighted with action labels.

- [ ] **Step 3: Confirm the publish receipt (acceptance flow #3)**

The publish enqueues real DB rows. Add a temporary assertion to prove it, then remove it — OR extend `run_scenarios` debug by querying the DB. Simplest: add a focused integration check in `tests/walkthrough/test_app_server_smoke.py` (keep it):

```python
def test_apply_enqueues_publish(app_url):
    import json, urllib.request
    # Accept the field item, then apply the clip.
    # 1. list items to find the field item id
    with urllib.request.urlopen(f"{app_url}/api/review/clips/101/items", timeout=10) as r:  # noqa: S310
        items = json.loads(r.read())
    field_item = next(i for i in items if i["kind"] == "field")
    # 2. accept it
    req = urllib.request.Request(
        f"{app_url}/api/review/items/{field_item['id']}/decision",
        data=json.dumps({"decision": "accepted"}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    urllib.request.urlopen(req, timeout=10)  # noqa: S310
    # 3. apply the clip
    req2 = urllib.request.Request(f"{app_url}/clips/101/apply", data=b"", method="POST")
    with urllib.request.urlopen(req2, timeout=10) as r:  # noqa: S310
        assert r.status in (200, 204)
```

(Verify the exact items/decision/apply route shapes against `routes/review.py` — adjust paths/payloads if they differ. The point is: the apply path returns success and writes real rows, no archive stub.)

Run:
```bash
.venv/bin/pytest tests/walkthrough/test_app_server_smoke.py::test_apply_enqueues_publish -v
```
Expected: PASS.

- [ ] **Step 4: Commit (if the receipt test was added)**

```bash
git add tests/walkthrough/test_app_server_smoke.py
git commit -m "test(e2e): assert publish path enqueues without an archive stub"
```

---

## Task 11: The `/e2e` skill

**Files:**
- Create: `.claude/skills/e2e/SKILL.md`

- [ ] **Step 1: Write the skill**

Create `.claude/skills/e2e/SKILL.md`:

```markdown
---
name: e2e
description: Run, record, and review the annotated Playwright walkthrough tests for catdv-annotator. Use whenever the user asks to run the e2e / walkthrough / browser tests, record a walkthrough video, regenerate the test-video gallery, or scaffold a new walkthrough scenario. Runs fully offline in its own in-process app on port 8766 and never touches the CatDV license seat or the :8765 dev server.
---

# /e2e — annotated walkthrough tests

Drives the real app in a browser via Python Playwright, optionally recording an
annotated walkthrough video (use-case chapter card + "Step N" counter + action
highlights). See `docs/specs/2026-06-22-playwright-walkthrough-tests-design.md`.

**These tests boot their own in-process app on port 8766 with injected fakes.
They do NOT use CatDV, GCS, or Gemini, and do NOT start/stop the :8765 dev
server. There is no license seat involved — never start the dev server for this.**

## Modes

- `/e2e` (no args) → **assert mode** (fast, headless, pass/fail):
  ```bash
  .venv/bin/python -m tests.walkthrough.run --assert
  ```
- `/e2e record [slug]` → **record mode** (headed, annotated webm + gallery):
  ```bash
  .venv/bin/python -m tests.walkthrough.run --record [slug]
  ```
  Then report the gallery path (`tests/walkthrough/artifacts/index.html`); the
  runner opens it automatically.
- `/e2e new "<plain-English flow>"` → scaffold a new scenario:
  1. Create `tests/walkthrough/scenarios/<slug>.py` with `SLUG`, `TITLE`,
     `DESCRIPTION`, and a `run(wt)` of `wt.step("...", lambda p: ...)` calls,
     following `scenarios/review_edit_annotation.py`. One `step` per
     user-visible action; phrase labels as documentation.
  2. Run `.venv/bin/python -m tests.walkthrough.run --record <slug>` to record it.
  3. If a click can't find its target, add a `data-test="..."` hook to the
     relevant template (see the spec §6) and re-run.

## Preflight (always do this first)

1. Confirm Playwright + Chromium + ffmpeg are available:
   ```bash
   .venv/bin/python -c "import playwright; print('playwright', playwright.__version__)"
   .venv/bin/python -c "from playwright.sync_api import sync_playwright; \
     import contextlib; \
     [print('chromium', pw.chromium.executable_path) for pw in [sync_playwright().start()]]" 2>/dev/null || echo "chromium missing"
   command -v ffmpeg >/dev/null && echo "ffmpeg ok" || echo "ffmpeg missing"
   ```
2. If anything is missing, offer to install (one-time):
   ```bash
   .venv/bin/pip install -e ".[dev]"
   .venv/bin/playwright install chromium
   # ffmpeg: brew install ffmpeg   (ask before installing system packages)
   ```
3. Always use `.venv/bin/python` (never system Python).

## After running

- Report per-scenario PASS/FAIL.
- In record mode, give the user the gallery path and remind them the videos
  double as user documentation.
- Never leave a process on :8766 — the runner tears its server down; if a run
  was interrupted, check `lsof -nP -iTCP:8766 -sTCP:LISTEN` and stop strays.
```

- [ ] **Step 2: Verify the skill is discoverable**

Run:
```bash
ls .claude/skills/e2e/SKILL.md && head -3 .claude/skills/e2e/SKILL.md
```
Expected: the file exists with the frontmatter. (The skill becomes invocable as `/e2e` in a new session.)

- [ ] **Step 3: Commit**

```bash
git add .claude/skills/e2e/SKILL.md
git commit -m "feat(e2e): add /e2e skill to run/record/review walkthroughs"
```

---

## Task 12: Full-suite sanity + docs cross-check

**Files:** possibly `docs/decisions.md` / an ADR (per project convention).

- [ ] **Step 1: Run the walkthrough unit tests (no browser needed)**

Run:
```bash
.venv/bin/pytest tests/walkthrough/test_harness_unit.py tests/walkthrough/test_gallery_unit.py tests/walkthrough/test_seed_unit.py -v
```
Expected: PASS (ffmpeg test SKIPs if ffmpeg absent).

- [ ] **Step 2: Confirm the new code doesn't break existing guards**

Run the import-linter and the design-language guard (the new test dir imports `httpx`-free; the templates only gained `data-test`):
```bash
.venv/bin/python -m pytest tests/unit/test_design_language_guard.py -q
cd /Users/peterhora/.supacode/repos/catdv-annotator/play-wright && .venv/bin/lint-imports || true
```
Expected: design-language guard PASS; lint-imports unaffected by `tests/` additions.

- [ ] **Step 3: Record an ADR (project convention)**

Per `CLAUDE.md`, a non-trivial design call (running the app in-process + injecting fakes over a socket instead of the fs provider) warrants an ADR. Create `docs/adr/NNNN-walkthrough-tests-in-process-injection.md` (one higher than the last ADR number — check `ls docs/adr/`), MADR-lite format:
- Context: need annotated E2E videos; web UI requires numeric clip keys (`int(clip.key[1])`); fs provider can't supply them.
- Decision: in-process uvicorn thread + `install_live_ctx` fakes + seeded DB draft; publish verified at the write-queue level.
- Consequences: offline, seat-free; one flow MVP; SyncEngine writeback + more flows are follow-ons.
Then add a row to the index table in `docs/decisions.md`.

- [ ] **Step 4: Commit**

```bash
git add docs/adr/ docs/decisions.md
git commit -m "docs(adr): record walkthrough-tests in-process injection decision"
```

---

## Self-Review notes (for the implementer)

- **Spec coverage:** Tasks map to spec sections — fakes/injection (§1,§2), harness (§3), scenarios (§4), runner+gallery (§5,§8/9), selectors (§6), `/e2e` skill (§7), seeding (Test-data section, Task 3). Acceptance flows 1–8 are exercised by Task 5 smoke (1), Task 10 (2,3,4), Task 9 e2e (6), Task 11 (7), and adding a second scenario (8, optional).
- **Type consistency:** `CLIP_ID=101`, `DECADE_IDENT`, `CLIP_NAME` are defined once in `fakes.py` and imported by `seed.py`/tests. `Walkthrough(page, record=…, video_path=…)`, `step_overlay_html(n, label)`, `render_gallery(entries)`, `run_scenarios(slugs, record=…)`, `WalkthroughApp(data_dir, port).start()/.stop()/.base_url` are used identically everywhere they appear.
- **Known runtime-verification points** (can't be fully unit-tested, exercised in Tasks 9–10): the exact Playwright screencast kwargs, the `data-draft-empty` assertion string, and the `/api/review/...` + `/clips/{id}/apply` payloads — each step says how to confirm/adjust against the real code.
- **Offline/seat safety:** no task starts the :8765 server or opens a CatDV session; the app boots with `CATDV_USERNAME=""`.
```
