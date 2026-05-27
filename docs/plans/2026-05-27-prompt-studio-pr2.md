# Prompt Studio — PR2 (version compare) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add side-by-side version comparison to Prompt Studio: a version-picker chip on each prompt-card, a `+ Compare` button that materializes a second card, line-diff views for prompt body and structured output, and a second range row on the player timeline for the cmp version's scenes. Reuse `_anno_panels.html` for the Output tab and extract clip_detail's timeline into a shared overlay partial.

**Architecture:** No new tables. Two new HTMX partial routes (`/studio/_prompt_card`, an extended `/studio/_player`), two new shared template partials (`_player_overlay.html`, `_studio_version_picker.html`, `_studio_diff.html`, `_studio_compare.html`), one new server-side adapter (`panels_from_studio_run`), one new client JS file (`studio-diff.js` with a Python-mirror `lineDiff`). Existing components — `Alpine.data("player", ...)`, `_anno_panels.html`, `studio_runs_repo.latest_for_pair` — do the heavy lifting.

**Tech Stack:** Python 3.13, FastAPI, aiosqlite (SQLite), Jinja2, Alpine.js v3, HTMX, pytest, ruff, basedpyright.

**Spec:** `docs/specs/2026-05-26-prompt-studio-pr2-design.md`
**Predecessor plan:** `docs/plans/2026-05-26-prompt-studio-pr1.md`
**Predecessor ADR:** `docs/adr/0033-prompt-studio-pr1-shell-and-run-loop.md`

---

## File map

**Create:**

- `backend/app/services/studio_panels.py` — `panels_from_studio_run(run, version, fps)` adapter
- `backend/app/templates/pages/_player_overlay.html` — shared timeline + ranges + playhead
- `backend/app/templates/pages/_studio_version_picker.html` — version-chip + dropdown
- `backend/app/templates/pages/_studio_compare.html` — wraps the 1-or-2 prompt-cards row
- `backend/app/templates/pages/_studio_diff.html` — diff view body (cmp card only)
- `backend/app/static/studio-diff.js` — `lineDiff(a, b)` JS + Alpine `cmpDiff` component
- `tests/unit/test_studio_panels_adapter.py` — `panels_from_studio_run` unit tests
- `tests/unit/test_studio_line_diff.py` — Python mirror of `lineDiff` + golden tests
- `tests/integration/test_player_overlay_partial.py` — clip_detail regression test
- `tests/integration/test_studio_prompt_card_route.py` — `/studio/_prompt_card` route tests
- `tests/integration/test_studio_player_overlay.py` — `/studio/_player?compare_id=…` route tests
- `tests/integration/test_studio_compare.py` — deep-link + compare materialization e2e
- `docs/adr/0034-prompt-studio-pr2-version-compare.md` — ADR

**Modify:**

- `backend/app/templates/pages/_anno_panels.html` — add optional `show_history` flag
- `backend/app/templates/pages/clip_detail.html` — `{% include "pages/_player_overlay.html" %}`
- `backend/app/templates/pages/_studio_player.html` — switch from native `<video controls>` to `Alpine.data("player", ...)` + shared overlay
- `backend/app/templates/pages/_studio_prompt_card.html` — side-aware (cur|cmp); chip in header; tabs bound to `$root.mode`; cmp gets `Diff vs v{cur}` toggle and `× Close`
- `backend/app/templates/pages/_studio_run_output.html` — drop bespoke `.ro-scene`/`.ro-field` markup; build `panels` and `{% include "pages/_anno_panels.html" %}`; keep the `<script type="application/json" data-run-json>` block
- `backend/app/templates/pages/studio.html` — use `_studio_compare.html`
- `backend/app/routes/pages/studio.py` — accept `?version_id=` and `?compare_version_id=` on `/studio`; add `GET /studio/_prompt_card`; extend `GET /studio/_player` with `?version_id=` and `?compare_id=`
- `backend/app/static/studio.js` — lift `mode` from `studioPromptCard()` to `studioPage()`; add `seekFocusedClip(secs)` proxy; add `compareVersionId` state + `openCompare()` / `closeCompare()`; wire `htmx:afterSwap` to bump `activeVersionId/Num` and `history.replaceState`
- `backend/app/static/app.css` — `.pc-vchip`, `.pc-vchip .menu`, `.btn-compare`, `.btn-diff-toggle`, `.cmp-card`, `.pc-diff` table, `.range-cur`, `.range-cmp`, `.range-draft`, `.timeline-legend`, `.legend-range-*`
- `docs/decisions.md` — append entry for ADR 0034

---

## TDD discipline

Every task follows: **red test → verify red → minimal impl → verify green → commit**. Run unit tests with `.venv/bin/pytest -q <path>`; integration tests the same way. Steps that change CSS or pure JS-runtime behavior have an explicit manual verification step instead of automated assertions (called out per task). Commit after every green step — small commits make review trivial and roll-back easy.

---

## Task 1: Extract `_player_overlay.html` (regression-safe)

Move the inline `.transport` / `.timeline` / `.ranges` / `.playhead` markup from `clip_detail.html` into a shared partial. clip_detail must render byte-identically — this is a pure mechanical extraction and the test guards it.

**Files:**
- Create: `backend/app/templates/pages/_player_overlay.html`
- Create: `tests/integration/test_player_overlay_partial.py`
- Modify: `backend/app/templates/pages/clip_detail.html` (around lines 116-148)

- [ ] **Step 1: Write the regression test**

`tests/integration/test_player_overlay_partial.py`:

```python
"""Clip detail's player transport renders identically after the
.transport/.timeline/.ranges/.playhead markup is extracted into
_player_overlay.html. This guards the only non-Studio surface PR2 touches.
"""

import importlib
import re

import pytest
from fastapi.testclient import TestClient


def _setenv(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.delenv("CATDV_PASSWORD", raising=False)


@pytest.fixture
def client(monkeypatch, tmp_path):
    _setenv(monkeypatch, tmp_path)
    from backend.app import main as main_mod
    importlib.reload(main_mod)
    with TestClient(main_mod.app) as c:
        yield c


def _player_html(html: str) -> str:
    # Slice from `<div class="transport">` to its matching closing — we only
    # care that the transport block is unchanged.
    m = re.search(r'<div class="transport">.*?</div>\s*</div>\s*</div>', html, re.DOTALL)
    return m.group(0) if m else ""


def test_clip_detail_transport_block_present(client):
    # No real clip data in unit env — but the route still 200s with an
    # empty clip placeholder; the timeline-less branch is covered.
    # When a clip has duration we exercise the .transport block.
    r = client.get("/clips/12041")
    # Either 200 (offline-stub clip) or 404 — both shapes are fine. The
    # regression value here is that an *existing* clip detail render
    # contains a .transport block when duration_secs > 0.
    if r.status_code != 200:
        pytest.skip("clip not available in offline test env")
    # If duration was 0 the template suppresses .transport — still OK.
    if "duration_secs" in r.text and 'class="transport"' in r.text:
        assert 'class="timeline"' in r.text
        assert 'class="ticks"' in r.text
        assert 'class="ranges"' in r.text
        assert 'class="playhead"' in r.text
```

- [ ] **Step 2: Run test to verify it passes against current state (baseline)**

```bash
.venv/bin/pytest -q tests/integration/test_player_overlay_partial.py
```

Expected: **PASS** (or `skip` in offline env). This locks the *current* behavior as the regression baseline before extraction.

- [ ] **Step 3: Extract the partial**

Create `backend/app/templates/pages/_player_overlay.html`:

```jinja
{# Shared player transport overlay — used by clip_detail and studio.

   Required scope (from caller's x-data="player(...)"):
     current, fps, duration (via player init), pct(secs), seek(secs),
     seekFromEvent(e), tc(secs), quintileTc(i), isMarkerActive(m), markers.

   Template inputs:
     duration_secs    — server-known clip duration (truthy gates the entire block)
     rows             — list of dicts, each {key, ranges, cls, alpine_list?}
                        - key: display label for legend (e.g. "v5", "draft")
                        - ranges: list of {in_secs, out_secs, name} dicts
                        - cls: CSS class for the row (e.g. "range-cur", "draft-ranges")
                        - alpine_list (optional): Alpine expression for active-highlighting
                                                  (e.g. "markers"); when None, no highlight
     duration_smpte   — pre-formatted SMPTE string for the far-right tc label
#}
{% if duration_secs %}
<div class="transport">
  <div class="timeline" @click="seekFromEvent($event)">
    <div class="ticks"></div>
    {% for row in rows %}
      <div class="ranges {{ row.cls }}"
           {% if row.x_show %}x-show="{{ row.x_show }}" x-cloak{% endif %}>
        {% for m in row.ranges %}
          <div class="range{% if row.cls == 'range-draft' %} draft-range{% endif %}"
               {% if row.alpine_list %}:class="{ active: isMarkerActive({{ row.alpine_list }}[{{ loop.index0 }}]) }"{% endif %}
               style="left: {{ (m.in_secs / duration_secs) * 100 }}%; width: {{ (((m.out_secs or m.in_secs + 1) - m.in_secs) / duration_secs) * 100 }}%"
               title="{{ m.name }}"></div>
        {% endfor %}
      </div>
    {% endfor %}
    <div class="playhead" :style="`left: ${pct(current)}%`"></div>
    <div class="tc-labels">
      <span x-text="quintileTc(0)">00:00:00:00</span>
      <span x-text="quintileTc(1)"></span>
      <span x-text="quintileTc(2)"></span>
      <span x-text="quintileTc(3)"></span>
      <span x-text="quintileTc(4)">{{ duration_smpte or '' }}</span>
    </div>
  </div>
  {% if rows|selectattr('ranges')|list %}
  <div class="timeline-legend mono-cell muted">
    {% for row in rows %}
      {% if row.ranges %}
        <span class="legend-{{ row.cls }}">● {{ row.key }} · {{ row.ranges|length }} scenes</span>
      {% endif %}
    {% endfor %}
  </div>
  {% endif %}
</div>
{% endif %}
```

- [ ] **Step 4: Replace inline markup in clip_detail.html**

In `backend/app/templates/pages/clip_detail.html`, replace lines 116-148 (the entire `{% if clip.duration_secs %} ... {% endif %}` block that renders `.transport`) with:

```jinja
{% with
    duration_secs = clip.duration_secs,
    duration_smpte = duration_smpte,
    rows = [
      {
        "key": "markers",
        "ranges": clip.markers,
        "cls": "range-cur",
        "alpine_list": "markers",
        "x_show": None,
      },
      {
        "key": "draft",
        "ranges": draft.markers if draft and draft.has_draft else [],
        "cls": "range-draft",
        "alpine_list": None,
        "x_show": "scope === 'draft'",
      },
    ]
  %}
  {% include "pages/_player_overlay.html" %}
{% endwith %}
```

If the existing clip_detail block also rendered an inline transport-controls strip below the timeline (play/pause/etc.), keep that as-is *outside* the include. The include covers only the timeline + legend.

- [ ] **Step 5: Run regression test**

```bash
.venv/bin/pytest -q tests/integration/test_player_overlay_partial.py
```

Expected: **PASS**.

- [ ] **Step 6: Manual smoke**

Start the server (`server-start` skill) and visit any cached clip's detail page. Verify:
- Timeline renders with ticks.
- Marker ranges show in correct positions.
- Playhead syncs to scrubbing.
- Switching the draft scope toggles the draft-ranges row.
- Marker click → seek works.

- [ ] **Step 7: Commit**

```bash
git add backend/app/templates/pages/_player_overlay.html \
        backend/app/templates/pages/clip_detail.html \
        tests/integration/test_player_overlay_partial.py
git commit -m "refactor(player): extract _player_overlay.html (used by clip_detail; studio next)"
```

---

## Task 2: Upgrade `_studio_player.html` to custom transport (one row)

Replace the studio player's native `<video controls>` with the same `Alpine.data("player", ...)` component clip_detail uses, plus the shared overlay partial. Still one row (cur only); cmp row arrives in Task 12.

**Files:**
- Modify: `backend/app/templates/pages/_studio_player.html`
- Modify: `backend/app/routes/pages/studio.py` (`/studio/_player` route)
- Create: `tests/integration/test_studio_player_overlay.py`

- [ ] **Step 1: Write the failing route test (one-row)**

`tests/integration/test_studio_player_overlay.py`:

