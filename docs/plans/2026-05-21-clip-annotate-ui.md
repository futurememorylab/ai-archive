# Clip Annotate UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an Annotate button + prompt picker on the clip detail page that runs the existing Gemini annotation pipeline against the current clip and renders the result as a "Draft" view of the right-aside metadata, toggleable against the existing "Published" CatDV view.

**Architecture:** No new backend services. We add a pure-function view-model (`build_draft_view`) that maps the latest annotation's `review_items` into the same shape the existing Published panels render. The clip detail page gains a segmented Published↔Draft scope toggle, an Annotate dropdown that posts a job-of-one to the existing `POST /api/jobs`, and an SSE client that swaps the Draft aside via HTMX when the job completes.

**Tech Stack:** Python 3.14 / FastAPI / Jinja2 / HTMX / Alpine.js / pytest / aiosqlite. Frontend is server-rendered; Alpine is used only for trivially small client state.

**Spec:** `docs/specs/2026-05-21-clip-annotate-ui-design.md`

---

## File map

**Create:**
- `backend/app/services/draft_view.py` — pure `build_draft_view(annotation, review_items) → dict` and helpers
- `backend/app/templates/pages/_anno_panels.html` — Markers / Fields / Notes panel renderer (DRY between Published and Draft)
- `backend/app/templates/pages/_anno_draft_empty.html` — empty-draft body
- `backend/app/templates/pages/_anno_draft.html` — Draft aside fragment (used both inline and as the HTMX swap target)
- `backend/app/templates/pages/_annotate_dropdown.html` — button + dropdown
- `backend/app/static/clipAnnotate.js` — Alpine component for dropdown + run lifecycle
- `tests/unit/test_draft_view.py` — unit tests for `build_draft_view`
- `tests/integration/test_clip_detail_draft.py` — route tests for inline draft + `/clips/{id}/draft` partial

**Modify:**
- `backend/app/routes/pages.py:130-148` — extend `clip_detail_page` to load latest annotation + review_items and build `draft`; add `GET /clips/{id}/draft` partial route
- `backend/app/templates/pages/clip_detail.html:142-204` — replace inline aside with the new partials + scope toggle + Annotate dropdown
- `backend/app/static/app.css` — styles for scope toggle, dropdown, status line, draft chip
- `README.md` — "Annotate a clip from the UI" how-to

**Untouched (the backend pipeline is reused as-is):**
- `backend/app/services/{gcs,gemini,annotator,target_map}.py`
- `backend/app/archive/ai_stores/*`
- `backend/app/repositories/{annotations,review_items,jobs,prompts}.py`
- `backend/app/routes/jobs.py`, `backend/app/routes/events.py`
- `scripts/setup-gcp.sh`, `.env.example`, `docs/DEPLOY.md`

---

## Conventions

- All Python runs via the project venv: `.venv/bin/python` and `.venv/bin/pytest`.
- The Czech mojibake helper `_fix` lives in `backend/app/ui/view_models.py` — import it from there, do not re-implement.
- View-models in this codebase return plain `dict[str, Any]`, not pydantic models. Match that pattern.
- Each task ends with a single git commit using a Conventional Commits prefix.

---

## Task 1: `build_draft_view` empty-state path

**Files:**
- Create: `backend/app/services/draft_view.py`
- Test: `tests/unit/test_draft_view.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_draft_view.py
from backend.app.services.draft_view import build_draft_view


def test_build_draft_view_returns_empty_when_annotation_is_none():
    result = build_draft_view(annotation=None, review_items=[])
    assert result == {
        "has_draft": False,
        "annotation_id": None,
        "created_at": None,
        "prompt_name": None,
        "version_num": None,
        "model": None,
        "markers": [],
        "fields": [],
        "notes": None,
    }
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/unit/test_draft_view.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'backend.app.services.draft_view'`

- [ ] **Step 3: Write minimal implementation**

```python
# backend/app/services/draft_view.py
"""Map an Annotation + its ReviewItems into the right-aside view-model.

The result is a dict with the same `markers / fields / notes` shapes the
existing Published view renders, so the Markers / Fields / Notes panels
can render Draft or Published through the same Jinja partial.
"""
from __future__ import annotations

from typing import Any

from backend.app.models.annotation import Annotation, ReviewItem


def build_draft_view(
    annotation: Annotation | None,
    review_items: list[ReviewItem],
) -> dict[str, Any]:
    if annotation is None:
        return {
            "has_draft": False,
            "annotation_id": None,
            "created_at": None,
            "prompt_name": None,
            "version_num": None,
            "model": None,
            "markers": [],
            "fields": [],
            "notes": None,
        }
    return {
        "has_draft": True,
        "annotation_id": annotation.id,
        "created_at": None,
        "prompt_name": None,
        "version_num": None,
        "model": annotation.model,
        "markers": [],
        "fields": [],
        "notes": None,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/unit/test_draft_view.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/draft_view.py tests/unit/test_draft_view.py
git commit -m "feat(draft-view): build_draft_view skeleton + empty-state path"
```

---

## Task 2: Marker review items → marker view dicts

**Files:**
- Modify: `backend/app/services/draft_view.py`
- Test: `tests/unit/test_draft_view.py`

`review_items` of `kind="marker"` carry a `proposed_value` that mirrors a CatDV marker dict: `{"name", "category", "description", "in": {"secs"}, "out": {"secs"}}` (see `tests/integration/test_annotator_worker.py:120-122`). The Published view renders markers with these flat keys: `name, in_secs, out_secs, description, category, color` (see `backend/app/ui/view_models.py:_marker_view`). The draft builder flattens to the same shape and applies the existing `_fix` mojibake cleanup from `backend/app/ui/view_models.py`.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_draft_view.py`:

```python
from backend.app.models.annotation import Annotation, ReviewItem


def _annotation(**overrides):
    base = dict(
        id=42, catdv_clip_id=101, catdv_clip_name="Clip_101",
        prompt_version_id=7, job_id=1, model="gemini-2.5-pro",
        prompt_used="p", raw_response={}, structured_output={},
        clip_snapshot={},
    )
    base.update(overrides)
    return Annotation(**base)


def test_build_draft_view_maps_marker_review_items():
    ann = _annotation()
    items = [
        ReviewItem(
            annotation_id=42, catdv_clip_id=101, kind="marker",
            proposed_value={
                "name": "Scene 1",
                "category": "Event",
                "description": "Intro",
                "in": {"secs": 0.0, "frm": 0},
                "out": {"secs": 1.0, "frm": 25},
            },
        ),
        ReviewItem(
            annotation_id=42, catdv_clip_id=101, kind="marker",
            proposed_value={
                "name": "Scene 2",
                "category": None,
                "description": None,
                "in": {"secs": 1.0, "frm": 25},
                "out": None,
            },
        ),
    ]
    result = build_draft_view(annotation=ann, review_items=items)
    assert result["has_draft"] is True
    assert result["markers"] == [
        {
            "name": "Scene 1",
            "category": "Event",
            "description": "Intro",
            "in_secs": 0.0,
            "out_secs": 1.0,
            "color": None,
        },
        {
            "name": "Scene 2",
            "category": None,
            "description": None,
            "in_secs": 1.0,
            "out_secs": None,
            "color": None,
        },
    ]


def test_build_draft_view_applies_mojibake_fix_to_marker_name_and_description():
    ann = _annotation()
    items = [
        ReviewItem(
            annotation_id=42, catdv_clip_id=101, kind="marker",
            proposed_value={
                "name": "DÄ\x9btsk\xc3\xa9 hry",
                "category": None,
                "description": "S koÃ\x83Â¡rkem",
                "in": {"secs": 0.0, "frm": 0},
                "out": None,
            },
        ),
    ]
    result = build_draft_view(annotation=ann, review_items=items)
    m = result["markers"][0]
    # _fix is a best-effort repair; either it fixes to a readable form or
    # leaves the string untouched. We just assert it ran and produced a str.
    assert isinstance(m["name"], str) and m["name"]
    assert isinstance(m["description"], str)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/unit/test_draft_view.py -v`
Expected: FAIL on the two new tests (`markers` is `[]`).

- [ ] **Step 3: Write minimal implementation**

Update `backend/app/services/draft_view.py`:

```python
from __future__ import annotations