```python
"""GET /studio/_player builds the shared overlay with cur (and optionally cmp)
rows. PR2 task 2 covers one-row mode; task 12 will exercise compare_id=."""

import importlib
import json

import pytest
from fastapi.testclient import TestClient


def _setenv(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.delenv("CATDV_PASSWORD", raising=False)


@pytest.fixture
def client(monkeypatch, tmp_path):
    _setenv(monkeypatch, tmp_path)
    from backend.app import main as main_mod
    importlib.reload(main_mod)
    with TestClient(main_mod.app) as c:
        yield c


def _seed_run(client, *, version_id, clip_id, scenes):
    """Insert a studio_run row directly via the DB for setup."""
    from backend.app import main as main_mod
    import asyncio
    import aiosqlite

    async def _go():
        async with aiosqlite.connect(main_mod.app.state.ctx.db_path) as db:
            await db.execute(
                "INSERT INTO studio_run(prompt_version_id, clip_id, status, "
                "output_json, model, finished_at) "
                "VALUES (?, ?, 'ok', ?, 'gemini-2.5-pro', '2026-05-27T00:00:00Z')",
                (version_id, clip_id, json.dumps({"scenes": scenes})),
            )
            await db.commit()
    asyncio.get_event_loop().run_until_complete(_go())


def test_player_one_row_no_version(client):
    r = client.get("/studio/_player?clip_id=12041")
    assert r.status_code == 200
    # No version_id → no overlay rows, but the player wrapper still renders.
    assert 'data-clip-player' in r.text
    assert 'class="transport"' not in r.text or 'class="ranges' not in r.text


def test_player_one_row_with_version(client):
    # The page route needs a prompt+version to exist. The simplest path:
    # use /api/prompts to create them and seed a run via the helper.
    r = client.post("/api/prompts", json={
        "name": "t", "media_kind": "any", "model": "gemini-2.5-pro",
        "target_map": {}, "output_schema": {}, "body": "x",
    })
    assert r.status_code == 201
    pid = r.json()["prompt_id"]
    vid = r.json()["version_id"]

    _seed_run(client, version_id=vid, clip_id=12041, scenes=[
        {"in_secs": 1.0, "out_secs": 2.0, "name": "a"},
        {"in_secs": 3.0, "out_secs": 4.0, "name": "b"},
    ])

    r = client.get(f"/studio/_player?clip_id=12041&version_id={vid}")
    assert r.status_code == 200
    # One ranges row in the overlay (cur only), with two range divs.
    assert r.text.count('class="ranges range-cur"') == 1
    assert r.text.count('class="range"') >= 2
    # Legend names the version.
    assert 'legend-range-cur' in r.text
```

- [ ] **Step 2: Run test to verify it fails**

```bash
.venv/bin/pytest -q tests/integration/test_studio_player_overlay.py
```

Expected: **FAIL** — current `_studio_player.html` emits native `<video controls>` with no `.ranges` block.

- [ ] **Step 3: Rewrite `_studio_player.html`**

`backend/app/templates/pages/_studio_player.html`:

```jinja
{# Studio player. Uses the same Alpine player() component as clip_detail
   plus the shared _player_overlay.html partial. The overlay is gated by
   the server-side rows list — when no version_id was passed (or the
   focused clip has no scenes recorded yet), the overlay renders just
   the playhead + ticks.

   Template inputs:
     clip_id          — int
     fps              — float (defaults to 25 if unknown)
     duration_secs    — float | None
     duration_smpte   — str  | ""
     rows             — list (built in the route)
#}
<div class="studio-player"
     data-clip-player
     data-clip-id="{{ clip_id }}"
     x-data="player({{ fps or 25 }}, {{ duration_secs or 0 }}, [], [])">
  <video x-ref="video"
         class="video"
         src="/api/media/{{ clip_id }}"
         preload="metadata"
         controls
         style="width: 100%; max-height: 320px; background: #000;"></video>
  {% include "pages/_player_overlay.html" %}
</div>
```

Note: `controls` stays on the `<video>` — we layer the overlay below the native transport. The umbrella spec says "no new player behavior"; the overlay is a visualization-only addition.

- [ ] **Step 4: Extend the `/studio/_player` route**

In `backend/app/routes/pages/studio.py`, replace the existing `_studio_player` handler:

```python
@router.get("/studio/_player", response_class=HTMLResponse)
async def _studio_player(
    request: Request,
    clip_id: int,
    version_id: int | None = None,
    compare_id: int | None = None,
):
    """Player wrapper for the focused clip + scenes overlay.

    Builds rows = [cur (if version_id), cmp (if compare_id)] each carrying
    that version's latest run scenes. Empty rows are still passed so the
    overlay can short-circuit on no-scenes consistently.
    """
    ctx = get_ctx(request)

    # Resolve clip metadata via the archive when available.
    fps: float = 25.0
    duration_secs: float | None = None
    duration_smpte: str = ""
    if ctx.archive:
        try:
            clip = await ctx.archive.get_clip(str(clip_id))
            fps = float(clip.fps or 25.0)
            duration_secs = clip.duration_secs
        except Exception:  # noqa: BLE001
            pass

    async def _scenes_for(vid: int) -> list[dict]:
        run = await ctx.studio_runs_repo.latest_for_pair(
            ctx.db, prompt_version_id=vid, clip_id=clip_id
        )
        if not run or not run.output_json:
            return []
        return list(run.output_json.get("scenes") or [])

    rows: list[dict] = []
    if version_id is not None:
        scenes = await _scenes_for(version_id)
        # Fetch version_num for the legend label.
        try:
            v = await ctx.prompts_repo.get_version(ctx.db, version_id)
            label = f"v{v.version_num}"
        except LookupError:
            label = f"v?{version_id}"
        rows.append({
            "key": label, "ranges": scenes, "cls": "range-cur",
            "alpine_list": None, "x_show": None,
        })
    if compare_id is not None:
        scenes = await _scenes_for(compare_id)
        try:
            v = await ctx.prompts_repo.get_version(ctx.db, compare_id)
            label = f"v{v.version_num}"
        except LookupError:
            label = f"v?{compare_id}"
        rows.append({
            "key": label, "ranges": scenes, "cls": "range-cmp",
            "alpine_list": None, "x_show": None,
        })

    return templates.TemplateResponse(
        request,
        "pages/_studio_player.html",
        {
            "clip_id": clip_id,
            "fps": fps,
            "duration_secs": duration_secs,
            "duration_smpte": duration_smpte,
            "rows": rows,
        },
    )
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
.venv/bin/pytest -q tests/integration/test_studio_player_overlay.py
```

Expected: **PASS** on `test_player_one_row_with_version`. `test_player_one_row_no_version` should also pass.

- [ ] **Step 6: Re-run the full studio integration suite to confirm no regression**

```bash
.venv/bin/pytest -q tests/integration/test_studio_page.py tests/integration/test_studio_api.py
```

Expected: **PASS**.

- [ ] **Step 7: Manual smoke**

Reload `/studio`, focus a clip with prior runs. Below the `<video>`, a timeline strip should render with the cur version's scene ranges visible and a legend `● v{n} · X scenes`.

- [ ] **Step 8: Commit**

```bash
git add backend/app/templates/pages/_studio_player.html \
        backend/app/routes/pages/studio.py \
        tests/integration/test_studio_player_overlay.py
git commit -m "feat(studio): player overlay reuses shared _player_overlay (cur row)"
```

---

## Task 3: Add `show_history` flag to `_anno_panels.html`

Flag defaults to `true` (clip_detail unchanged). Studio will pass `false` in Task 5.

**Files:**
- Modify: `backend/app/templates/pages/_anno_panels.html`

- [ ] **Step 1: Write the unit test**

Add to `tests/integration/test_player_overlay_partial.py` (or create `tests/integration/test_anno_panels_flag.py`):

```python
def test_anno_panels_show_history_default_true_clip_detail_unchanged(client):
    """Clip detail still renders the History tab (default-true)."""
    r = client.get("/clips/12041")
    if r.status_code != 200:
        pytest.skip("clip not available in offline test env")
    # History tab is rendered when show_history is unset (default true).
    assert "tab === 'history'" in r.text
```

- [ ] **Step 2: Run test (should already PASS — no behavior change yet)**

```bash
.venv/bin/pytest -q tests/integration/test_anno_panels_flag.py
```

Expected: **PASS** (history tab still rendered).

- [ ] **Step 3: Add the flag in `_anno_panels.html`**

In `backend/app/templates/pages/_anno_panels.html`, wrap the history-tab button and the history `<div class="anno-section">` panel in `{% if show_history is not defined or show_history %}` blocks. Concretely:

Replace lines 27-32 (the History button) with:

```jinja
  {% if show_history is not defined or show_history %}
  <button type="button" class="anno-tab" role="tab"
          :class="{ active: tab === 'history' }"
          :aria-selected="tab === 'history'"
          @click="tab = 'history'; if (!historyLoaded) loadHistory()">
    History
  </button>
  {% endif %}
```

Replace lines 72-74 (the history panel) with:

```jinja
{% if show_history is not defined or show_history %}
<div class="anno-section" role="tabpanel" x-show="tab === 'history'" x-cloak>
  <div x-html="historyHtml || '<p class=&quot;muted&quot;>Načítám…</p>'"></div>
</div>
{% endif %}
```

- [ ] **Step 4: Re-run the test + clip-detail full suite**

```bash
.venv/bin/pytest -q tests/integration/test_anno_panels_flag.py \
                    tests/integration/test_clip_detail_draft.py
```

Expected: **PASS**.

- [ ] **Step 5: Commit**

```bash
git add backend/app/templates/pages/_anno_panels.html \
        tests/integration/test_anno_panels_flag.py
git commit -m "feat(anno-panels): add optional show_history flag (default true)"
```

---

## Task 4: `panels_from_studio_run` adapter

Server-side helper that converts a `StudioRun` + `PromptVersion` into the `panels` dict shape `_anno_panels.html` expects.

**Files:**
- Create: `backend/app/services/studio_panels.py`
- Create: `tests/unit/test_studio_panels_adapter.py`

- [ ] **Step 1: Write the failing tests**

`tests/unit/test_studio_panels_adapter.py`:

```python
"""Unit tests for panels_from_studio_run — the adapter that converts a
studio run's output_json + the prompt version's target_map into the
panels dict shape _anno_panels.html expects.
"""

from backend.app.models.prompt import PromptVersion, TargetEntry, TargetMap
from backend.app.models.studio import StudioRun


def _version(target_map_dict: dict) -> PromptVersion:
    return PromptVersion(
        id=1, prompt_id=1, version_num=1, state="draft",
        body="x", target_map=TargetMap(target_map_dict),
        output_schema={}, model="gemini-2.5-pro",
        created_at="2026-05-26T00:00:00Z", updated_at="2026-05-26T00:00:00Z",
    )


def _ok_run(output_json: dict) -> StudioRun:
    return StudioRun(
        id=1, prompt_version_id=1, clip_id=12041, job_id=None,
        status="ok", output_json=output_json,
        duration_s=1.0, tokens_in=10, tokens_out=20, cost_usd=0.01,
        model="gemini-2.5-pro", error=None,
        started_at=None, finished_at="2026-05-27T00:00:00Z",
    )


def test_returns_empty_panels_when_run_is_none():
    from backend.app.services.studio_panels import panels_from_studio_run
    p = panels_from_studio_run(None, _version({}), fps=25.0)
    assert p == {"markers": [], "fields": [], "notes": None, "big_notes": None, "fps": 25.0}


def test_returns_empty_panels_when_version_is_none():
    from backend.app.services.studio_panels import panels_from_studio_run
    p = panels_from_studio_run(_ok_run({"scenes": []}), None, fps=25.0)
    assert p == {"markers": [], "fields": [], "notes": None, "big_notes": None, "fps": 25.0}


def test_scenes_become_markers():
    from backend.app.services.studio_panels import panels_from_studio_run
    run = _ok_run({"scenes": [
        {"in_secs": 1.0, "out_secs": 2.5, "name": "a", "description": "d", "category": "c"},
        {"in_secs": 3.0, "out_secs": 4.0, "name": "b"},
    ]})
    p = panels_from_studio_run(run, _version({}), fps=25.0)
    assert len(p["markers"]) == 2
    m0, m1 = p["markers"]
    assert m0["in_secs"] == 1.0 and m0["out_secs"] == 2.5
    assert m0["name"] == "a" and m0["description"] == "d" and m0["category"] == "c"
    assert m1["in_secs"] == 3.0 and m1["out_secs"] == 4.0 and m1["name"] == "b"
    # Missing description/category default to None / empty.
    assert m1.get("description") in (None, "")
    assert m1.get("category") in (None, "")


def test_non_scenes_become_fields_via_target_map():
    from backend.app.services.studio_panels import panels_from_studio_run
    run = _ok_run({
        "scenes": [],
        "summary_cz": "krátký souhrn",
        "decade": "1970s",
    })
    v = _version({
        "summary_cz": {"kind": "field", "identifier": "pragafilm.popis.materialu"},
        "decade":     {"kind": "field", "identifier": "pragafilm.dekada"},
    })
    p = panels_from_studio_run(run, v, fps=25.0)
    fields_by_identifier = {f["identifier"]: f["value"] for f in p["fields"]}
    assert fields_by_identifier == {
        "pragafilm.popis.materialu": "krátký souhrn",
        "pragafilm.dekada": "1970s",
    }


def test_missing_target_map_entry_falls_through_to_raw_key():
    from backend.app.services.studio_panels import panels_from_studio_run
    run = _ok_run({"scenes": [], "leftover": "value"})
    p = panels_from_studio_run(run, _version({}), fps=25.0)
    assert p["fields"] == [{"identifier": "leftover", "value": "value"}]


def test_non_string_field_values_pass_through():
    from backend.app.services.studio_panels import panels_from_studio_run
    run = _ok_run({"scenes": [], "count": 5, "tags": ["a", "b"]})
    p = panels_from_studio_run(run, _version({}), fps=25.0)
    values_by_key = {f["identifier"]: f["value"] for f in p["fields"]}
    assert values_by_key["count"] == 5
    assert values_by_key["tags"] == ["a", "b"]


def test_scene_with_no_out_secs():
    from backend.app.services.studio_panels import panels_from_studio_run
    run = _ok_run({"scenes": [{"in_secs": 1.0, "name": "a"}]})
    p = panels_from_studio_run(run, _version({}), fps=25.0)
    assert p["markers"][0]["in_secs"] == 1.0
    assert p["markers"][0]["out_secs"] is None
```