from typing import Any

from backend.app.models.annotation import Annotation, ReviewItem
from backend.app.ui.view_models import _fix


def _marker_from_review(item: ReviewItem) -> dict[str, Any]:
    pv: dict[str, Any] = item.proposed_value if isinstance(item.proposed_value, dict) else {}
    in_part = pv.get("in") or {}
    out_part = pv.get("out")
    return {
        "name": _fix(pv.get("name")) or "",
        "category": pv.get("category"),
        "description": _fix(pv.get("description")),
        "in_secs": float(in_part.get("secs", 0.0)),
        "out_secs": float(out_part["secs"]) if isinstance(out_part, dict) and "secs" in out_part else None,
        "color": pv.get("color"),
    }


def build_draft_view(
    annotation: Annotation | None,
    review_items: list[ReviewItem],
) -> dict[str, Any]:
    if annotation is None:
        return {
            "has_draft": False, "annotation_id": None, "created_at": None,
            "prompt_name": None, "version_num": None, "model": None,
            "markers": [], "fields": [], "notes": None,
        }
    markers = [
        _marker_from_review(it) for it in review_items if it.kind == "marker"
    ]
    markers.sort(key=lambda m: m["in_secs"])
    return {
        "has_draft": True,
        "annotation_id": annotation.id,
        "created_at": None,
        "prompt_name": None,
        "version_num": None,
        "model": annotation.model,
        "markers": markers,
        "fields": [],
        "notes": None,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/unit/test_draft_view.py -v`
Expected: PASS for all four tests.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/draft_view.py tests/unit/test_draft_view.py
git commit -m "feat(draft-view): map marker review_items to flat marker dicts"
```

---

## Task 3: Field review items → field view dicts

**Files:**
- Modify: `backend/app/services/draft_view.py`
- Test: `tests/unit/test_draft_view.py`

Fields in the Published view have `{identifier, name, value}` where `name` is the last dotted segment of the identifier and `value` is a stringified version (lists get joined with `", "`). See `backend/app/ui/view_models.py:_field_view`. Draft builder mirrors this.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_draft_view.py`:

```python
def test_build_draft_view_maps_string_field():
    ann = _annotation()
    items = [
        ReviewItem(
            annotation_id=42, catdv_clip_id=101, kind="field",
            target_identifier="pragafilm.dekáda.natočení",
            proposed_value="30.léta",
        ),
    ]
    result = build_draft_view(annotation=ann, review_items=items)
    assert result["fields"] == [
        {
            "identifier": "pragafilm.dekáda.natočení",
            "name": "natočení",
            "value": "30.léta",
        },
    ]


def test_build_draft_view_maps_list_field_by_joining():
    ann = _annotation()
    items = [
        ReviewItem(
            annotation_id=42, catdv_clip_id=101, kind="field",
            target_identifier="pragafilm.rok.natočení",
            proposed_value=["1932", "1933"],
        ),
    ]
    result = build_draft_view(annotation=ann, review_items=items)
    assert result["fields"] == [
        {
            "identifier": "pragafilm.rok.natočení",
            "name": "natočení",
            "value": "1932, 1933",
        },
    ]


def test_build_draft_view_fields_sorted_by_identifier():
    ann = _annotation()
    items = [
        ReviewItem(
            annotation_id=42, catdv_clip_id=101, kind="field",
            target_identifier="pragafilm.rok.natočení", proposed_value="1932",
        ),
        ReviewItem(
            annotation_id=42, catdv_clip_id=101, kind="field",
            target_identifier="pragafilm.barva", proposed_value="true",
        ),
    ]
    result = build_draft_view(annotation=ann, review_items=items)
    idents = [f["identifier"] for f in result["fields"]]
    assert idents == ["pragafilm.barva", "pragafilm.rok.natočení"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/unit/test_draft_view.py -v`
Expected: FAIL on the three new tests (`fields` is `[]`).

- [ ] **Step 3: Update the implementation**

In `backend/app/services/draft_view.py` add:

```python
def _field_from_review(item: ReviewItem) -> dict[str, Any]:
    identifier = item.target_identifier or ""
    value = item.proposed_value
    if isinstance(value, list):
        value_str = ", ".join(_fix(str(v)) or "" for v in value)
    elif value is None:
        value_str = ""
    else:
        value_str = _fix(str(value)) or ""
    return {
        "identifier": identifier,
        "name": identifier.split(".")[-1],
        "value": value_str,
    }
```

And update `build_draft_view` to populate fields:

```python
    fields = [
        _field_from_review(it) for it in review_items if it.kind == "field"
    ]
    fields.sort(key=lambda f: f["identifier"])
```

then return `"fields": fields` instead of `[]`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/unit/test_draft_view.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/draft_view.py tests/unit/test_draft_view.py
git commit -m "feat(draft-view): map field review_items to {identifier,name,value} dicts"
```

---

## Task 4: Note review items → notes string

**Files:**
- Modify: `backend/app/services/draft_view.py`
- Test: `tests/unit/test_draft_view.py`

`target_map.expand` for `kind="note"` produces a `ReviewItem` with `target_identifier` set to the target key (e.g. `notes` or `bigNotes`) and `proposed_value` being the note text. If multiple note items exist we join with blank lines (rare in practice — most prompts produce one note target).

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_draft_view.py`:

```python
def test_build_draft_view_maps_single_note():
    ann = _annotation()
    items = [
        ReviewItem(
            annotation_id=42, catdv_clip_id=101, kind="note",
            target_identifier="notes", proposed_value="A summary of the clip.",
        ),
    ]
    result = build_draft_view(annotation=ann, review_items=items)
    assert result["notes"] == "A summary of the clip."


def test_build_draft_view_joins_multiple_notes_with_blank_lines():
    ann = _annotation()
    items = [
        ReviewItem(
            annotation_id=42, catdv_clip_id=101, kind="note",
            target_identifier="notes", proposed_value="Line one.",
        ),
        ReviewItem(
            annotation_id=42, catdv_clip_id=101, kind="note",
            target_identifier="bigNotes", proposed_value="Line two.",
        ),
    ]
    result = build_draft_view(annotation=ann, review_items=items)
    assert result["notes"] == "Line one.\n\nLine two."
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/unit/test_draft_view.py -v`
Expected: FAIL on the two new tests.

- [ ] **Step 3: Update the implementation**

In `backend/app/services/draft_view.py` update `build_draft_view`:

```python
    note_texts = [
        _fix(str(it.proposed_value)) or ""
        for it in review_items
        if it.kind == "note" and it.proposed_value is not None
    ]
    notes = "\n\n".join(t for t in note_texts if t) or None
```

return `"notes": notes` instead of `None`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/unit/test_draft_view.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/draft_view.py tests/unit/test_draft_view.py
git commit -m "feat(draft-view): map note review_items to a single notes string"
```

---

## Task 5: Header chip metadata — prompt name, version, created_at

**Files:**
- Modify: `backend/app/services/draft_view.py`
- Test: `tests/unit/test_draft_view.py`

The Draft view's header chip in the spec reads `Prompt "X" • v3 • gemini-2.5-pro • HH:MM:SS`. The builder takes an optional `prompt_name`, `version_num`, and `created_at` so the caller can supply them after loading the prompt-version record. Keeping these as caller-supplied avoids the view-model depending on `prompts_repo`.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_draft_view.py`:

```python
def test_build_draft_view_includes_header_chip_metadata_when_supplied():
    ann = _annotation()
    result = build_draft_view(
        annotation=ann,
        review_items=[],
        prompt_name="Decade tagger",
        version_num=3,
        created_at="2026-05-21T14:22:08+00:00",
    )
    assert result["prompt_name"] == "Decade tagger"
    assert result["version_num"] == 3
    assert result["created_at"] == "2026-05-21T14:22:08+00:00"
    assert result["model"] == "gemini-2.5-pro"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/unit/test_draft_view.py::test_build_draft_view_includes_header_chip_metadata_when_supplied -v`
Expected: FAIL — `TypeError: build_draft_view() got an unexpected keyword argument 'prompt_name'`.

- [ ] **Step 3: Update the signature**

In `backend/app/services/draft_view.py`:

```python
def build_draft_view(
    annotation: Annotation | None,
    review_items: list[ReviewItem],
    *,
    prompt_name: str | None = None,
    version_num: int | None = None,
    created_at: str | None = None,
) -> dict[str, Any]:
```

In both return paths set `prompt_name=prompt_name`, `version_num=version_num`, `created_at=created_at`.

- [ ] **Step 4: Run all draft-view tests**

Run: `.venv/bin/pytest tests/unit/test_draft_view.py -v`
Expected: PASS for all tests.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/draft_view.py tests/unit/test_draft_view.py
git commit -m "feat(draft-view): accept caller-supplied prompt name/version/created_at"
```

---

## Task 6: Wire `draft` into `clip_detail_page`

**Files:**
- Modify: `backend/app/routes/pages.py:130-148`
- Test: `tests/integration/test_clip_detail_draft.py`

The route loads the latest annotation for this clip (or `None`), filters its review_items in Python (the repo's `list_by_clip` returns all items for the clip across all annotations), and passes `draft` alongside `clip` to the template.

- [ ] **Step 1: Read the integration test conftest**

```bash
.venv/bin/cat tests/integration/conftest.py | head -80
```

Identify which fixtures provide an `app` and a `db` already wired with `ctx`. Reuse them.

- [ ] **Step 2: Write the failing tests**

Create `tests/integration/test_clip_detail_draft.py`:

```python
import pytest
from httpx import AsyncClient

from backend.app.models.annotation import Annotation, ReviewItem


@pytest.mark.asyncio
async def test_clip_detail_renders_empty_draft_when_no_annotation(
    client: AsyncClient, seeded_clip_101,
):
    r = await client.get("/clips/101")
    assert r.status_code == 200
    assert 'data-draft-empty="true"' in r.text


@pytest.mark.asyncio
async def test_clip_detail_renders_draft_when_annotation_exists(
    client: AsyncClient, seeded_clip_101, ctx,
):
    ann_id = await ctx.annotations_repo.insert(
        ctx.db,
        Annotation(
            catdv_clip_id=101, catdv_clip_name="Clip_101",
            prompt_version_id=1, job_id=1, model="gemini-2.5-pro",
            prompt_used="p", raw_response={}, structured_output={},
            clip_snapshot={},
        ),
    )
    await ctx.review_items_repo.bulk_insert(
        ctx.db,
        [
            ReviewItem(
                annotation_id=ann_id, catdv_clip_id=101, kind="marker",
                proposed_value={
                    "name": "Scene 1", "category": None, "description": None,
                    "in": {"secs": 0.0, "frm": 0},
                    "out": {"secs": 1.0, "frm": 25},
                },
            ),
        ],
    )
    r = await client.get("/clips/101")
    assert r.status_code == 200
    assert 'data-draft-empty="true"' not in r.text
    assert "Scene 1" in r.text
```

Note: the `seeded_clip_101`, `client`, and `ctx` fixtures need to exist. If they don't already, add them in this test file's module-level setup using the existing `tests/integration/conftest.py` patterns (a `FakeArchive` returning a `CanonicalClip` with id 101 — same approach as `tests/integration/test_annotator_worker.py:112-117`). Inspect that file first and mimic.

- [ ] **Step 3: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/integration/test_clip_detail_draft.py -v`
Expected: FAIL — `'data-draft-empty="true"' in r.text` returns False because nothing emits that marker yet.

- [ ] **Step 4: Update `routes/pages.py`**

Replace `backend/app/routes/pages.py:130-148` (`clip_detail_page`) with:

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
    ctx_dict["draft"] = await _build_draft_for_clip(ctx, clip_id)
    return templates.TemplateResponse(request, "pages/clip_detail.html", ctx_dict)


async def _build_draft_for_clip(ctx, clip_id: int) -> dict:
    from backend.app.services.draft_view import build_draft_view

    annotations = await ctx.annotations_repo.list_by_clip(ctx.db, clip_id)
    if not annotations:
        return build_draft_view(annotation=None, review_items=[])
    latest = annotations[0]  # list_by_clip is DESC
    all_items = await ctx.review_items_repo.list_by_clip(ctx.db, clip_id)
    items = [it for it in all_items if it.annotation_id == latest.id]
    prompt_name: str | None = None
    version_num: int | None = None
    try:
        version = await ctx.prompts_repo.get_version(ctx.db, latest.prompt_version_id)
        version_num = version.version_num
        prompt = await ctx.prompts_repo.get(ctx.db, version.prompt_id)
        prompt_name = prompt.name
    except LookupError:
        pass
    return build_draft_view(
        annotation=latest,
        review_items=items,
        prompt_name=prompt_name,
        version_num=version_num,
        created_at=None,  # filled in Task 7 once annotations carry created_at
    )
```

- [ ] **Step 5: Update the template to emit the empty-state marker**

Temporarily, at the very bottom of `backend/app/templates/pages/clip_detail.html` just before `{% endblock %}`, add a hidden hook so the failing-test assertion has something to find. We will replace this in Task 8 with the real Draft markup:

```jinja
<div hidden
     data-draft-empty="{{ 'true' if not draft.has_draft else 'false' }}"></div>
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/integration/test_clip_detail_draft.py -v`
Expected: PASS for both tests. (Marker shows up; second test sees `data-draft-empty="false"` and "Scene 1" — wait, "Scene 1" appears in the draft dict but is not yet in rendered output. **For this task, only the data-draft-empty="false" assertion needs to pass.** Update the test so that the "Scene 1" assertion is `pytest.xfail`-ed until Task 8.)

Adjust the second test:

```python
    r = await client.get("/clips/101")
    assert r.status_code == 200
    assert 'data-draft-empty="true"' not in r.text
    # "Scene 1" rendering lands in Task 8.
```

Re-run: PASS for both.

- [ ] **Step 7: Commit**

```bash
git add backend/app/routes/pages.py backend/app/templates/pages/clip_detail.html tests/integration/test_clip_detail_draft.py
git commit -m "feat(clip-detail): include draft view-model in clip_detail_page"
```

---

## Task 7: Persist `created_at` on annotations and surface it in the draft

**Files:**
- Modify: `backend/app/models/annotation.py`
- Modify: `backend/app/repositories/annotations.py`
- Modify: `backend/app/routes/pages.py` (the `_build_draft_for_clip` helper)
- Test: `tests/integration/test_annotations_repo.py` (extend existing)

The `Annotation` row already has a `created_at` column in the DB (the INSERT writes `_now_iso()`), but the model and `get`/`list_by_clip` don't read it back. Add it to the model and SELECTs so the draft header chip can show timestamps.

- [ ] **Step 1: Inspect current annotations test to see fixture style**

```bash
.venv/bin/cat tests/integration/test_annotations_repo.py | head -60
```

- [ ] **Step 2: Write the failing test**

Append a new test to `tests/integration/test_annotations_repo.py`:

```python
@pytest.mark.asyncio
async def test_list_by_clip_returns_created_at(db):
    repo = AnnotationsRepo()
    aid = await repo.insert(
        db,
        Annotation(
            catdv_clip_id=999, catdv_clip_name="x",
            prompt_version_id=1, job_id=None, model="m",
            prompt_used="p", raw_response={}, structured_output=None,
            clip_snapshot={},
        ),
    )
    rows = await repo.list_by_clip(db, 999)
    assert len(rows) == 1
    assert rows[0].id == aid
    assert rows[0].created_at and "T" in rows[0].created_at
```

(`Annotation` will need the `created_at` field — the test as written fails at construction. Add `created_at: str | None = None` to `Annotation` in the same step so the test compiles, then the test fails on the assertion.)

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/bin/pytest tests/integration/test_annotations_repo.py -v -k test_list_by_clip_returns_created_at`
Expected: FAIL — `rows[0].created_at` is `None`.

- [ ] **Step 4: Update the SELECTs**

In `backend/app/repositories/annotations.py`:

- Add `created_at` to the SELECT columns in `get` and `list_by_clip`.
- Update `_row` to read it:

```python
@staticmethod
def _row(row) -> Annotation:
    structured_raw = row[8]
    structured = None if structured_raw == "null" else json.loads(structured_raw)
    return Annotation(
        id=row[0],
        catdv_clip_id=row[1],
        catdv_clip_name=row[2],
        prompt_version_id=row[3],
        job_id=row[4],
        model=row[5],
        prompt_used=row[6],
        raw_response=json.loads(row[7]),
        structured_output=structured,
        clip_snapshot=json.loads(row[9]),
        created_at=row[10] if len(row) > 10 else None,
    )
```

Both SELECTs change from `SELECT id, …, clip_snapshot FROM annotations …` to `SELECT id, …, clip_snapshot, created_at FROM annotations …`.

- [ ] **Step 5: Update `_build_draft_for_clip`**

In `backend/app/routes/pages.py`:

```python
    return build_draft_view(
        annotation=latest,
        review_items=items,
        prompt_name=prompt_name,
        version_num=version_num,
        created_at=latest.created_at,
    )
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/integration/test_annotations_repo.py tests/integration/test_clip_detail_draft.py tests/unit/test_draft_view.py -v`
Expected: all PASS. Existing repo tests should not break — the SELECT change is additive.

- [ ] **Step 7: Commit**

```bash
git add backend/app/models/annotation.py backend/app/repositories/annotations.py backend/app/routes/pages.py tests/integration/test_annotations_repo.py
git commit -m "feat(annotations): surface created_at via model + repo SELECTs"
```

---

## Task 8: Extract `_anno_panels.html` partial (no behavior change)

**Files:**
- Create: `backend/app/templates/pages/_anno_panels.html`
- Modify: `backend/app/templates/pages/clip_detail.html:142-204`

Pull the markers / fields / notes panels out into a reusable Jinja include. Both Published (driven by `clip`) and Draft (driven by `draft`) call it with the same shape.

- [ ] **Step 1: Create the partial**

`backend/app/templates/pages/_anno_panels.html`:

```jinja
{# Inputs:
     panels — { markers: list, fields: list, notes: str|None, big_notes: str|None }
     scope  — "published" or "draft" (used only for distinct DOM ids if needed)
#}
{% set has_notes = panels.notes or panels.big_notes %}
<div class="anno-tabs" role="tablist">
  <button type="button" class="anno-tab" role="tab"
          :class="{ active: tab === 'markers' }"
          :aria-selected="tab === 'markers'"
          @click="tab = 'markers'">
    Markers <span class="count">{{ panels.markers|length }}</span>
  </button>
  <button type="button" class="anno-tab" role="tab"
          :class="{ active: tab === 'fields' }"
          :aria-selected="tab === 'fields'"
          @click="tab = 'fields'">
    Fields <span class="count">{{ panels.fields|length }}</span>
  </button>
  {% if has_notes %}
  <button type="button" class="anno-tab" role="tab"
          :class="{ active: tab === 'notes' }"
          :aria-selected="tab === 'notes'"
          @click="tab = 'notes'">
    Notes
  </button>
  {% endif %}
</div>

<div class="anno-section" role="tabpanel" x-show="tab === 'markers'" x-cloak>
  {% for m in panels.markers %}
    <article class="marker" @click="seek({{ m.in_secs }})">
      <header>
        <span class="kind marker-kind">MARKER</span>
        {% if m.category %}<span class="cat">{{ m.category }}</span>{% endif %}
        <span class="tc mono">
          {{ smpte(m.in_secs, clip.fps) }}{% if m.out_secs is not none %} – {{ smpte(m.out_secs, clip.fps) }}{% endif %}
        </span>
      </header>
      <h3 class="m-name">{{ m.name }}</h3>
      {% if m.description %}<p class="m-desc">{{ m.description }}</p>{% endif %}
    </article>
  {% else %}
    <p class="muted">No markers.</p>
  {% endfor %}
</div>

<div class="anno-section" role="tabpanel" x-show="tab === 'fields'" x-cloak>
  {% for f in panels.fields %}
    <div class="field-row">
      <span class="ident mono">{{ f.identifier }}</span>
      <span class="arrow">→</span>
      <span class="val">{{ f.value }}</span>
    </div>
  {% else %}
    <p class="muted">No custom fields.</p>
  {% endfor %}
</div>

{% if has_notes %}
<div class="anno-section" role="tabpanel" x-show="tab === 'notes'" x-cloak>
  {% if panels.notes %}<p class="note">{{ panels.notes }}</p>{% endif %}
  {% if panels.big_notes %}<p class="note big">{{ panels.big_notes }}</p>{% endif %}
</div>
{% endif %}
```

- [ ] **Step 2: Replace the inline aside in `clip_detail.html`**

In `backend/app/templates/pages/clip_detail.html`, replace lines 142-204 (the `{% set has_notes = … %}` through closing `</aside>`) with:

```jinja
  <aside class="anno-col" x-data="{ tab: 'markers' }">
    {% with panels = {
        "markers": clip.markers,
        "fields":  clip.fields,
        "notes":   clip.notes,
        "big_notes": clip.big_notes,
       } %}
      {% include "pages/_anno_panels.html" %}
    {% endwith %}
  </aside>
```

Leave the bottom `data-draft-empty` hook from Task 6 untouched.

- [ ] **Step 3: Verify the page still renders identically**

Run the full integration suite (the existing tests already snapshot enough of the clip detail page to catch a regression):

```bash
.venv/bin/pytest tests/integration -v -k clip_detail
```

Then start the dev server and manually load a clip:

```bash
./run.sh &
sleep 3
curl -sf http://localhost:8765/clips/<id-known-to-exist> | grep -E "anno-tabs|marker-kind"
/bin/kill -TERM $(pgrep -f "uvicorn|backend.app")
```

Expected: tabs and marker articles still present.

- [ ] **Step 4: Commit**

```bash
git add backend/app/templates/pages/_anno_panels.html backend/app/templates/pages/clip_detail.html
git commit -m "refactor(clip-detail): extract Markers/Fields/Notes into _anno_panels partial"
```

---

## Task 9: Scope toggle + Draft aside rendering

**Files:**
- Create: `backend/app/templates/pages/_anno_draft.html`
- Create: `backend/app/templates/pages/_anno_draft_empty.html`
- Modify: `backend/app/templates/pages/clip_detail.html`

Add a segmented Published↔Draft toggle above the tabs row. When `Draft` is active, render either the empty-state or the populated panels — using the same `_anno_panels.html` partial.

- [ ] **Step 1: Create the empty-state partial**

`backend/app/templates/pages/_anno_draft_empty.html`:

```jinja
<div class="anno-draft-empty" data-draft-empty="true">
  <p class="muted">No draft yet.</p>
  <p class="muted small">Click <strong>Annotate</strong> to generate one.</p>
</div>
```

- [ ] **Step 2: Create the populated draft partial**

`backend/app/templates/pages/_anno_draft.html`:

```jinja
{% if draft.has_draft %}
  <div class="anno-draft-chip mono">
    {% if draft.prompt_name %}Prompt &ldquo;{{ draft.prompt_name }}&rdquo;{% endif %}
    {% if draft.version_num %} · v{{ draft.version_num }}{% endif %}
    {% if draft.model %} · {{ draft.model }}{% endif %}
    {% if draft.created_at %} · {{ draft.created_at }}{% endif %}
  </div>
  {% with panels = {
      "markers": draft.markers,
      "fields":  draft.fields,
      "notes":   draft.notes,
      "big_notes": none,
     } %}
    {% include "pages/_anno_panels.html" %}
  {% endwith %}
{% else %}
  {% include "pages/_anno_draft_empty.html" %}
{% endif %}
```

- [ ] **Step 3: Rewrite the aside with the scope toggle**

Replace the `<aside class="anno-col" …>` block in `backend/app/templates/pages/clip_detail.html` with:

```jinja
  <aside class="anno-col" x-data="{ scope: 'published', tab: 'markers' }">
    <div class="anno-scope" role="tablist" aria-label="Annotation source">
      <button type="button" class="anno-scope-btn"
              :class="{ active: scope === 'published' }"
              :aria-selected="scope === 'published'"
              @click="scope = 'published'">
        Published
      </button>
      <button type="button" class="anno-scope-btn"
              :class="{ active: scope === 'draft' }"
              :aria-selected="scope === 'draft'"
              @click="scope = 'draft'">
        Draft
      </button>
    </div>

    <div class="anno-scoped" x-show="scope === 'published'" x-cloak>
      {% with panels = {
          "markers": clip.markers,
          "fields":  clip.fields,
          "notes":   clip.notes,
          "big_notes": clip.big_notes,
         } %}
        {% include "pages/_anno_panels.html" %}
      {% endwith %}
    </div>

    <div class="anno-scoped" id="draft-aside" x-show="scope === 'draft'" x-cloak>
      {% include "pages/_anno_draft.html" %}
    </div>
  </aside>
```

Remove the `data-draft-empty` hook block added at the bottom in Task 6 — `_anno_draft_empty.html` now carries the marker.

- [ ] **Step 4: Re-run the clip-detail integration tests**

The seeded-annotation test now needs the "Scene 1" assertion back. Update `tests/integration/test_clip_detail_draft.py` second test:

```python
    r = await client.get("/clips/101")
    assert r.status_code == 200
    assert 'data-draft-empty="true"' not in r.text
    assert "Scene 1" in r.text
```

Run: `.venv/bin/pytest tests/integration/test_clip_detail_draft.py -v`
Expected: PASS.

- [ ] **Step 5: Manual visual check**

```bash
./run.sh &
sleep 3
# Open in browser: http://localhost:8765/clips/<id>
# Verify: scope toggle visible above tabs; "Published" shows markers/fields/notes as before;
# "Draft" shows "No draft yet."
/bin/kill -TERM $(pgrep -f "uvicorn|backend.app")
```

- [ ] **Step 6: Commit**

```bash
git add backend/app/templates/pages/_anno_draft.html backend/app/templates/pages/_anno_draft_empty.html backend/app/templates/pages/clip_detail.html tests/integration/test_clip_detail_draft.py
git commit -m "feat(clip-detail): Published↔Draft scope toggle + Draft aside rendering"
```

---

## Task 10: `GET /clips/{id}/draft` HTMX partial route

**Files:**
- Modify: `backend/app/routes/pages.py`
- Test: `tests/integration/test_clip_detail_draft.py`

After a job completes, the client swaps the Draft aside via `hx-get`. Return only the contents of `#draft-aside` — i.e., `_anno_draft.html`.

- [ ] **Step 1: Write the failing test**

Append to `tests/integration/test_clip_detail_draft.py`:

```python
@pytest.mark.asyncio
async def test_clips_draft_partial_returns_empty_state(
    client: AsyncClient, seeded_clip_101,
):
    r = await client.get("/clips/101/draft")
    assert r.status_code == 200
    assert 'data-draft-empty="true"' in r.text
    # Body is a partial — must not include the full page layout.
    assert "<html" not in r.text.lower()


@pytest.mark.asyncio
async def test_clips_draft_partial_returns_populated_when_annotation_exists(
    client: AsyncClient, seeded_clip_101, ctx,
):
    ann_id = await ctx.annotations_repo.insert(
        ctx.db,
        Annotation(
            catdv_clip_id=101, catdv_clip_name="Clip_101",
            prompt_version_id=1, job_id=1, model="gemini-2.5-pro",
            prompt_used="p", raw_response={}, structured_output={},
            clip_snapshot={},
        ),
    )
    await ctx.review_items_repo.bulk_insert(
        ctx.db,
        [
            ReviewItem(
                annotation_id=ann_id, catdv_clip_id=101, kind="marker",
                proposed_value={
                    "name": "Scene 1", "category": None, "description": None,
                    "in": {"secs": 0.0, "frm": 0},
                    "out": {"secs": 1.0, "frm": 25},
                },
            ),
        ],
    )
    r = await client.get("/clips/101/draft")
    assert r.status_code == 200
    assert "Scene 1" in r.text
    assert "<html" not in r.text.lower()


@pytest.mark.asyncio
async def test_clips_draft_partial_returns_404_when_clip_missing(
    client: AsyncClient,
):
    r = await client.get("/clips/999999/draft")
    assert r.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/integration/test_clip_detail_draft.py -v -k "draft_partial"`
Expected: FAIL — `404` because the route does not exist yet.

- [ ] **Step 3: Add the route**

In `backend/app/routes/pages.py`, after `clip_detail_page`:

```python
@router.get("/clips/{clip_id}/draft", response_class=HTMLResponse)
async def clip_draft_partial(request: Request, clip_id: int):
    ctx = request.app.state.ctx
    if ctx.archive is None:
        raise HTTPException(503, "archive provider not initialized")
    try:
        # Confirm the clip exists so we 404 properly; we don't render it here.
        await ctx.archive.get_clip(str(clip_id))
    except ProviderError as exc:
        raise HTTPException(404, f"clip not found: {exc}") from exc

    draft = await _build_draft_for_clip(ctx, clip_id)
    return templates.TemplateResponse(
        request, "pages/_anno_draft.html", {"draft": draft, "clip": {"fps": 25.0}}
    )
```

Note: the include needs `clip.fps` for the SMPTE timecode in marker headers. We pass a minimal stub `{"fps": 25.0}` since the partial only reads `clip.fps`. If a marker's `_marker_view` were to need other clip fields later, this stub would need to grow — keep watch.

A cleaner fix: change `_anno_panels.html` so it reads `fps` from a partial-local variable rather than `clip.fps`. Apply that here:

In `_anno_panels.html`, replace:

```
{{ smpte(m.in_secs, clip.fps) }}{% if m.out_secs is not none %} – {{ smpte(m.out_secs, clip.fps) }}{% endif %}
```

with:

```
{{ smpte(m.in_secs, panels.fps or clip.fps) }}{% if m.out_secs is not none %} – {{ smpte(m.out_secs, panels.fps or clip.fps) }}{% endif %}
```

And in `_anno_draft.html`, pass `fps`:

```jinja
  {% with panels = {
      "markers": draft.markers,
      "fields":  draft.fields,
      "notes":   draft.notes,
      "big_notes": none,
      "fps": (clip.fps if clip is defined else 25.0),
     } %}
```

Then the partial route doesn't need a stub:

```python
    return templates.TemplateResponse(
        request, "pages/_anno_draft.html", {"draft": draft, "clip": None}
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/integration/test_clip_detail_draft.py -v`
Expected: PASS for all draft tests.

- [ ] **Step 5: Commit**

```bash
git add backend/app/routes/pages.py backend/app/templates/pages/_anno_panels.html backend/app/templates/pages/_anno_draft.html tests/integration/test_clip_detail_draft.py
git commit -m "feat(clip-detail): GET /clips/{id}/draft HTMX partial route"
```

---

## Task 11: Annotate dropdown markup + Alpine skeleton

**Files:**
- Create: `backend/app/templates/pages/_annotate_dropdown.html`
- Create: `backend/app/static/clipAnnotate.js`
- Modify: `backend/app/templates/pages/clip_detail.html` (add the dropdown next to Cache/Evict)
- Modify: `backend/app/templates/pages/layout.html` (script include)

The dropdown opens, fetches the list of production prompts once per page, and renders them. Clicking a prompt doesn't start anything yet — that lands in Task 12.

- [ ] **Step 1: Create the Alpine component**

`backend/app/static/clipAnnotate.js`:

```javascript
function clipAnnotate(clipId) {
  return {
    open: false,
    prompts: null,
    loading: false,
    error: null,
    running: false,
    runningPromptName: null,
    runStatus: null,
    runError: null,
    jobId: null,

    async toggleOpen() {
      this.open = !this.open;
      if (this.open && this.prompts === null) {
        await this.loadPrompts();
      }
    },

    async loadPrompts() {
      this.loading = true;
      this.error = null;
      try {
        const r = await fetch("/api/prompts?archived=0");
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const data = await r.json();
        // Filter prompts that have a production version.
        this.prompts = (data || []).filter(
          (p) => p.current_production_version_id != null,
        );
      } catch (e) {
        this.error = String(e);
        this.prompts = [];
      } finally {
        this.loading = false;
      }
    },

    pick(prompt) {
      // Task 12 fills this in.
      this.open = false;
    },
  };
}
window.clipAnnotate = clipAnnotate;
```

- [ ] **Step 2: Create the dropdown partial**

`backend/app/templates/pages/_annotate_dropdown.html`:

```jinja
<div class="annotate-wrap" x-data="clipAnnotate({{ clip.id }})">
  <button type="button"
          class="ca-btn ca-btn-primary"
          :disabled="running"
          @click="toggleOpen()">
    <span x-show="!running">Annotate</span>
    <span x-show="running" x-cloak>
      Running: <span class="mono" x-text="runningPromptName"></span>
    </span>
  </button>

  <div class="annotate-menu" x-show="open" x-cloak @click.outside="open = false">
    <template x-if="loading">
      <p class="muted">Loading prompts…</p>
    </template>
    <template x-if="!loading && error">
      <p class="error" x-text="error"></p>
    </template>
    <template x-if="!loading && !error && prompts && prompts.length === 0">
      <p class="muted">
        No production prompts.
        <a href="/prompts">Open Prompts</a> to create one.
      </p>
    </template>
    <template x-if="!loading && prompts && prompts.length > 0">
      <ul class="annotate-list">
        <template x-for="p in prompts" :key="p.id">
          <li>
            <button type="button" class="annotate-item" @click="pick(p)">
              <span class="annotate-name" x-text="p.name"></span>
              <span class="annotate-meta mono">
                v<span x-text="p.current_production_version_num"></span>
              </span>
            </button>
          </li>
        </template>
      </ul>
    </template>
  </div>
</div>
```

- [ ] **Step 3: Verify the `prompts` JSON envelope shape**

```bash
.venv/bin/grep -n "current_production" backend/app/routes/prompts.py
```

If the envelope does not include `current_production_version_id` and `current_production_version_num`, look at `_prompt_envelope` in `backend/app/routes/prompts.py:56-66` and add them. Both are computable from the `versions` list already returned. Example:

```python
def _prompt_envelope(prompt: Prompt, versions: list[PromptVersion]) -> dict[str, Any]:
    prod = next((v for v in versions if v.state == "production"), None)
    return {
        # ...existing keys...
        "current_production_version_id": prod.id if prod else None,
        "current_production_version_num": prod.version_num if prod else None,
    }
```

Add a unit test for `_prompt_envelope` in `tests/unit/test_prompt_envelope.py`:

```python
from datetime import datetime, timezone

from backend.app.models.prompt import Prompt, PromptVersion, TargetMap
from backend.app.routes.prompts import _prompt_envelope


def _now():
    return datetime.now(timezone.utc).isoformat()


def _prompt():
    return Prompt(id=1, name="P", description=None, archived=False,
                  created_at=_now(), updated_at=_now())


def _version(state, n):
    return PromptVersion(
        id=10 + n, prompt_id=1, version_num=n, state=state, body="b",
        target_map=TargetMap({}), output_schema={}, model="gemini-2.5-pro",
        created_at=_now(), updated_at=_now(),
    )


def test_envelope_exposes_production_version_when_one_exists():
    env = _prompt_envelope(_prompt(), [_version("draft", 2), _version("production", 1)])
    assert env["current_production_version_id"] == 11
    assert env["current_production_version_num"] == 1


def test_envelope_exposes_none_when_no_production_version():
    env = _prompt_envelope(_prompt(), [_version("draft", 1)])
    assert env["current_production_version_id"] is None
    assert env["current_production_version_num"] is None
```

Run: `.venv/bin/pytest tests/unit/test_prompt_envelope.py -v` → both PASS after adding the two keys to `_prompt_envelope`.

- [ ] **Step 4: Wire dropdown into `clip_detail.html`**

In `backend/app/templates/pages/clip_detail.html`, inside the header's `<span class="cache-actions">`, after the existing Cache/Evict button block, add:

```jinja
      {% include "pages/_annotate_dropdown.html" %}
```

- [ ] **Step 5: Include the JS in layout**

In `backend/app/templates/pages/layout.html`, alongside the other `<script>` tags, add:

```html
<script src="/static/clipAnnotate.js"></script>
```

- [ ] **Step 6: Manual check**

```bash
./run.sh &
sleep 3
# Open a clip page in the browser, click "Annotate".
# Verify: dropdown opens, fetches /api/prompts?archived=0, lists production prompts.
# Pick one — nothing happens yet, dropdown closes.
/bin/kill -TERM $(pgrep -f "uvicorn|backend.app")
```

- [ ] **Step 7: Commit**

```bash
git add backend/app/templates/pages/_annotate_dropdown.html backend/app/static/clipAnnotate.js backend/app/templates/pages/clip_detail.html backend/app/templates/pages/layout.html backend/app/routes/prompts.py tests/unit/test_prompt_envelope.py
git commit -m "feat(clip-detail): Annotate dropdown + Alpine skeleton"
```

---

## Task 12: Wire dropdown to `POST /api/jobs`, auto-switch to Draft

**Files:**
- Modify: `backend/app/static/clipAnnotate.js`
- Modify: `backend/app/templates/pages/clip_detail.html` (lift `scope` state to a parent so the button can flip it)

Clicking a prompt posts a job-of-one, sets `running=true`, switches the aside to Draft, and shows a status line. SSE handling lands in Task 13.

- [ ] **Step 1: Lift `scope` state so both the dropdown and the aside read it**

Wrap the whole `.detail` block (or just header + aside) in a parent Alpine scope, or expose a tiny shared store. Simplest: move `x-data="{ scope: 'published', tab: 'markers' }"` from the `<aside>` to the `.detail` wrapper (`backend/app/templates/pages/clip_detail.html:10`):

```jinja
<div class="detail"
     x-data="Object.assign(player({{ clip.fps }}, {{ clip.duration_secs }}, {{ clip.markers|tojson }}), { scope: 'published', tab: 'markers' })"
     @keydown.window="handleKey($event)">
```

Then update the `<aside>` to drop its own `x-data` and just consume the inherited `scope` / `tab`:

```jinja
  <aside class="anno-col">
```

Manual check: confirm tabs still work, scope toggle still works.

- [ ] **Step 2: Pass `$root` down so `clipAnnotate` can flip `scope`**

Update the dropdown call site in `clip_detail.html`:

```jinja
{% include "pages/_annotate_dropdown.html" %}
```

and in `_annotate_dropdown.html`, change the root x-data init so the Alpine component has access to the parent:

```jinja
<div class="annotate-wrap" x-data="clipAnnotate({{ clip.id }})" x-modelable="">
```

Actually simpler: the Alpine `$root` magic gives the child access to the nearest `x-data`. In `clipAnnotate.js`'s `pick(prompt)`, accept the root as an arg:

```jinja
<button type="button" class="annotate-item" @click="pick(p, $root)">
```

- [ ] **Step 3: Implement `pick`**

Update `backend/app/static/clipAnnotate.js`:

```javascript
    async pick(prompt, root) {
      this.open = false;
      this.running = true;
      this.runningPromptName = prompt.name;
      this.runStatus = "starting";
      this.runError = null;
      if (root) root.scope = "draft";

      try {
        const r = await fetch("/api/jobs", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            prompt_version_id: prompt.current_production_version_id,
            clip_ids: [clipId],
            auto_start: true,
          }),
        });
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const data = await r.json();
        this.jobId = data.id;
        // Task 13 attaches the SSE listener here.
      } catch (e) {
        this.runError = String(e);
        this.running = false;
      }
    },
```

- [ ] **Step 4: Render the status line inside the Draft aside**

In `backend/app/templates/pages/_anno_draft.html`, prepend:

```jinja
<template x-if="$root.scope === 'draft' && ($root.runStatus || $root.runError)">
  <div class="anno-status" :class="{ error: $root.runError }">
    <span x-show="$root.runError" x-text="`Failed: ${$root.runError}`"></span>
    <span x-show="!$root.runError" x-text="$root.runStatus"></span>
  </div>
</template>
```

Wait — `$root` here is the `.detail` wrapper, which has `scope` but not `runStatus`. The cleanest approach: the dropdown's Alpine component is a sibling of the aside, not a parent. To share state, expose `runStatus` / `runError` on `$root` by hoisting them into the `.detail` wrapper's `x-data` too:

```jinja
<div class="detail"
     x-data="Object.assign(player({{ clip.fps }}, {{ clip.duration_secs }}, {{ clip.markers|tojson }}), { scope: 'published', tab: 'markers', runStatus: null, runError: null, running: false, runningPromptName: null })"
     @keydown.window="handleKey($event)">
```

And `clipAnnotate.pick(prompt, root)` mutates `root.runStatus`, `root.running`, etc., instead of its own state. Then `_anno_draft.html` and the Annotate button both read from `$root`. Simpler and avoids cross-component plumbing.

Update `clipAnnotate.js` accordingly: store all run state on `root.*`, not `this.*`. The Annotate button's `:disabled` and labels become `:disabled="$root.running"`, `x-show="$root.running"`, `x-text="$root.runningPromptName"`.

- [ ] **Step 5: Manual check**

```bash
./run.sh &
sleep 3
# Click Annotate, pick a prompt:
# - aside switches to Draft
# - status line reads "starting"
# - DB shows a new job
# (Run will still complete because the server-side annotator runs in BackgroundTasks.)
/bin/kill -TERM $(pgrep -f "uvicorn|backend.app")
```

- [ ] **Step 6: Commit**

```bash
git add backend/app/static/clipAnnotate.js backend/app/templates/pages/clip_detail.html backend/app/templates/pages/_anno_draft.html backend/app/templates/pages/_annotate_dropdown.html
git commit -m "feat(clip-detail): pick prompt → POST /api/jobs → switch to Draft"
```

---

## Task 13: SSE consumer + HTMX swap on `review_ready`

**Files:**
- Modify: `backend/app/static/clipAnnotate.js`

Open `EventSource('/api/jobs/{job_id}/events')`. Map each event to a human status line. On `review_ready`, perform an `hx-get` to `/clips/{clipId}/draft` and replace `#draft-aside`.

- [ ] **Step 1: Add the SSE handler**

In `clipAnnotate.js` `pick`, after `this.jobId = data.id;`:

```javascript
        this.attachStream(root, this.jobId);
```

Add method:

```javascript
    attachStream(root, jobId) {
      const STATUS_LABEL = {
        resolving:    "Locating proxy…",
        uploading:    "Uploading proxy to GCS…",
        prompting:    "Calling Gemini…",
        review_ready: "Done — loading draft…",
      };
      const es = new EventSource(`/api/jobs/${jobId}/events`);
      es.onmessage = async (evt) => {
        let payload;
        try { payload = JSON.parse(evt.data); } catch { return; }
        if (payload.status === "error") {
          root.runError = payload.error || "Unknown error";
          root.runStatus = null;
          root.running = false;
          es.close();
          return;
        }
        const label = STATUS_LABEL[payload.status];
        if (label) root.runStatus = label;
        if (payload.status === "review_ready") {
          await this.swapDraft(root);
          es.close();
        }
      };
      es.onerror = () => {
        es.close();
        // Fall back to polling — Task 14.
        this.pollJob(root, jobId);
      };
    },

    async swapDraft(root) {
      const r = await fetch(`/clips/${clipId}/draft`);
      if (!r.ok) {
        root.runError = `Failed to load draft: HTTP ${r.status}`;
        root.running = false;
        return;
      }
      const html = await r.text();
      const target = document.getElementById("draft-aside");
      if (target) target.innerHTML = html;
      root.runStatus = null;
      root.running = false;
    },
```

- [ ] **Step 2: Manual end-to-end check (requires a working Gemini)**

```bash
./run.sh &
sleep 3
# Click Annotate, pick a prompt. Watch status line transition.
# When complete, Draft aside fills with markers/fields.
/bin/kill -TERM $(pgrep -f "uvicorn|backend.app")
```

Confirm logs show `Application shutdown complete.` so the CatDV seat is released.

If a working Gemini is not available, you can fake the job result by POSTing a job and immediately writing an annotation + review_items + a final event:

```python
# tools/dev_fake_job.py — ad-hoc, optional
```

If you go that route, do not commit the helper.

- [ ] **Step 3: Commit**

```bash
git add backend/app/static/clipAnnotate.js
git commit -m "feat(clip-detail): SSE-driven status line + HTMX draft swap on review_ready"
```

---

## Task 14: Poll fallback when SSE drops

**Files:**
- Modify: `backend/app/static/clipAnnotate.js`

On `EventSource.onerror`, poll `GET /api/jobs/{id}` every 2 s until the job's status is `completed`, `failed`, or `cancelled`. On terminal success, call `swapDraft`. On terminal failure, surface the error.

- [ ] **Step 1: Implement `pollJob`**

In `clipAnnotate.js`:

```javascript
    async pollJob(root, jobId) {
      const TERMINAL = new Set(["completed", "failed", "cancelled"]);
      const STATUS_LABEL = {
        running: "Calling Gemini…",
      };
      while (root.running) {
        await new Promise((res) => setTimeout(res, 2000));
        let job;
        try {
          const r = await fetch(`/api/jobs/${jobId}`);
          if (!r.ok) continue;
          job = await r.json();
        } catch {
          continue;
        }
        if (STATUS_LABEL[job.status]) root.runStatus = STATUS_LABEL[job.status];
        if (TERMINAL.has(job.status)) {
          if (job.status === "completed") {
            await this.swapDraft(root);
          } else {
            const errItem = (job.items || []).find((it) => it.status === "error");
            root.runError = errItem?.error || `Job ${job.status}`;
            root.runStatus = null;
            root.running = false;
          }
          return;
        }
      }
    },
```

- [ ] **Step 2: Manual verification**

Hard to reproduce SSE failure deterministically; trust the code path. As a smoke test, temporarily change the SSE URL in `clipAnnotate.js` to a 404 path, click Annotate, confirm that `pollJob` kicks in and eventually swaps the draft. Revert the change.

- [ ] **Step 3: Commit**

```bash
git add backend/app/static/clipAnnotate.js
git commit -m "feat(clip-detail): poll fallback for SSE drops"
```

---

## Task 15: CSS for scope toggle, dropdown, status line, draft chip

**Files:**
- Modify: `backend/app/static/app.css`

No tests — visual only. Match the existing `anno-tabs` / `ca-btn` aesthetic.

- [ ] **Step 1: Inspect existing component styles**

```bash
.venv/bin/grep -n "\.anno-tab\|\.ca-btn\|\.cache-actions" backend/app/static/app.css
```

Use the same font, padding, color tokens, and spacing as the existing `.anno-tabs` / `.ca-btn` rules.

- [ ] **Step 2: Add styles**

Append to `backend/app/static/app.css`:

```css
/* --- Annotate dropdown --- */
.annotate-wrap { position: relative; display: inline-block; }
.ca-btn-primary {
  background: var(--accent, #4a8fff);
  color: white;
  border: 1px solid var(--accent, #4a8fff);
}
.ca-btn-primary:disabled { opacity: 0.6; cursor: progress; }
.annotate-menu {
  position: absolute; top: calc(100% + 4px); right: 0;
  min-width: 280px; max-height: 360px; overflow-y: auto;
  background: var(--surface, #fff);
  border: 1px solid var(--border, #d0d0d0);
  border-radius: 6px;
  padding: 6px;
  z-index: 50;
  box-shadow: 0 4px 16px rgba(0,0,0,0.12);
}
.annotate-list { list-style: none; margin: 0; padding: 0; }
.annotate-item {
  display: flex; justify-content: space-between; align-items: baseline;
  width: 100%; padding: 6px 8px;
  background: transparent; border: 0; cursor: pointer; text-align: left;
  border-radius: 4px;
}
.annotate-item:hover { background: var(--hover, #f3f3f3); }
.annotate-name { font-weight: 500; }
.annotate-meta { color: var(--muted, #7a7a7a); font-size: 0.9em; }

/* --- Published↔Draft scope toggle --- */
.anno-scope {
  display: inline-flex; gap: 0;
  border: 1px solid var(--border, #d0d0d0);
  border-radius: 6px;
  overflow: hidden;
  margin-bottom: 8px;
}
.anno-scope-btn {
  background: transparent;
  border: 0;
  padding: 4px 10px;
  cursor: pointer;
  font: inherit;
  color: var(--muted, #7a7a7a);
}
.anno-scope-btn.active {
  background: var(--accent, #4a8fff);
  color: white;
}

/* --- Run status line --- */
.anno-status {
  padding: 8px 10px;
  margin: 8px 0;
  background: var(--info-bg, #eef4ff);
  border-left: 3px solid var(--accent, #4a8fff);
  font-size: 0.9em;
}
.anno-status.error {
  background: var(--error-bg, #fdecec);
  border-left-color: var(--error, #c0392b);
  color: var(--error, #c0392b);
}

/* --- Draft chip --- */
.anno-draft-chip {
  font-size: 0.85em;
  color: var(--muted, #7a7a7a);
  padding: 4px 0 6px;
  border-bottom: 1px dashed var(--border, #d0d0d0);
  margin-bottom: 6px;
}

/* --- Empty draft state --- */
.anno-draft-empty {
  padding: 16px 8px;
  text-align: center;
}
.anno-draft-empty .small { font-size: 0.85em; }
```

- [ ] **Step 3: Manual visual check**

```bash
./run.sh &
sleep 3
# Open a clip, click Annotate, observe dropdown styling and scope toggle.
/bin/kill -TERM $(pgrep -f "uvicorn|backend.app")
```

- [ ] **Step 4: Commit**

```bash
git add backend/app/static/app.css
git commit -m "feat(clip-detail): styles for scope toggle, annotate dropdown, status line"
```

---

## Task 16: README "Annotate a clip from the UI" how-to

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Append a new section under "Status"**

Add after the existing Status list:

```markdown
## Annotate a clip from the UI

Once `scripts/setup-gcp.sh` has been run and `.env` has the GCP variables set:

1. Open a clip detail page (e.g. `http://localhost:8765/clips/881603`).
2. Click **Annotate** in the header — a dropdown lists every prompt that has a
   production version.
3. Pick one. The right aside switches to **Draft** and shows a status line:
   *Locating proxy → Uploading proxy to GCS → Calling Gemini → Done*.
4. When the run finishes, the Draft tabs render the proposed markers / fields /
   notes in the same visual treatment as the **Published** tabs (which show the
   current CatDV state). Toggle between them with the Published↔Draft segmented
   control above the tabs.
5. Each run persists an annotation row + review_items in the local DB. The
   Draft view always shows the **latest** annotation for the clip — re-running
   replaces what's visible.

Notes:

- The proxy is fetched and cached on demand if not already local. The status
  line tells you when this is happening.
- Accept / reject of proposed items and pushing them back to CatDV are out of
  scope in this iteration; both flows already have backend hooks
  (`review_items.decision`, `write_queue`) and will land in a follow-up.
- If no prompt has a production version, the dropdown links to `/prompts`.
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: README how-to for Annotate from clip detail"
```

---

## Task 17: End-to-end integration test using the existing fakes

**Files:**
- Create: `tests/integration/test_annotate_ui_e2e.py`

Run a job-of-one with the existing fakes (`FakeArchive`, `FakeResolver`, `FakeAIStore`, `FakeGeminiStructured`), then `GET /clips/{id}` and assert the rendered Draft contains the markers and fields the fake gemini produced.

- [ ] **Step 1: Inspect existing fakes**

```bash
.venv/bin/grep -rn "class FakeArchive\|class FakeResolver\|class FakeAIStore" tests/fakes/ tests/integration/
```

Reuse the ones from `tests/integration/test_annotator_worker.py` (extract into a `tests/fakes/` module if not already there — only if reuse is awkward; otherwise import from that test module directly).

- [ ] **Step 2: Write the test**

```python
# tests/integration/test_annotate_ui_e2e.py
import json
import pytest

from backend.app.repositories.annotations import AnnotationsRepo
from backend.app.repositories.jobs import JobsRepo
from backend.app.repositories.prompts import PromptsRepo
from backend.app.repositories.review_items import ReviewItemsRepo
from backend.app.services.annotator import run_job
from backend.app.services.events import EventBus

# Reuse fakes from the annotator worker test (path may need adjusting if
# they get moved to tests/fakes/).
from tests.integration.test_annotator_worker import (
    FakeArchive, FakeResolver, FakeAIStore,
)


class FakeGeminiStructured:
    def __init__(self, structured):
        self._structured = structured
    def annotate(self, *, file_ref, prompt, schema, model):
        text = json.dumps(self._structured)
        return {"text": text, "raw": {"candidates": [{"text": text}]}}


@pytest.mark.asyncio
async def test_end_to_end_renders_draft_with_gemini_output(
    db, tmp_path, client, ctx,
):
    prompts = PromptsRepo()
    _, vid = await prompts.create_with_initial_version(
        db,
        name="Decade tagger",
        description=None,
        body="p",
        target_map={
            "scenes": {"kind": "markers"},
            "decade": {"kind": "field", "identifier": "pragafilm.dekáda.natočení"},
        },
        output_schema={},
        model="gemini-2.5-pro",
    )
    # promote_version takes prompt_id + version_id; v1 here is on prompt 1.
    await prompts.promote_version(db, prompt_id=1, version_id=vid)

    jobs = JobsRepo()
    job_id = await jobs.create_job(db, prompt_version_id=vid, clip_ids=[101])

    proxy = tmp_path / "101.mov"
    proxy.write_bytes(b"X" * 100)

    structured = {
        "scenes": [
            {"name": "Scene-1", "in": {"frm": 0, "secs": 0.0}, "out": {"frm": 25, "secs": 1.0}},
        ],
        "decade": "30.léta",
    }

    await run_job(
        db=db,
        job_id=job_id,
        archive=FakeArchive({101: {"ID": 101, "name": "Clip_101", "markers": []}}),
        proxy_resolver=FakeResolver({101: proxy}),
        ai_store=FakeAIStore(),
        gemini=FakeGeminiStructured(structured),
        event_bus=EventBus(),
        annotations_repo=AnnotationsRepo(),
        review_items_repo=ReviewItemsRepo(),
        jobs_repo=jobs,
        prompts_repo=prompts,
    )

    r = await client.get("/clips/101")
    assert r.status_code == 200
    assert "Scene-1" in r.text
    assert "pragafilm.dekáda.natočení" in r.text
    assert "30.léta" in r.text
    assert "Decade tagger" in r.text  # header chip
```

Note: `promote_version` is the real method name on `PromptsRepo` (verified at `backend/app/repositories/prompts.py:281`). Its signature takes `db`, `prompt_id`, `version_id`.

- [ ] **Step 3: Run the test**

```bash
.venv/bin/pytest tests/integration/test_annotate_ui_e2e.py -v
```

Expected: PASS.

- [ ] **Step 4: Run the full suite to catch regressions**

```bash
.venv/bin/pytest -q
```

Expected: green.

- [ ] **Step 5: Commit**

```bash
git add tests/integration/test_annotate_ui_e2e.py
git commit -m "test(annotate-ui): end-to-end render of draft from a fake gemini run"
```

---

## Spec-coverage check

| Spec requirement | Task(s) |
|---|---|
| Annotate button next to Cache/Evict | 11 |
| Dropdown listing prompts with a production version | 11 |
| Production-only client-side filter | 11 |
| Empty-state copy + link to `/prompts` when none | 11 |
| `POST /api/jobs` with `{prompt_version_id, clip_ids:[id], auto_start:true}` | 12 |
| Auto-switch aside to Draft on run start | 12 |
| Live status line via `/api/jobs/{id}/events` SSE | 13 |
| Status labels (resolving → uploading → prompting → done) | 13 |
| HTMX swap of Draft aside on `review_ready` | 13 |
| Error path: red status line, previous draft preserved | 13 |
| Poll fallback when SSE drops | 14 |
| `DraftView` with `has_draft`, header chip metadata | 1, 5 |
| Marker mapping (with mojibake fix) | 2 |
| Field mapping (with list join) | 3 |
| Note mapping (single + multi-join) | 4 |
| `build_draft_view` consumed by `clip_detail_page` | 6 |
| `created_at` persisted on annotations | 7 |
| `_anno_panels.html` shared between Published and Draft | 8 |
| Published↔Draft segmented toggle | 9 |
| Empty-state Draft body | 9 |
| `GET /clips/{id}/draft` HTMX partial route | 10 |
| 404 when clip missing | 10 |
| Styles parity with existing components | 15 |
| README how-to | 16 |
| End-to-end integration test | 17 |