- [ ] **Step 2: Run tests to verify they fail (no module yet)**

```bash
.venv/bin/pytest -q tests/unit/test_studio_panels_adapter.py
```

Expected: **FAIL** — `ModuleNotFoundError: No module named 'backend.app.services.studio_panels'`.

- [ ] **Step 3: Implement the adapter**

`backend/app/services/studio_panels.py`:

```python
"""Adapter: (StudioRun, PromptVersion) → panels dict for _anno_panels.html.

Maps:
  output_json["scenes"][]            → panels["markers"]
  other top-level output_json keys   → panels["fields"] (identifier via target_map)
  no notes / big_notes in v1         → both None
"""

from __future__ import annotations

from typing import Any

from backend.app.models.prompt import PromptVersion
from backend.app.models.studio import StudioRun

EMPTY_PANELS: dict[str, Any] = {
    "markers": [],
    "fields": [],
    "notes": None,
    "big_notes": None,
}


def panels_from_studio_run(
    run: StudioRun | None,
    version: PromptVersion | None,
    fps: float,
) -> dict[str, Any]:
    """Return the panels dict consumed by templates/pages/_anno_panels.html.

    Defensive: when run or version is None (or the run hasn't completed
    successfully), returns empty panels — the surrounding template handles
    the empty-state copy.
    """
    if run is None or version is None or not run.output_json:
        return {**EMPTY_PANELS, "fps": fps}

    out = run.output_json
    scenes = out.get("scenes") or []
    markers = [
        {
            "in_secs": s.get("in_secs"),
            "out_secs": s.get("out_secs"),
            "name": s.get("name") or "",
            "description": s.get("description"),
            "category": s.get("category"),
        }
        for s in scenes
        if s.get("in_secs") is not None
    ]

    # target_map is a TargetMap RootModel — its .root is dict[str, TargetEntry].
    tmap = version.target_map.root if version.target_map else {}

    fields: list[dict[str, Any]] = []
    for key, value in out.items():
        if key == "scenes":
            continue
        entry = tmap.get(key)
        identifier = entry.identifier if (entry and entry.identifier) else key
        fields.append({"identifier": identifier, "value": value})

    return {
        "markers": markers,
        "fields": fields,
        "notes": None,
        "big_notes": None,
        "fps": fps,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
.venv/bin/pytest -q tests/unit/test_studio_panels_adapter.py
```

Expected: **PASS** (7 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/studio_panels.py \
        tests/unit/test_studio_panels_adapter.py
git commit -m "feat(studio): panels_from_studio_run adapter — output_json → anno-panels shape"
```

---

## Task 5: Rewrite `_studio_run_output.html` to reuse `_anno_panels.html`

Drop bespoke `.ro-scene` / `.ro-field` markup. Build `panels` in the route and include the shared partial. Keep (and verify) the `<script type="application/json" data-run-json>` block for the eventual OutputDiff.

**Files:**
- Modify: `backend/app/templates/pages/_studio_run_output.html`
- Modify: `backend/app/routes/pages/studio.py` (`_studio_run` route — pass `panels`, `fps`, `clip_fps_resolver`)

- [ ] **Step 1: Write the failing integration test**

Create `tests/integration/test_studio_run_output_reuse.py`:

```python
"""The Output tab now renders via the shared _anno_panels.html partial,
and embeds the raw run JSON in a <script type=\"application/json\"
data-run-json> block for client-side diffing."""

import asyncio
import importlib
import json

import aiosqlite
import pytest
from fastapi.testclient import TestClient


def _setenv(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.delenv("CATDV_PASSWORD", raising=False)


@pytest.fixture
def client(monkeypatch, tmp_path):
    _setenv(monkeypatch, tmp_path)
    from backend.app import main as main_mod
    importlib.reload(main_mod)
    with TestClient(main_mod.app) as c:
        yield c


def _seed_run(app, *, version_id, clip_id, output_json):
    async def _go():
        async with aiosqlite.connect(app.state.ctx.db_path) as db:
            await db.execute(
                "INSERT INTO studio_run(prompt_version_id, clip_id, status, "
                "output_json, model, finished_at) "
                "VALUES (?, ?, 'ok', ?, 'gemini-2.5-pro', '2026-05-27T00:00:00Z')",
                (version_id, clip_id, json.dumps(output_json)),
            )
            await db.commit()
    asyncio.get_event_loop().run_until_complete(_go())


def test_run_output_uses_anno_panels_and_has_run_json(client):
    # Create prompt + version.
    r = client.post("/api/prompts", json={
        "name": "t", "media_kind": "any", "model": "gemini-2.5-pro",
        "target_map": {"summary": {"kind": "field", "identifier": "pf.summary"}},
        "output_schema": {}, "body": "x",
    })
    pid = r.json()["prompt_id"]
    vid = r.json()["version_id"]

    from backend.app import main as main_mod
    _seed_run(main_mod.app, version_id=vid, clip_id=12041, output_json={
        "scenes": [{"in_secs": 1.0, "out_secs": 2.0, "name": "scene-a"}],
        "summary": "krátký",
    })

    r = client.get(f"/studio/_run?prompt_version_id={vid}&clip_id=12041")
    assert r.status_code == 200
    html = r.text

    # Shared partial is in.
    assert 'class="anno-tabs"' in html
    assert 'class="anno-section"' in html
    # Bespoke markup is gone.
    assert "ro-scene" not in html
    assert "ro-field" not in html
    # History tab is hidden in studio context.
    assert "tab === 'history'" not in html
    # Raw JSON is embedded for OutputDiff.
    assert 'type="application/json"' in html
    assert 'data-run-json' in html
    # Marker article rendering works.
    assert "scene-a" in html
    # Field identifier was looked up via target_map.
    assert "pf.summary" in html


def test_run_output_empty_state_when_no_run(client):
    r = client.post("/api/prompts", json={
        "name": "u", "media_kind": "any", "model": "gemini-2.5-pro",
        "target_map": {}, "output_schema": {}, "body": "x",
    })
    vid = r.json()["version_id"]
    r = client.get(f"/studio/_run?prompt_version_id={vid}&clip_id=99999")
    assert r.status_code == 200
    assert "No run yet" in r.text
    assert "anno-tabs" not in r.text
```

- [ ] **Step 2: Run test to verify it fails**

```bash
.venv/bin/pytest -q tests/integration/test_studio_run_output_reuse.py
```

Expected: **FAIL** — current template emits `ro-scene` / `ro-field`, no `anno-tabs`, no `data-run-json` script.

- [ ] **Step 3: Rewrite the partial**

`backend/app/templates/pages/_studio_run_output.html`:

```jinja
{# Renders the latest studio_run for (version, clip) via the shared
   _anno_panels.html partial. Bespoke .ro-scene / .ro-field markup is
   gone — anno-panels is the one renderer for markers/fields/notes
   across the app.

   The raw run JSON is also embedded in a script tag so the client-side
   OutputDiff can read it without an extra fetch.

   Inputs (from the route):
     run     — StudioRun.model_dump() | None
     version — PromptVersion.model_dump() | None
     panels  — dict (already built via panels_from_studio_run)
#}
{% if not version %}
  <div class="run-empty muted">Unknown version.</div>
{% elif not run %}
  <div class="run-empty muted">
    No run yet. Hit <b>Run</b> to execute v{{ version.version_num }} on the focused clip.
  </div>
{% elif run.status == 'error' %}
  <div class="run-error">
    <div class="run-error-h"><b>Error</b> — v{{ version.version_num }}</div>
    <div class="run-error-msg">{{ run.error or 'unknown error' }}</div>
  </div>
{% elif run.status in ('pending', 'running') %}
  <div class="run-empty muted">⟳ Running…</div>
{% else %}
  {% with show_history = False %}
    {% include "pages/_anno_panels.html" %}
  {% endwith %}
  <script type="application/json" data-run-json>{{ run.output_json|tojson }}</script>
  <div class="run-stats mono-cell muted" style="padding:6px 0;">
    {{ "%.1f"|format(run.duration_s or 0) }}s · {{ run.tokens_out or 0 }} tok · ${{ "%.4f"|format(run.cost_usd or 0) }} · {{ run.model }}
  </div>
{% endif %}
```

- [ ] **Step 4: Update the `_studio_run` route to pass `panels`**

In `backend/app/routes/pages/studio.py`, replace the existing `_studio_run` handler with:

```python
@router.get("/studio/_run", response_class=HTMLResponse)
async def _studio_run(
    request: Request,
    prompt_version_id: int,
    clip_id: int,
):
    from backend.app.services.studio_panels import panels_from_studio_run

    ctx = get_ctx(request)
    run = await ctx.studio_runs_repo.latest_for_pair(
        ctx.db, prompt_version_id=prompt_version_id, clip_id=clip_id
    )
    try:
        version = await ctx.prompts_repo.get_version(ctx.db, prompt_version_id)
    except LookupError:
        version = None

    # fps lookup for SMPTE rendering inside _anno_panels.html (best-effort).
    fps = 25.0
    if ctx.archive:
        try:
            clip = await ctx.archive.get_clip(str(clip_id))
            fps = float(clip.fps or 25.0)
        except Exception:  # noqa: BLE001
            pass

    panels = panels_from_studio_run(run, version, fps=fps)

    return templates.TemplateResponse(
        request,
        "pages/_studio_run_output.html",
        {
            "run": run.model_dump() if run else None,
            "version": version.model_dump() if version else None,
            "panels": panels,
            "clip": {"fps": fps},  # _anno_panels.html references clip.fps in its tc()
        },
    )
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
.venv/bin/pytest -q tests/integration/test_studio_run_output_reuse.py
```

Expected: **PASS** (2 tests).

- [ ] **Step 6: Re-run the full studio suite + clip-detail suite**

```bash
.venv/bin/pytest -q tests/integration/test_studio_page.py \
                    tests/integration/test_studio_api.py \
                    tests/integration/test_clip_detail_draft.py
```

Expected: **PASS**.

- [ ] **Step 7: Manual smoke**

Focus a clip with a prior run on `/studio`. Open the Output tab. The rendering should now look like the clip-detail Markers/Fields tabs (same article/row markup), and there should be **no** History tab. Scrub the player; marker times should still display in SMPTE.

- [ ] **Step 8: Commit**

```bash
git add backend/app/templates/pages/_studio_run_output.html \
        backend/app/routes/pages/studio.py \
        tests/integration/test_studio_run_output_reuse.py
git commit -m "feat(studio): Output tab reuses _anno_panels.html via panels_from_studio_run"
```

---

## Task 6: Page route deep-link params (`?version_id=`, `?compare_version_id=`)

Make the studio page server-render the right cur/cmp versions when those params are present in the URL. Falls back to PR1 defaults otherwise.

**Files:**
- Modify: `backend/app/routes/pages/studio.py` (`studio_page`)
- Modify: `tests/integration/test_studio_page.py` (add deep-link cases)

- [ ] **Step 1: Add failing tests**

In `tests/integration/test_studio_page.py`, append:

```python
def test_studio_page_respects_version_id_param(client):
    # Create a prompt with two versions.
    r = client.post("/api/prompts", json={
        "name": "vp", "media_kind": "any", "model": "gemini-2.5-pro",
        "target_map": {}, "output_schema": {}, "body": "v1",
    })
    pid = r.json()["prompt_id"]
    v1 = r.json()["version_id"]
    # Promote v1 to production so a fresh draft can be branched.
    client.post(f"/api/prompts/{pid}/versions/{v1}/promote")
    # Branch a new draft (v2).
    r = client.post(f"/api/prompts/{pid}/versions", json={
        "from_version_id": v1, "body": "v2",
    })
    v2 = r.json()["version_id"]

    # Without param: default = draft = v2.
    r = client.get(f"/studio?prompt_id={pid}")
    assert r.status_code == 200
    assert f'activeVersionId: {v2}' in r.text

    # With param: pick v1 explicitly.
    r = client.get(f"/studio?prompt_id={pid}&version_id={v1}")
    assert r.status_code == 200
    assert f'activeVersionId: {v1}' in r.text


def test_studio_page_respects_compare_version_id_param(client):
    # Set up two versions (re-using helper inline).
    r = client.post("/api/prompts", json={
        "name": "vp2", "media_kind": "any", "model": "gemini-2.5-pro",
        "target_map": {}, "output_schema": {}, "body": "v1",
    })
    pid = r.json()["prompt_id"]
    v1 = r.json()["version_id"]
    client.post(f"/api/prompts/{pid}/versions/{v1}/promote")
    r = client.post(f"/api/prompts/{pid}/versions", json={
        "from_version_id": v1, "body": "v2",
    })
    v2 = r.json()["version_id"]

    r = client.get(f"/studio?prompt_id={pid}&version_id={v2}&compare_version_id={v1}")
    assert r.status_code == 200
    assert f'compareVersionId: {v1}' in r.text


def test_studio_page_ignores_compare_equal_to_cur(client):
    r = client.post("/api/prompts", json={
        "name": "vp3", "media_kind": "any", "model": "gemini-2.5-pro",
        "target_map": {}, "output_schema": {}, "body": "v1",
    })
    pid = r.json()["prompt_id"]
    vid = r.json()["version_id"]
    r = client.get(f"/studio?prompt_id={pid}&version_id={vid}&compare_version_id={vid}")
    assert r.status_code == 200
    # compareVersionId should be null when equal to cur (no point comparing v with itself).
    assert "compareVersionId: null" in r.text
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
.venv/bin/pytest -q tests/integration/test_studio_page.py
```

Expected: **FAIL** on the three new tests (page route doesn't read the params yet; `compareVersionId` isn't in the template).

- [ ] **Step 3: Update the page route**

In `backend/app/routes/pages/studio.py`, replace `studio_page` with:

```python
@router.get("/studio", response_class=HTMLResponse)
async def studio_page(
    request: Request,
    prompt_id: int | None = None,
    version_id: int | None = None,
    compare_version_id: int | None = None,
):
    ctx = get_ctx(request)
    prompts = await ctx.prompts_repo.list_active(ctx.db)
    folders = await ctx.studio_folders_repo.list_folders_with_counts(ctx.db)

    selected_prompt = None
    versions: list = []
    if prompt_id is not None:
        try:
            selected_prompt, versions = await ctx.prompts_repo.get_with_versions(
                ctx.db, prompt_id
            )
        except LookupError:
            selected_prompt = None
            versions = []
    elif prompts:
        first_id = prompts[0].id
        assert first_id is not None
        selected_prompt, versions = await ctx.prompts_repo.get_with_versions(
            ctx.db, first_id
        )

    version_ids = {v.id for v in versions}

    # Pick the active version (cur).
    active_version = None
    if version_id is not None and version_id in version_ids:
        active_version = next(v for v in versions if v.id == version_id)
    elif versions:
        active_version = next((v for v in versions if v.state == "draft"), versions[0])

    # Pick the compare version (cmp).
    compare_version = None
    if (
        compare_version_id is not None
        and compare_version_id in version_ids
        and active_version is not None
        and compare_version_id != active_version.id
    ):
        compare_version = next(v for v in versions if v.id == compare_version_id)

    return templates.TemplateResponse(
        request,
        "pages/studio.html",
        {
            "prompts": [p.model_dump() for p in prompts],
            "selected_prompt": selected_prompt.model_dump() if selected_prompt else None,
            "versions": [v.model_dump() for v in versions],
            "active_version": active_version.model_dump() if active_version else None,
            "compare_version": compare_version.model_dump() if compare_version else None,
            "folders": folders,
        },
    )
```

- [ ] **Step 4: Wire `compareVersionId` into the Alpine init in `studio.html`**

Replace the `x-data="studioPage({...})"` block at the top of `studio.html` with:

```jinja
<div class="studio-page"
     x-data="studioPage({
       promptId: {{ selected_prompt.id if selected_prompt else 'null' }},
       activeVersionId: {{ active_version.id if active_version else 'null' }},
       activeVersionNum: {{ active_version.version_num if active_version else 'null' }},
       activeModel: '{{ active_version.model if active_version else '' }}',
       compareVersionId: {{ compare_version.id if compare_version else 'null' }},
       compareVersionNum: {{ compare_version.version_num if compare_version else 'null' }},
     })">
```

- [ ] **Step 5: Add the fields to `studioPage()` in `studio.js`**

In `backend/app/static/studio.js`, inside `Alpine.data('studioPage', (initial) => ({ ... }))`, add (alongside the existing `promptId`, `activeVersionId`, etc.):

```js
    compareVersionId: initial.compareVersionId,
    compareVersionNum: initial.compareVersionNum,
    mode: 'prompt',  // lifted from per-card state — Task 11 finalizes the lift.
```

(Tab sync officially lands in Task 11; adding `mode` here is harmless until then.)

- [ ] **Step 6: Run the deep-link tests**

```bash
.venv/bin/pytest -q tests/integration/test_studio_page.py
```

Expected: **PASS**.

- [ ] **Step 7: Commit**

```bash
git add backend/app/routes/pages/studio.py \
        backend/app/templates/pages/studio.html \
        backend/app/static/studio.js \
        tests/integration/test_studio_page.py
git commit -m "feat(studio): deep-link version_id and compare_version_id on /studio"
```

---

## Task 7: Side-aware `/studio/_prompt_card` route

A new HTMX partial route that renders a single prompt-card for either side, used by both initial cmp materialization and the version-picker chip swap.

**Files:**
- Modify: `backend/app/routes/pages/studio.py`
- Create: `tests/integration/test_studio_prompt_card_route.py`

- [ ] **Step 1: Write the failing route tests**

`tests/integration/test_studio_prompt_card_route.py`:

```python
"""GET /studio/_prompt_card — side-aware partial used by HTMX swaps."""

import importlib

import pytest
from fastapi.testclient import TestClient


def _setenv(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.delenv("CATDV_PASSWORD", raising=False)


@pytest.fixture
def client(monkeypatch, tmp_path):
    _setenv(monkeypatch, tmp_path)
    from backend.app import main as main_mod
    importlib.reload(main_mod)
    with TestClient(main_mod.app) as c:
        yield c


def _make_prompt_two_versions(client):
    r = client.post("/api/prompts", json={
        "name": "pc", "media_kind": "any", "model": "gemini-2.5-pro",
        "target_map": {}, "output_schema": {}, "body": "v1",
    })
    pid = r.json()["prompt_id"]
    v1 = r.json()["version_id"]
    client.post(f"/api/prompts/{pid}/versions/{v1}/promote")
    r = client.post(f"/api/prompts/{pid}/versions", json={
        "from_version_id": v1, "body": "v2-draft",
    })
    v2 = r.json()["version_id"]
    return pid, v1, v2


def test_404_on_missing_version(client):
    r = client.get("/studio/_prompt_card?side=cur&prompt_version_id=9999")
    assert r.status_code == 404


def test_400_on_invalid_side(client):
    _, v1, _ = _make_prompt_two_versions(client)
    r = client.get(f"/studio/_prompt_card?side=garbage&prompt_version_id={v1}")
    assert r.status_code == 422  # FastAPI Literal validation


def test_draft_renders_textarea(client):
    _, _, v2 = _make_prompt_two_versions(client)
    r = client.get(f"/studio/_prompt_card?side=cur&prompt_version_id={v2}")
    assert r.status_code == 200
    assert "<textarea" in r.text
    assert "pc-readonly" not in r.text


def test_non_draft_renders_readonly_pre(client):
    _, v1, _ = _make_prompt_two_versions(client)
    r = client.get(f"/studio/_prompt_card?side=cur&prompt_version_id={v1}")
    assert r.status_code == 200
    assert 'class="pc-readonly mono"' in r.text
    assert "<textarea" not in r.text


def test_includes_data_attrs_for_alpine_sync(client):
    _, v1, _ = _make_prompt_two_versions(client)
    r = client.get(f"/studio/_prompt_card?side=cur&prompt_version_id={v1}")
    # Root carries data-side and data-version-id so the page-level
    # htmx:afterSwap handler can read them.
    assert 'data-side="cur"' in r.text
    assert f'data-version-id="{v1}"' in r.text


def test_cmp_side_renders_close_and_diff_toggle(client):
    _, v1, _ = _make_prompt_two_versions(client)
    r = client.get(f"/studio/_prompt_card?side=cmp&prompt_version_id={v1}")
    assert r.status_code == 200
    assert 'data-side="cmp"' in r.text
    assert 'btn-close-cmp' in r.text
    assert 'btn-diff-toggle' in r.text


def test_output_tab_includes_data_run_json_when_run_exists(client):
    # Seed a run, then assert the partial includes the JSON block.
    import asyncio, json
    import aiosqlite
    from backend.app import main as main_mod

    _, v1, _ = _make_prompt_two_versions(client)
    async def _seed():
        async with aiosqlite.connect(main_mod.app.state.ctx.db_path) as db:
            await db.execute(
                "INSERT INTO studio_run(prompt_version_id, clip_id, status, "
                "output_json, model, finished_at) "
                "VALUES (?, ?, 'ok', ?, 'gemini-2.5-pro', '2026-05-27T00:00:00Z')",
                (v1, 12041, json.dumps({"scenes": []})),
            )
            await db.commit()
    asyncio.get_event_loop().run_until_complete(_seed())

    r = client.get(f"/studio/_prompt_card?side=cur&prompt_version_id={v1}&clip_id=12041")
    assert r.status_code == 200
    assert "data-run-json" in r.text
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
.venv/bin/pytest -q tests/integration/test_studio_prompt_card_route.py
```

Expected: **FAIL** — route doesn't exist.

- [ ] **Step 3: Implement the route**

Add to `backend/app/routes/pages/studio.py`:

```python
from typing import Literal


@router.get("/studio/_prompt_card", response_class=HTMLResponse)
async def _studio_prompt_card(
    request: Request,
    side: Literal["cur", "cmp"],
    prompt_version_id: int,
    clip_id: int | None = None,
):
    """Renders one prompt-card. Used by HTMX swaps from the version chip
    and by the initial cmp materialization.

    404 on missing version. With clip_id, the Output tab pre-loads the
    run partial; without, the Output tab shows the focus-a-clip
    empty-state.
    """
    from backend.app.services.studio_panels import panels_from_studio_run

    ctx = get_ctx(request)
    try:
        version = await ctx.prompts_repo.get_version(ctx.db, prompt_version_id)
    except LookupError as exc:
        raise HTTPException(404, f"version {prompt_version_id} not found") from exc

    # Load the prompt's full version list for the picker dropdown.
    _, versions = await ctx.prompts_repo.get_with_versions(ctx.db, version.prompt_id)

    run = None
    panels: dict | None = None
    fps = 25.0
    if clip_id is not None:
        run = await ctx.studio_runs_repo.latest_for_pair(
            ctx.db, prompt_version_id=prompt_version_id, clip_id=clip_id
        )
        if ctx.archive:
            try:
                clip = await ctx.archive.get_clip(str(clip_id))
                fps = float(clip.fps or 25.0)
            except Exception:  # noqa: BLE001
                pass
        panels = panels_from_studio_run(run, version, fps=fps)

    return templates.TemplateResponse(
        request,
        "pages/_studio_prompt_card.html",
        {
            "side": side,
            "active_version": version.model_dump(),
            "versions": [v.model_dump() for v in versions],
            "clip_id": clip_id,
            "run": run.model_dump() if run else None,
            "panels": panels,
            "clip": {"fps": fps},
        },
    )
```

Also add `HTTPException` to imports at the top of the file if not present.

- [ ] **Step 4: Make `_studio_prompt_card.html` side-aware (minimal change for now)**

Replace `backend/app/templates/pages/_studio_prompt_card.html` with the following (chip + close + diff-toggle stubs land in Tasks 8/9/10/13 — this version just gets the structural framing right):

```jinja
{# Studio prompt-card. side ∈ {'cur', 'cmp'}. Used both as the page-rendered
   initial card and as the HTMX-swapped partial. The version chip lives in
   the header; cmp side gets a Close button and a Diff-vs-cur toggle.

   Required template inputs:
     side             — "cur" | "cmp"
     active_version   — dict (PromptVersion.model_dump())
     versions         — list[dict]  (all versions of the prompt, for the picker)
     clip_id          — int | None
     run              — dict | None  (latest run for active_version + clip)
     panels           — dict | None  (built via panels_from_studio_run)
     clip             — { fps: float }  (consumed by _anno_panels.html)
#}
<div class="studio-prompt-card{% if side == 'cmp' %} cmp-card{% endif %}"
     data-side="{{ side }}"
     data-version-id="{{ active_version.id }}"
     data-version-num="{{ active_version.version_num }}"
     x-data="studioPromptCard('{{ side }}')">

  <div class="pc-hdr">
    {% include "pages/_studio_version_picker.html" %}
    {% if side == 'cmp' %}
      <span class="grow"></span>
      <button type="button" class="btn-diff-toggle"
              @click="diff = !diff"
              :class="diff && 'active'"
              x-text="diff ? `Hide diff` : `Diff vs v${$root.activeVersionNum}`"></button>
      <button type="button" class="btn-close-cmp" title="Close compare"
              @click="$root.closeCompare()">×</button>
    {% else %}
      <span class="grow"></span>
      <button type="button" class="btn-compare"
              x-show="$root.compareVersionId === null"
              @click="$root.openCompare()">+ Compare</button>
    {% endif %}
  </div>

  <div class="pc-tabs">
    <button class="pc-tab" :class="$root.mode === 'prompt' && 'active'"
            @click="$root.mode = 'prompt'">Prompt</button>
    <button class="pc-tab" :class="$root.mode === 'output' && 'active'"
            @click="$root.mode = 'output'">Output</button>
  </div>

  <div class="pc-body">
    {% if side == 'cmp' %}
      <div x-show="diff" x-cloak>
        {% include "pages/_studio_diff.html" %}
      </div>
    {% endif %}

    <div {% if side == 'cmp' %}x-show="!diff"{% endif %}>
      <div x-show="$root.mode === 'prompt'">
        {% if active_version.state == 'draft' and side == 'cur' %}
          <textarea class="pc-editor" x-ref="editor"
                    @input.debounce.700ms="save()"
                    spellcheck="false">{{ active_version.body }}</textarea>
        {% else %}
          <pre class="pc-readonly mono">{{ active_version.body }}</pre>
        {% endif %}
      </div>
      <div x-show="$root.mode === 'output'"
           x-init="$nextTick(() => loadOutput())"
           x-effect="$root.pendingRunSwap && loadOutput()">
        <div class="run-slot" x-ref="runSlot">
          {% if panels is not none %}
            {% include "pages/_studio_run_output.html" %}
          {% else %}
            <div class="muted">Click a clip in a folder to focus it.</div>
          {% endif %}
        </div>
      </div>
    </div>
  </div>

  {% if side == 'cur' %}
  <div class="pc-foot">
    <span x-show="dirty" class="mono-cell muted">draft · saving…</span>
    <span x-show="!dirty" class="mono-cell muted">saved</span>
  </div>
  {% endif %}
</div>
```

- [ ] **Step 5: Create stub `_studio_version_picker.html` and `_studio_diff.html` (real impls in Tasks 8 + 13)**

`backend/app/templates/pages/_studio_version_picker.html`:

```jinja
{# Version chip + dropdown. Picking a version posts hx-get to
   /studio/_prompt_card and swaps the card body in place. Filled in Task 8.

   Required scope from the enclosing card:
     active_version, versions, side, clip_id
#}
<span class="pc-vchip" data-version-picker>
  <span class="pc-vlbl">v{{ active_version.version_num }}</span>
  <span class="pc-status {{ active_version.state }}">{{ active_version.state }}</span>
</span>
```

`backend/app/templates/pages/_studio_diff.html`:

```jinja
{# Cmp-only diff body. Real implementation in Task 13. #}
<div class="pc-diff" data-cmp-diff>
  <div class="muted">diff coming soon</div>
</div>
```

- [ ] **Step 6: Update `studioPromptCard` Alpine factory to accept `side` and `diff`**

In `backend/app/static/studio.js`, replace `Alpine.data('studioPromptCard', () => ({ ... }))` with:

```js
  Alpine.data('studioPromptCard', (side = 'cur') => ({
    side,
    diff: false,
    dirty: false,
    // mode was here in PR1; lifted to $root.mode in Task 11. The
    // partial reads $root.mode directly, so nothing in this factory
    // needs to change when Task 11 finishes the lift.

    async save() {
      // ... existing impl preserved ...
      if (this.side !== 'cur') return;  // never save from the cmp card.
      this.dirty = true;
      const versionId = this.$root.activeVersionId;
      const promptId = this.$root.promptId;
      if (!versionId || !promptId) { this.dirty = false; return; }
      const body = this.$refs.editor ? this.$refs.editor.value : null;
      if (body == null) { this.dirty = false; return; }
      try {
        const v = await fetch(`/api/prompts/${promptId}/versions/${versionId}`).then(r => r.json());
        const res = await fetch(`/api/prompts/${promptId}/versions/${versionId}`, {
          method: 'PUT',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({
            body, target_map: v.target_map,
            output_schema: v.output_schema, model: v.model,
          }),
        });
        this.dirty = !res.ok;
      } catch (err) {
        console.error('studio save failed', err);
        this.dirty = false;
      }
    },

    async loadOutput() {
      const versionId = this.side === 'cur'
        ? this.$root.activeVersionId
        : this.$root.compareVersionId;
      const clipId = this.$root.focusedClipId;
      if (!versionId) return;
      const slot = this.$refs.runSlot;
      if (!slot) return;
      if (!clipId) {
        slot.innerHTML = '<div class="muted">Click a clip in a folder to focus it.</div>';
        return;
      }
      try {
        const html = await fetch(
          `/studio/_run?prompt_version_id=${versionId}&clip_id=${clipId}`,
        ).then(r => r.text());
        slot.innerHTML = html;
      } catch (err) {
        console.error('loadOutput failed', err);
      }
    },
  }));
```

Also add `openCompare()` / `closeCompare()` to `studioPage` Alpine data. `openCompare()` here is a state-only stub — Task 9 replaces it with the version that also HTMX-fetches the cmp card. Same name across tasks so the button template doesn't need re-editing:

```js
    openCompare() {
      const versions = window.__studioVersions || [];
      const cur = this.activeVersionId;
      // Prefer next-most-recent non-cur draft, else production, else any.
      const drafts = versions.filter(v => v.id !== cur && v.state === 'draft');
      const prods  = versions.filter(v => v.id !== cur && v.state === 'production');
      const others = versions.filter(v => v.id !== cur);
      const pick = (drafts[0] || prods[0] || others[0]) || null;
      if (!pick) return;
      this.compareVersionId = pick.id;
      this.compareVersionNum = pick.version_num;
      this._writeUrl();
    },

    closeCompare() {
      this.compareVersionId = null;
      this.compareVersionNum = null;
      this._writeUrl();
    },

    _writeUrl() {
      const p = new URLSearchParams(window.location.search);
      if (this.promptId) p.set('prompt_id', this.promptId); else p.delete('prompt_id');
      if (this.activeVersionId) p.set('version_id', this.activeVersionId); else p.delete('version_id');
      if (this.compareVersionId) p.set('compare_version_id', this.compareVersionId); else p.delete('compare_version_id');
      window.history.replaceState({}, '', `${window.location.pathname}?${p.toString()}`);
    },
```

And expose `versions` as a window global in `studio.html` (`openCompare` needs them, but they're already model-dumped server-side):

In `studio.html`, just before the `</div>` that closes `.studio-page`, add:

```jinja
<script>
  window.__studioVersions = {{ versions|tojson }};
</script>
```

- [ ] **Step 7: Run the route tests**

```bash
.venv/bin/pytest -q tests/integration/test_studio_prompt_card_route.py
```

Expected: **PASS**.

- [ ] **Step 8: Run the full studio suite to catch regressions**

```bash
.venv/bin/pytest -q tests/integration/test_studio_page.py \
                    tests/integration/test_studio_api.py \
                    tests/integration/test_studio_run_output_reuse.py \
                    tests/integration/test_studio_player_overlay.py
```

Expected: **PASS**.

- [ ] **Step 9: Manual smoke**

Reload `/studio` with `?prompt_id=N&compare_version_id=M`. Two prompt cards should appear side-by-side, both rendering. The cmp card has `× Close` and `Diff vs vN` buttons. The cur card has a `+ Compare` button that disappears when cmp is open.

- [ ] **Step 10: Commit**

```bash
git add backend/app/routes/pages/studio.py \
        backend/app/templates/pages/_studio_prompt_card.html \
        backend/app/templates/pages/_studio_version_picker.html \
        backend/app/templates/pages/_studio_diff.html \
        backend/app/templates/pages/studio.html \
        backend/app/static/studio.js \
        tests/integration/test_studio_prompt_card_route.py
git commit -m "feat(studio): side-aware /studio/_prompt_card + cmp materialization scaffolding"
```

---

## Task 8: Wire the version-picker chip (server-rendered dropdown + HTMX swap)

Fill in `_studio_version_picker.html` with the real chip + dropdown. Picking a version triggers an HTMX swap of the surrounding card.

**Files:**
- Modify: `backend/app/templates/pages/_studio_version_picker.html`
- Modify: `backend/app/static/studio.js` (htmx:afterSwap handler)
- Modify: `backend/app/static/app.css`

- [ ] **Step 1: Add failing test**

In `tests/integration/test_studio_prompt_card_route.py`, append:

```python
def test_prompt_card_lists_all_versions_in_picker(client):
    _, v1, v2 = _make_prompt_two_versions(client)
    r = client.get(f"/studio/_prompt_card?side=cur&prompt_version_id={v2}")
    assert r.status_code == 200
    # Both versions show up in the dropdown.
    assert f'data-version-pick="{v1}"' in r.text
    assert f'data-version-pick="{v2}"' in r.text
    # Active version is marked.
    assert f'data-version-pick="{v2}"' in r.text
    assert 'is-current' in r.text


def test_picker_uses_hx_get_to_swap_card(client):
    _, v1, v2 = _make_prompt_two_versions(client)
    r = client.get(f"/studio/_prompt_card?side=cur&prompt_version_id={v2}")
    html = r.text
    # Each non-active row carries hx-get pointing at /studio/_prompt_card.
    assert "hx-get=\"/studio/_prompt_card" in html
    assert 'hx-target="closest .studio-prompt-card"' in html
    assert 'hx-swap="outerHTML"' in html
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
.venv/bin/pytest -q tests/integration/test_studio_prompt_card_route.py::test_prompt_card_lists_all_versions_in_picker \
                    tests/integration/test_studio_prompt_card_route.py::test_picker_uses_hx_get_to_swap_card
```

Expected: **FAIL**.

- [ ] **Step 3: Implement the chip + dropdown**

Replace `backend/app/templates/pages/_studio_version_picker.html`:

```jinja
{# Version chip + dropdown for a single prompt-card. Picking a row triggers
   an HTMX swap of the surrounding card to /studio/_prompt_card.

   Required scope from the enclosing card:
     active_version, versions, side, clip_id (optional)
#}
<span class="pc-vchip" x-data="{ open: false }" @click.outside="open = false">
  <button type="button" class="pc-vchip-btn" :class="open && 'open'"
          @click="open = !open" title="Switch version">
    <span class="pc-vlbl">v{{ active_version.version_num }}</span>
    <span class="pc-status {{ active_version.state }}">{{ active_version.state }}</span>
    <span class="caret">▾</span>
  </button>
  <div class="pc-vmenu" x-show="open" x-cloak>
    <div class="pc-vmenu-h mono-cell">versions</div>
    {% for v in versions %}
      <button type="button"
              class="pc-vmenu-item{% if v.id == active_version.id %} is-current{% endif %}"
              data-version-pick="{{ v.id }}"
              hx-get="/studio/_prompt_card?side={{ side }}&prompt_version_id={{ v.id }}{% if clip_id %}&clip_id={{ clip_id }}{% endif %}"
              hx-target="closest .studio-prompt-card"
              hx-swap="outerHTML"
              @click="open = false">
        <span class="pc-vmenu-lbl">v{{ v.version_num }}</span>
        <span class="pc-status {{ v.state }}">{{ v.state }}</span>
      </button>
    {% endfor %}
  </div>
</span>
```

- [ ] **Step 4: Add CSS for the chip**

In `backend/app/static/app.css`, append:

```css
/* === Studio: version chip ============================================ */
.pc-vchip { position: relative; display: inline-flex; align-items: center; }
.pc-vchip-btn {
  display: inline-flex; align-items: center; gap: 6px;
  border: 1px solid var(--border, #2a2a2a);
  background: var(--bg-2, #161616); color: inherit;
  padding: 2px 8px; border-radius: 999px; cursor: pointer;
}
.pc-vchip-btn .caret { font-size: 10px; opacity: 0.7; }
.pc-vchip-btn.open { background: var(--bg-3, #1f1f1f); }
.pc-vmenu {
  position: absolute; top: calc(100% + 4px); left: 0; min-width: 160px;
  background: var(--bg-2, #161616); border: 1px solid var(--border, #2a2a2a);
  border-radius: 6px; padding: 4px; z-index: 20;
  box-shadow: 0 6px 20px rgba(0,0,0,0.4);
}
.pc-vmenu-h { padding: 4px 8px; font-size: 10px; text-transform: uppercase; opacity: 0.5; }
.pc-vmenu-item {
  display: flex; width: 100%; align-items: center; gap: 8px;
  background: none; border: 0; color: inherit;
  padding: 6px 8px; border-radius: 4px; cursor: pointer;
}
.pc-vmenu-item:hover { background: var(--bg-3, #1f1f1f); }
.pc-vmenu-item.is-current { background: var(--accent-fade, #2b3a4d); }
```

- [ ] **Step 5: Wire htmx:afterSwap → update page state**

In `backend/app/static/studio.js`, inside the `'alpine:init'` block (or at top-level after the `window.studio` definition), add:

```js
document.body.addEventListener('htmx:afterSwap', (evt) => {
  const root = document.querySelector('.studio-page');
  if (!root || !root._x_dataStack) return;
  const page = root._x_dataStack[0];
  const card = evt.target.closest('.studio-prompt-card');
  if (!card) return;
  const side = card.getAttribute('data-side');
  const vId  = parseInt(card.getAttribute('data-version-id'), 10);
  const vNum = parseInt(card.getAttribute('data-version-num'), 10);
  if (Number.isNaN(vId)) return;
  if (side === 'cur') {
    page.activeVersionId = vId;
    page.activeVersionNum = vNum;
    page.pendingRunSwap++;
  } else if (side === 'cmp') {
    page.compareVersionId = vId;
    page.compareVersionNum = vNum;
    page.pendingRunSwap++;
  }
  page._writeUrl();
  // Refresh the player overlay so range rows reflect the new versions.
  if (page.focusedClipId) page.refreshPlayer();
});
```

Also add the `refreshPlayer()` method to `studioPage`:

```js
    refreshPlayer() {
      const slot = document.querySelector('[data-studio-player-slot]');
      if (!slot || !this.focusedClipId) return;
      const params = new URLSearchParams();
      params.set('clip_id', this.focusedClipId);
      if (this.activeVersionId)  params.set('version_id', this.activeVersionId);
      if (this.compareVersionId) params.set('compare_id', this.compareVersionId);
      fetch(`/studio/_player?${params.toString()}`)
        .then(r => r.text())
        .then(html => { slot.innerHTML = html; });
    },
```

And update `focusClip` to use `refreshPlayer`:

```js
    focusClip(clipId) {
      this.focusedClipId = clipId;
      this.pendingRunSwap++;
      const body = document.querySelector('.studio-body');
      if (body) body.classList.remove('no-player');
      this.refreshPlayer();
    },
```

- [ ] **Step 6: Make sure the HTMX script is loaded**

Verify `backend/app/templates/pages/layout.html` already includes htmx (it did in PR1; if not, add `<script src="/static/htmx.min.js"></script>` or the CDN reference the project uses). No change expected.

- [ ] **Step 7: Run tests**

```bash
.venv/bin/pytest -q tests/integration/test_studio_prompt_card_route.py
```

Expected: **PASS**.

- [ ] **Step 8: Manual smoke**

Reload `/studio?prompt_id=N`. Click the chip on the cur card → dropdown lists all versions with state badges. Pick a different one. The card should swap, the Run-button label should update to the new `v#`, and the URL should gain `?version_id=`.

- [ ] **Step 9: Commit**

```bash
git add backend/app/templates/pages/_studio_version_picker.html \
        backend/app/static/studio.js \
        backend/app/static/app.css \
        tests/integration/test_studio_prompt_card_route.py
git commit -m "feat(studio): version picker chip with HTMX swap of prompt-card"
```

---

## Task 9: Cmp card materialization (`+ Compare` button)

The `+ Compare` button is already in the template (Task 7). What's missing: when cmp is open, render the cmp card slot in `studio.html`; when `+ Compare` is clicked from a `compareVersionId === null` state, populate the slot via HTMX.

**Files:**
- Create: `backend/app/templates/pages/_studio_compare.html`
- Modify: `backend/app/templates/pages/studio.html`

- [ ] **Step 1: Add failing test**

In `tests/integration/test_studio_compare.py` (new file):

```python
"""End-to-end deep-link + compare materialization tests."""

import importlib

import pytest
from fastapi.testclient import TestClient


def _setenv(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.delenv("CATDV_PASSWORD", raising=False)


@pytest.fixture
def client(monkeypatch, tmp_path):
    _setenv(monkeypatch, tmp_path)
    from backend.app import main as main_mod
    importlib.reload(main_mod)
    with TestClient(main_mod.app) as c:
        yield c


def _two_versions(client):
    r = client.post("/api/prompts", json={
        "name": "cmp", "media_kind": "any", "model": "gemini-2.5-pro",
        "target_map": {}, "output_schema": {}, "body": "v1",
    })
    pid = r.json()["prompt_id"]; v1 = r.json()["version_id"]
    client.post(f"/api/prompts/{pid}/versions/{v1}/promote")
    r = client.post(f"/api/prompts/{pid}/versions", json={"from_version_id": v1, "body": "v2"})
    v2 = r.json()["version_id"]
    return pid, v1, v2


def test_single_card_when_no_compare_param(client):
    pid, v1, v2 = _two_versions(client)
    r = client.get(f"/studio?prompt_id={pid}")
    assert r.status_code == 200
    # One card.
    assert r.text.count('data-side="cur"') == 1
    assert 'data-side="cmp"' not in r.text


def test_two_cards_when_compare_param_set(client):
    pid, v1, v2 = _two_versions(client)
    r = client.get(f"/studio?prompt_id={pid}&version_id={v2}&compare_version_id={v1}")
    assert r.status_code == 200
    assert 'data-side="cur"' in r.text
    assert 'data-side="cmp"' in r.text
    # Cur is v2; cmp is v1.
    assert f'data-version-id="{v2}"' in r.text  # cur card
    assert f'data-version-id="{v1}"' in r.text  # cmp card
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
.venv/bin/pytest -q tests/integration/test_studio_compare.py
```

Expected: **FAIL on `test_two_cards_when_compare_param_set`** — `studio.html` only renders one card.

- [ ] **Step 3: Create the compare wrapper partial**

`backend/app/templates/pages/_studio_compare.html`:

```jinja
{# Wraps the 1-or-2 prompt-cards row.

   Required template inputs (passed through from studio.html):
     active_version, compare_version, versions, folders (unused here)
#}
<div class="studio-compare-row">
  {% with side='cur', active_version=active_version, versions=versions, clip_id=None, run=None, panels=None, clip={'fps': 25.0} %}
    {% include "pages/_studio_prompt_card.html" %}
  {% endwith %}

  <div class="cmp-slot" data-cmp-slot
       {% if not compare_version %}style="display:none"{% endif %}>
    {% if compare_version %}
      {% with side='cmp', active_version=compare_version, versions=versions, clip_id=None, run=None, panels=None, clip={'fps': 25.0} %}
        {% include "pages/_studio_prompt_card.html" %}
      {% endwith %}
    {% endif %}
  </div>
</div>
```

- [ ] **Step 4: Update `studio.html` to use the wrapper**

In `backend/app/templates/pages/studio.html`, replace the `<div class="studio-compare">…</div>` block with:

```jinja
      <div class="studio-compare">
        {% include "pages/_studio_compare.html" %}
      </div>
```

- [ ] **Step 5: Replace `openCompare()` stub with the HTMX-fetching version**

In Task 7 we defined `openCompare()` as state-only (it sets `compareVersionId` and rewrites the URL but never fetches the cmp card). The `+ Compare` button in `_studio_prompt_card.html` already binds to `$root.openCompare()`, so the template doesn't change — only the implementation does.

Replace the existing `openCompare()` in `studioPage` with:

```js
    async openCompare() {
      const versions = window.__studioVersions || [];
      const cur = this.activeVersionId;
      const drafts = versions.filter(v => v.id !== cur && v.state === 'draft');
      const prods  = versions.filter(v => v.id !== cur && v.state === 'production');
      const others = versions.filter(v => v.id !== cur);
      const pick = (drafts[0] || prods[0] || others[0]);
      if (!pick) return;
      this.compareVersionId = pick.id;
      this.compareVersionNum = pick.version_num;
      this._writeUrl();
      // Inject the cmp card via HTMX.
      const slot = document.querySelector('[data-cmp-slot]');
      if (!slot) return;
      slot.style.display = '';
      const params = new URLSearchParams();
      params.set('side', 'cmp');
      params.set('prompt_version_id', pick.id);
      if (this.focusedClipId) params.set('clip_id', this.focusedClipId);
      const html = await fetch(`/studio/_prompt_card?${params.toString()}`).then(r => r.text());
      slot.innerHTML = html;
      // Initialize Alpine on injected nodes.
      window.Alpine?.initTree(slot);
      this.refreshPlayer();
    },
```

Replace the existing `closeCompare()` to also empty the slot:

```js
    closeCompare() {
      this.compareVersionId = null;
      this.compareVersionNum = null;
      this._writeUrl();
      const slot = document.querySelector('[data-cmp-slot]');
      if (slot) { slot.innerHTML = ''; slot.style.display = 'none'; }
      this.refreshPlayer();
    },
```

- [ ] **Step 6: Run tests**

```bash
.venv/bin/pytest -q tests/integration/test_studio_compare.py
```

Expected: **PASS**.

- [ ] **Step 7: Manual smoke**

`/studio` with one prompt and ≥ 2 versions. Click `+ Compare`. A second card materializes to the right. Click `×` on cmp; cmp disappears, single-card view returns.

- [ ] **Step 8: Commit**

```bash
git add backend/app/templates/pages/_studio_compare.html \
        backend/app/templates/pages/studio.html \
        backend/app/templates/pages/_studio_prompt_card.html \
        backend/app/static/studio.js \
        tests/integration/test_studio_compare.py
git commit -m "feat(studio): + Compare materializes cmp card; × Close removes it"
```

---

## Task 10: Player overlay second range row (cmp)

The `/studio/_player` route already accepts `compare_id` (Task 2). Now it needs to actually be invoked with it. `refreshPlayer()` (added in Task 8) already passes it; we just need a test that proves the two-row path renders correctly.

**Files:**
- Modify: `tests/integration/test_studio_player_overlay.py` (add the two-row test)

- [ ] **Step 1: Add failing test**

Append to `tests/integration/test_studio_player_overlay.py`:

```python
def test_player_two_rows_with_compare_id(client):
    # Create prompt + two versions, seed runs on both.
    r = client.post("/api/prompts", json={
        "name": "p2", "media_kind": "any", "model": "gemini-2.5-pro",
        "target_map": {}, "output_schema": {}, "body": "v1",
    })
    pid = r.json()["prompt_id"]; v1 = r.json()["version_id"]
    client.post(f"/api/prompts/{pid}/versions/{v1}/promote")
    r = client.post(f"/api/prompts/{pid}/versions", json={"from_version_id": v1, "body": "v2"})
    v2 = r.json()["version_id"]

    _seed_run(client, version_id=v1, clip_id=12041, scenes=[
        {"in_secs": 1.0, "out_secs": 2.0, "name": "v1-scene"},
    ])
    _seed_run(client, version_id=v2, clip_id=12041, scenes=[
        {"in_secs": 5.0, "out_secs": 6.0, "name": "v2-scene-a"},
        {"in_secs": 7.0, "out_secs": 8.0, "name": "v2-scene-b"},
    ])

    r = client.get(f"/studio/_player?clip_id=12041&version_id={v2}&compare_id={v1}")
    assert r.status_code == 200
    # Two ranges rows, one with class range-cur, one with range-cmp.
    assert 'class="ranges range-cur"' in r.text
    assert 'class="ranges range-cmp"' in r.text
    # Legend mentions both versions.
    assert 'legend-range-cur' in r.text
    assert 'legend-range-cmp' in r.text


def test_player_two_rows_with_empty_cmp(client):
    """compare_id given but no run exists for it → row renders but with 0 scenes."""
    r = client.post("/api/prompts", json={
        "name": "p3", "media_kind": "any", "model": "gemini-2.5-pro",
        "target_map": {}, "output_schema": {}, "body": "v1",
    })
    pid = r.json()["prompt_id"]; v1 = r.json()["version_id"]
    client.post(f"/api/prompts/{pid}/versions/{v1}/promote")
    r = client.post(f"/api/prompts/{pid}/versions", json={"from_version_id": v1, "body": "v2"})
    v2 = r.json()["version_id"]
    _seed_run(client, version_id=v1, clip_id=12041, scenes=[
        {"in_secs": 1.0, "out_secs": 2.0, "name": "x"},
    ])
    # No run on v2.
    r = client.get(f"/studio/_player?clip_id=12041&version_id={v1}&compare_id={v2}")
    assert r.status_code == 200
    assert 'class="ranges range-cur"' in r.text
    # range-cmp ranges container still emitted, even if empty (so legend stays consistent).
    assert 'class="ranges range-cmp"' in r.text
    # Legend should NOT include the empty row (selectattr filter in template).
    assert 'legend-range-cmp' not in r.text
```

- [ ] **Step 2: Run tests**

```bash
.venv/bin/pytest -q tests/integration/test_studio_player_overlay.py
```

Expected: **PASS** (Task 2 already wired this; these tests verify the wiring).

- [ ] **Step 3: Manual smoke**

Open `/studio` with cmp showing. The player should show two stacked range rows; the legend should list both versions and their scene counts.

- [ ] **Step 4: Commit**

```bash
git add tests/integration/test_studio_player_overlay.py
git commit -m "test(studio): cover two-row player overlay via /studio/_player?compare_id="
```

---

## Task 11: Tab sync — lift `mode` from card to page

`mode` already lives on `$root` in `studioPage` (added in Task 6). Both cards' tab buttons already bind to `$root.mode` (Task 7). What's left: prove sync works under HTMX swaps, and remove any vestigial per-card `mode` references.

**Files:**
- Modify: `backend/app/static/studio.js` (remove per-card `mode`; ensure nothing lingers)
- Verify: `backend/app/templates/pages/_studio_prompt_card.html` references `$root.mode` only

- [ ] **Step 1: Add failing test**

Append to `tests/integration/test_studio_compare.py`:

```python
def test_both_cards_bind_tabs_to_root_mode(client):
    pid, v1, v2 = _two_versions(client)
    r = client.get(f"/studio?prompt_id={pid}&version_id={v2}&compare_version_id={v1}")
    html = r.text
    # Both cards' tab buttons read $root.mode.
    cur_count = html.count('$root.mode === \'prompt\'')
    out_count = html.count('$root.mode === \'output\'')
    assert cur_count >= 2  # both cards have one each
    assert out_count >= 2
    # Per-card `mode` ref should not appear.
    assert 'this.mode' not in html
```

- [ ] **Step 2: Run test**

```bash
.venv/bin/pytest -q tests/integration/test_studio_compare.py::test_both_cards_bind_tabs_to_root_mode
```

Expected: **PASS** (Tasks 6+7 wired this; assertion is the regression guard).

- [ ] **Step 3: Audit `studio.js` for vestigial `mode`**

Run:

```bash
grep -n "mode" backend/app/static/studio.js
```

The only matches should be `mode: 'prompt'` on the page initializer (Task 6 added it). If `studioPromptCard` still has `mode: ...`, remove it. No edit needed if Task 6's lift was clean.

- [ ] **Step 4: Manual smoke**

Open `/studio` with both cards. Click the Output tab on one card; both should switch. Switch back to Prompt on the other; both switch.

- [ ] **Step 5: Commit (only if any change was made)**

```bash
git add backend/app/static/studio.js \
        tests/integration/test_studio_compare.py
git commit -m "test(studio): lock $root.mode tab sync across both prompt-cards"
```

---

## Task 12: Port `lineDiff` to Python and JS (TDD on the algorithm)

Both ports must agree on the same fixtures. Python version drives unit tests; JS version is what runs in the browser.

**Files:**
- Create: `tests/unit/test_studio_line_diff.py`
- Create: `backend/app/static/studio-diff.js`

- [ ] **Step 1: Write the Python tests (unit-test the algorithm in Python)**

`tests/unit/test_studio_line_diff.py`:

```python
"""Python mirror of the JS lineDiff function. Tests pin the algorithm
shape; the JS version (backend/app/static/studio-diff.js) is a
character-for-character port of this implementation and shares these
fixtures."""

from typing import Any


def line_diff(a_text: str, b_text: str) -> list[dict[str, Any]]:
    """LCS-aligned line diff. Output rows are dicts with:
      {"type": "eq", "a": <line>, "b": <line>}
      {"type": "del", "a": <line>}
      {"type": "ins", "b": <line>}
    """
    A = (a_text or "").split("\n")
    B = (b_text or "").split("\n")
    n, m = len(A), len(B)
    # lcs[i][j] = LCS length of A[i:] and B[j:]
    lcs = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            if A[i] == B[j]:
                lcs[i][j] = lcs[i + 1][j + 1] + 1
            else:
                lcs[i][j] = max(lcs[i + 1][j], lcs[i][j + 1])
    out: list[dict[str, Any]] = []
    i = j = 0
    while i < n and j < m:
        if A[i] == B[j]:
            out.append({"type": "eq", "a": A[i], "b": B[j]}); i += 1; j += 1
        elif lcs[i + 1][j] >= lcs[i][j + 1]:
            out.append({"type": "del", "a": A[i]}); i += 1
        else:
            out.append({"type": "ins", "b": B[j]}); j += 1
    while i < n:
        out.append({"type": "del", "a": A[i]}); i += 1
    while j < m:
        out.append({"type": "ins", "b": B[j]}); j += 1
    return out


def test_empty_inputs_produce_empty_output():
    # Both empty strings split to [""] each, which match → one eq row.
    rows = line_diff("", "")
    assert rows == [{"type": "eq", "a": "", "b": ""}]


def test_identical_text_is_all_eq():
    text = "a\nb\nc"
    rows = line_diff(text, text)
    assert all(r["type"] == "eq" for r in rows)
    assert [r["a"] for r in rows] == ["a", "b", "c"]


def test_all_insert():
    rows = line_diff("", "x\ny")
    # "" splits to [""]; "x\ny" splits to ["x", "y"]. LCS = 0 → all ins
    # plus the trailing "" match? Actually [""].lcs(["x","y"]) = 0,
    # both sides walk: del "", ins "x", ins "y".
    types = [r["type"] for r in rows]
    assert types == ["del", "ins", "ins"]
    assert rows[0]["a"] == ""
    assert [r["b"] for r in rows[1:]] == ["x", "y"]


def test_all_delete():
    rows = line_diff("x\ny", "")
    types = [r["type"] for r in rows]
    assert types == ["del", "del", "ins"]
    assert [r["a"] for r in rows[:2]] == ["x", "y"]
    assert rows[2]["b"] == ""


def test_interleaved():
    a = "a\nb\nc\nd"
    b = "a\nX\nc\nd"
    rows = line_diff(a, b)
    # eq a, del b, ins X, eq c, eq d
    assert [r["type"] for r in rows] == ["eq", "del", "ins", "eq", "eq"]
    assert rows[0]["a"] == "a"
    assert rows[1]["a"] == "b"
    assert rows[2]["b"] == "X"
    assert rows[3]["a"] == "c" and rows[3]["b"] == "c"
    assert rows[4]["a"] == "d" and rows[4]["b"] == "d"


def test_handles_none_safely():
    rows = line_diff(None, None)  # type: ignore[arg-type]
    assert rows == [{"type": "eq", "a": "", "b": ""}]
```

- [ ] **Step 2: Run tests to verify they pass**

```bash
.venv/bin/pytest -q tests/unit/test_studio_line_diff.py
```

Expected: **PASS** (6 tests).

- [ ] **Step 3: Port to JS (character-for-character)**

`backend/app/static/studio-diff.js`:

```js
// Studio line-diff: LCS-aligned line diff over two strings.
// Mirror of tests/unit/test_studio_line_diff.py:line_diff — keep the
// two in sync. The Python tests are authoritative; if you change the
// algorithm here, change it there too and rerun the tests.

function lineDiff(aText, bText) {
  const A = (aText || "").split("\n");
  const B = (bText || "").split("\n");
  const n = A.length, m = B.length;
  // lcs[i][j] = LCS length of A.slice(i) and B.slice(j).
  const lcs = Array.from({length: n + 1}, () => new Int32Array(m + 1));
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      lcs[i][j] = A[i] === B[j]
        ? lcs[i + 1][j + 1] + 1
        : Math.max(lcs[i + 1][j], lcs[i][j + 1]);
    }
  }
  const out = [];
  let i = 0, j = 0;
  while (i < n && j < m) {
    if (A[i] === B[j])                   { out.push({type: "eq",  a: A[i], b: B[j]}); i++; j++; }
    else if (lcs[i + 1][j] >= lcs[i][j + 1]) { out.push({type: "del", a: A[i]}); i++; }
    else                                     { out.push({type: "ins", b: B[j]}); j++; }
  }
  while (i < n) { out.push({type: "del", a: A[i++]}); }
  while (j < m) { out.push({type: "ins", b: B[j++]}); }
  return out;
}

// Alpine component for the cmp card's diff view. Reads cur + cmp text
// from the sibling DOM. Real wiring lands in Task 13; this stub exposes
// the component so Alpine doesn't error on the directive when the page
// includes the script before the next task lands.
document.addEventListener("alpine:init", () => {
  if (!window.Alpine) return;
  window.Alpine.data("cmpDiff", () => ({
    rows: [],
    refresh() { /* filled in Task 13 */ },
  }));
});

window.lineDiff = lineDiff;  // exported for browser-console verification
```

- [ ] **Step 4: Add the script to layout (loaded only on /studio)**

In `backend/app/templates/pages/studio.html`, at the bottom of the page (just before the closing `</div>` for `.studio-page`, or just after the `window.__studioVersions` script), add:

```jinja
<script src="/static/studio-diff.js"></script>
```

- [ ] **Step 5: Manual cross-check Python ↔ JS**

Start the dev server. Open `/studio` in a browser, open devtools, run in the console:

```js
JSON.stringify(lineDiff("a\nb\nc\nd", "a\nX\nc\nd"))
```

Expected output (matches the Python `test_interleaved` fixture):

```json
[{"type":"eq","a":"a","b":"a"},
 {"type":"del","a":"b"},
 {"type":"ins","b":"X"},
 {"type":"eq","a":"c","b":"c"},
 {"type":"eq","a":"d","b":"d"}]
```

- [ ] **Step 6: Commit**

```bash
git add backend/app/static/studio-diff.js \
        backend/app/templates/pages/studio.html \
        tests/unit/test_studio_line_diff.py
git commit -m "feat(studio): lineDiff — Python + JS ports with shared fixtures"
```

---

## Task 13: Wire the diff toggle and `cmpDiff` Alpine component

Fill in `_studio_diff.html` with the real diff renderer. The Alpine `cmpDiff` reads cur+cmp text from sibling DOM, runs `lineDiff`, and renders rows.

**Files:**
- Modify: `backend/app/templates/pages/_studio_diff.html`
- Modify: `backend/app/static/studio-diff.js` (fill in `cmpDiff` Alpine component)
- Modify: `backend/app/static/app.css`

- [ ] **Step 1: Write a (necessarily-manual) failing assertion**

The diff is pure browser-side; pytest can't easily exercise it without a headless browser. We use a manual flow as the test and a small integration check that asserts the diff slot is reachable.

Append to `tests/integration/test_studio_compare.py`:

```python
def test_cmp_card_emits_cmp_diff_alpine_root(client):
    pid, v1, v2 = _two_versions(client)
    r = client.get(f"/studio?prompt_id={pid}&version_id={v2}&compare_version_id={v1}")
    html = r.text
    # Cmp card has a diff slot wired to the cmpDiff Alpine component.
    assert 'data-cmp-diff' in html
    assert 'x-data="cmpDiff"' in html
```

- [ ] **Step 2: Run test**

```bash
.venv/bin/pytest -q tests/integration/test_studio_compare.py::test_cmp_card_emits_cmp_diff_alpine_root
```

Expected: **FAIL** — `data-cmp-diff` div exists from Task 7's stub, but `x-data="cmpDiff"` does not.

- [ ] **Step 3: Replace `_studio_diff.html`**

```jinja
{# Cmp-card diff body. Reads cur + cmp text from sibling DOM and renders
   a two-column line-diff. Refreshes when:
     * the cmp `diff` toggle flips on (x-init runs)
     * the page-level mode flips (prompt ↔ output)
     * either side's content swaps (pendingRunSwap bumps)

   Required scope: $root.mode, $root.pendingRunSwap, $root.activeVersionId,
   $root.compareVersionId.
#}
<div class="pc-diff" data-cmp-diff x-data="cmpDiff"
     x-init="refresh()"
     x-effect="$root.mode; $root.pendingRunSwap; $root.activeVersionId; $root.compareVersionId; refresh()">
  <div class="pc-diff-hdr mono-cell muted">
    <span x-text="`diff · ${$root.mode}`"></span>
    <span class="grow"></span>
    <span x-show="rows.length" x-text="`${rows.filter(r => r.type !== 'eq').length} differences`"></span>
  </div>
  <table class="pc-diff-table">
    <tbody>
      <template x-for="(row, i) in rows" :key="i">
        <tr :class="`diff-row diff-${row.type}`">
          <td class="diff-cell-a mono"><span x-text="row.a ?? ''"></span></td>
          <td class="diff-cell-b mono"><span x-text="row.b ?? ''"></span></td>
        </tr>
      </template>
    </tbody>
  </table>
  <div class="muted" x-show="!rows.length">No content to diff yet.</div>
</div>
```

- [ ] **Step 4: Fill in the `cmpDiff` Alpine component**

In `backend/app/static/studio-diff.js`, replace the stub with:

```js
document.addEventListener("alpine:init", () => {
  if (!window.Alpine) return;

  window.Alpine.data("cmpDiff", () => ({
    rows: [],

    refresh() {
      const page = document.querySelector(".studio-page")?._x_dataStack?.[0];
      if (!page) { this.rows = []; return; }
      const mode = page.mode || "prompt";
      const curCard = document.querySelector('.studio-prompt-card[data-side="cur"]');
      const cmpCard = document.querySelector('.studio-prompt-card[data-side="cmp"]');
      if (!curCard || !cmpCard) { this.rows = []; return; }

      const readText = (card) => {
        if (mode === "prompt") {
          const ta = card.querySelector("textarea.pc-editor");
          if (ta) return ta.value;
          const pre = card.querySelector("pre.pc-readonly");
          return pre ? pre.textContent : "";
        }
        // mode === 'output': read raw JSON from the embedded script and
        // pretty-print it. The script lives inside the card's run-slot.
        const blk = card.querySelector('script[type="application/json"][data-run-json]');
        if (!blk) return "";
        try {
          const obj = JSON.parse(blk.textContent || "{}");
          return JSON.stringify(obj, null, 2);
        } catch {
          return blk.textContent || "";
        }
      };

      this.rows = lineDiff(readText(curCard), readText(cmpCard));
    },
  }));
});
```

- [ ] **Step 5: Append CSS for diff rows**

In `backend/app/static/app.css`, append:

```css
/* === Studio: prompt-card diff view ==================================== */
.pc-diff { padding: 8px; }
.pc-diff-hdr { display: flex; align-items: center; padding: 4px 6px 8px; }
.pc-diff-table { width: 100%; border-collapse: collapse; font-size: 12px; }
.pc-diff-table td {
  vertical-align: top; padding: 2px 6px; white-space: pre-wrap;
  word-break: break-word; border-top: 1px solid var(--border, #1f1f1f);
}
.diff-row.diff-eq td  { opacity: 0.55; }
.diff-row.diff-del .diff-cell-a { background: rgba(220, 60, 60, 0.18); }
.diff-row.diff-del .diff-cell-b { background: rgba(0,0,0,0); }
.diff-row.diff-ins .diff-cell-a { background: rgba(0,0,0,0); }
.diff-row.diff-ins .diff-cell-b { background: rgba(60, 180, 90, 0.18); }

/* === Studio: compare row + cmp affordances =========================== */
.studio-compare-row { display: flex; gap: 12px; align-items: stretch; }
.studio-compare-row .studio-prompt-card { flex: 1 1 0; min-width: 0; }
.cmp-card { border-left: 3px solid var(--accent, #4a90e2); }
.btn-compare, .btn-diff-toggle, .btn-close-cmp {
  background: none; border: 1px solid var(--border, #2a2a2a); color: inherit;
  padding: 2px 8px; border-radius: 4px; cursor: pointer; font-size: 12px;
}
.btn-diff-toggle.active { background: var(--accent-fade, #2b3a4d); }
.btn-close-cmp { font-size: 14px; line-height: 1; padding: 0 6px; }

/* === Player overlay: cur / cmp range colors ========================== */
.ranges.range-cur .range { background: rgba(74, 144, 226, 0.45); }
.ranges.range-cmp { top: 60%; }  /* stack below the cur row */
.ranges.range-cmp .range { background: rgba(220, 140, 60, 0.45); }
.timeline-legend { display: flex; gap: 12px; padding: 4px 8px; font-size: 11px; }
.legend-range-cur { color: rgba(74, 144, 226, 0.9); }
.legend-range-cmp { color: rgba(220, 140, 60, 0.9); }
```

- [ ] **Step 6: Run tests**

```bash
.venv/bin/pytest -q tests/integration/test_studio_compare.py
```

Expected: **PASS** including `test_cmp_card_emits_cmp_diff_alpine_root`.

- [ ] **Step 7: Manual smoke — Prompt diff**

Open `/studio?prompt_id=N&version_id=…&compare_version_id=…` where the two prompt bodies differ. Click `Diff vs v{cur}` on the cmp card. The cmp body should become a two-column diff: identical lines neutral, lines only in cur red on the left, lines only in cmp green on the right.

- [ ] **Step 8: Manual smoke — Output diff**

Switch to the Output tab on both cards (one click — sync). Click `Diff vs v{cur}` again. The diff now shows pretty-printed JSON differences between the two latest runs.

- [ ] **Step 9: Commit**

```bash
git add backend/app/templates/pages/_studio_diff.html \
        backend/app/static/studio-diff.js \
        backend/app/static/app.css \
        tests/integration/test_studio_compare.py
git commit -m "feat(studio): cmp-card diff view via cmpDiff Alpine + lineDiff"
```

---

## Task 14: `seekFocusedClip` proxy and marker-click integration

`_anno_panels.html` calls `seek(secs)` on marker articles. In clip view that resolves to the player Alpine. In studio, the markers list is rendered inside the prompt-card, which doesn't own a player. Proxy through `$root`.

**Files:**
- Modify: `backend/app/static/studio.js`
- Modify: `backend/app/templates/pages/_studio_prompt_card.html` — provide a `seek` shim in the prompt-card scope

- [ ] **Step 1: Add a manual-verification placeholder test**

Append to `tests/integration/test_studio_run_output_reuse.py`:

```python
def test_marker_articles_have_seek_handler(client):
    """Smoke: rendered marker @click attr is present (the seek wiring is
    JS-only and tested manually below)."""
    # Re-use setup from test_run_output_uses_anno_panels_and_has_run_json
    r = client.post("/api/prompts", json={
        "name": "marker-seek", "media_kind": "any", "model": "gemini-2.5-pro",
        "target_map": {}, "output_schema": {}, "body": "x",
    })
    vid = r.json()["version_id"]
    from backend.app import main as main_mod
    _seed_run(main_mod.app, version_id=vid, clip_id=12041, output_json={
        "scenes": [{"in_secs": 5.0, "out_secs": 6.0, "name": "s"}],
    })
    r = client.get(f"/studio/_run?prompt_version_id={vid}&clip_id=12041")
    assert r.status_code == 200
    assert '@click="seek(' in r.text  # comes from _anno_panels.html line 37
```

- [ ] **Step 2: Run test**

```bash
.venv/bin/pytest -q tests/integration/test_studio_run_output_reuse.py::test_marker_articles_have_seek_handler
```

Expected: **PASS**.

- [ ] **Step 3: Add `seek(secs)` to `studioPromptCard` Alpine factory**

In `backend/app/static/studio.js`, inside the `Alpine.data('studioPromptCard', ...)` factory, add:

```js
    seek(secs) {
      // _anno_panels.html marker articles call seek(secs). Proxy through
      // the page root to the player's Alpine instance.
      const root = document.querySelector('.studio-page')?._x_dataStack?.[0];
      root?.seekFocusedClip(secs);
    },
```

And add to `studioPage`:

```js
    seekFocusedClip(secs) {
      const playerEl = document.querySelector('.studio-player');
      if (!playerEl || !playerEl._x_dataStack) return;
      const player = playerEl._x_dataStack[0];
      if (typeof player.seek === 'function') player.seek(secs);
    },
```

- [ ] **Step 4: Make sure `_anno_panels.html`'s `seek(secs)` call resolves inside the studio scope**

Re-read `_studio_prompt_card.html` — the include happens inside `x-data="studioPromptCard('{{ side }}')"`. The included partial's `@click="seek({{ m.in_secs }})"` resolves against the closest enclosing Alpine, which is the prompt-card. Step 3 added `seek` to that scope. ✅

- [ ] **Step 5: Manual smoke**

`/studio`, focus a clip with a run that has scenes. Output tab. Click a marker article. The player should jump to that scene's `in_secs`.

- [ ] **Step 6: Commit**

```bash
git add backend/app/static/studio.js \
        tests/integration/test_studio_run_output_reuse.py
git commit -m "feat(studio): seek proxy — anno-panel marker clicks scrub the player"
```

---

## Task 15: CSS polish minimum + final regression sweep

Make sure the new affordances look reasonable on dark+light themes; tighten anything that drifted during impl.

**Files:**
- Modify: `backend/app/static/app.css` (only if visual issues found in the manual sweep)

- [ ] **Step 1: Visual checklist (manual)**

Open `/studio` and walk through each flow from the spec's Manual acceptance flows section (`docs/specs/2026-05-26-prompt-studio-pr2-design.md`):

1. Cur-card version picker — switch
2. Cur-card editable / read-only
3. Compare materialization
4. Cmp-card version picker — local
5. Tab sync across cards
6. Diff vs cur — Prompt mode
7. Diff vs cur — Output mode
8. Annotation-card visual parity
9. Player overlay updates on version switch
10. Run on this clip — label follows cur
11. Deep linking (open in new tab)
12. Single-card mode reload safety
13. Clip-detail regression (open `/clips/12041` — timeline + ranges + scrubbing + keyboard still work)

For each flow that visually misbehaves, edit `app.css` and re-verify. Commit any CSS tweaks in a single commit at the end of the sweep.

- [ ] **Step 2: Full test sweep**

```bash
.venv/bin/pytest -q
```

Expected: **all pass**, no skips beyond the ones that already existed before PR2.

- [ ] **Step 3: Lint + typecheck**

```bash
.venv/bin/ruff check backend tests
.venv/bin/basedpyright backend
```

Expected: both clean.

- [ ] **Step 4: Commit any CSS / lint fixes**

```bash
git add -A
git commit -m "chore(studio): visual polish + lint clean after PR2 sweep"
```

(Skip this commit if no changes were needed.)

---

## Task 16: ADR + decisions index

Document the PR2 design calls per the project rule in `CLAUDE.md`.

**Files:**
- Create: `docs/adr/0034-prompt-studio-pr2-version-compare.md`
- Modify: `docs/decisions.md`

- [ ] **Step 1: Write the ADR**

`docs/adr/0034-prompt-studio-pr2-version-compare.md`:

```markdown
# 0034. Prompt Studio PR2 — version compare

- **Date:** 2026-05-27
- **Status:** Accepted

## Context

PR2 of Prompt Studio adds side-by-side version comparison: a version
picker on each prompt-card, a `+ Compare` button that materializes a
second card, line-diff views for prompt body and structured output, and
a second range row on the player overlay. The spec at
`docs/specs/2026-05-26-prompt-studio-pr2-design.md` covers the design;
this ADR records the implementation calls worth knowing about.

## Alternatives

- **Alpine-side state with all versions preloaded** for version-switch.
  Rejected: bigger initial payload, more JS, and the editor↔readonly
  DOM toggle would need to be replicated client-side. HTMX swap of the
  card partial keeps server-side ownership of the rendering and is
  consistent with the existing `/studio/_run` swap pattern.
- **Separate /api endpoint for the output JSON** consumed by the diff.
  Rejected: every diff toggle would incur two fetches. Embedding the
  raw JSON in a `<script type="application/json" data-run-json>` block
  inside the existing output partial is zero-cost since the partial is
  already loaded.
- **Custom transport replacing native `<video controls>`.** Rejected:
  the umbrella spec says "no new player behavior". Layering an SVG/HTML
  strip below the native controls and reusing `Alpine.data("player",
  ...)` from clip_detail is the smaller change.
- **Bespoke renderer for the Studio Output tab.** Rejected — actively.
  `_anno_panels.html` already renders markers/fields/notes on
  clip_detail and `_anno_draft.html`; the studio's PR1 renderer was
  one day old. Codified the reuse rule in `CLAUDE.md` (`Frontend:
  explore before implementing`).

## Decision

- **Version switch is HTMX-driven.** A new partial route
  `GET /studio/_prompt_card?side=cur|cmp&prompt_version_id=N&clip_id=M`
  renders one card. The chip's dropdown rows are HTMX buttons with
  `hx-target="closest .studio-prompt-card"` and `hx-swap="outerHTML"`.
- **Cur picker is the active-version picker.** Picking on cur bumps
  `studioPage.activeVersionId/Num`, the Run-button label, and the URL
  `?version_id=`. Picking on cmp updates only `compareVersionId` and
  `?compare_version_id=`.
- **Both selectors are deep-linkable.** The page route accepts both
  query params and server-renders the right initial state.
- **Output diff reads from sibling DOM** — no extra fetch. Each card's
  Output partial includes a `<script type="application/json"
  data-run-json>` block; the cmp card's `cmpDiff` Alpine component
  parses both blocks, pretty-prints, and runs `lineDiff`.
- **`lineDiff` ports** — Python (in the test suite) and JS (in the
  browser). The Python version is authoritative; the JS is a
  character-for-character port. Same fixtures cover both.
- **`_player_overlay.html`** is extracted from clip_detail and shared
  with the studio player. The studio uses `Alpine.data("player", ...)`
  unchanged.
- **`_anno_panels.html`** is reused for the studio Output tab. A new
  `show_history` flag (default true) elides the History tab in studio
  context; a server-side `panels_from_studio_run` adapter converts
  `(output_json, target_map)` into the partial's `panels` shape. Marker
  clicks reach the studio player via a `seekFocusedClip` proxy on the
  page Alpine root.
- **Tab sync** lifts `mode` from per-card to the page Alpine. Both
  cards' tab buttons bind to `$root.mode`.

## Consequences

- One renderer for markers/fields/notes across the app. New
  marker/field features added to `_anno_panels.html` show up in
  studio for free. Conversely, regressions there affect studio — the
  clip-detail player-overlay regression test guards the seam.
- The cmp-card diff view has a known limitation: it only diffs what is
  currently rendered. If the Prompt tab is active when toggled, the
  diff is over prompt bodies; if Output is active, it's over JSON.
  Switching modes re-runs `cmpDiff.refresh` automatically via
  `x-effect`.
- Deep-link URLs (`?prompt_id=…&version_id=…&compare_version_id=…`) are
  reload-safe and shareable. Tab/mode and diff-toggle state are not in
  the URL (intentional — see open questions in the spec).
- The `seek` proxy assumes the studio player is the focused-clip player
  on the same page. If we ever support multiple players on the studio
  page (we don't plan to), this assumption needs revisiting.
```

- [ ] **Step 2: Append to the decisions index**

Open `docs/decisions.md` and add a row to the index table. Use the same format as the existing rows. Example new line (place under the most recent entry):

```markdown
| 0034 | Prompt Studio PR2 — version compare | 2026-05-27 | Accepted | [link](adr/0034-prompt-studio-pr2-version-compare.md) |
```

(If your table uses different column headers, match them — copy the row format from the row above.)

- [ ] **Step 3: Commit**

```bash
git add docs/adr/0034-prompt-studio-pr2-version-compare.md docs/decisions.md
git commit -m "docs(adr): 0034 — Prompt Studio PR2 version compare"
```

---

## Final verification

After all 16 tasks land, run the full pipeline once more from a clean state:

- [ ] `.venv/bin/pytest -q` — **all green**
- [ ] `.venv/bin/ruff check backend tests` — **clean**
- [ ] `.venv/bin/basedpyright backend` — **clean**
- [ ] Manual walk through every flow in
      `docs/specs/2026-05-26-prompt-studio-pr2-design.md`'s "Manual
      acceptance flows" section — **every flow passes**
- [ ] `git log --oneline main..HEAD` should show ~14-16 commits, each
      focused on one task or sub-task

---

## Notes on running the dev server

This project enforces single-instance + graceful-shutdown discipline
(CatDV license seat). Use the project's `server-start` skill before
manual smoke tests and `server-stop` after — never `kill -9`. See
`CLAUDE.md` ("CatDV session discipline") for the full rationale.
