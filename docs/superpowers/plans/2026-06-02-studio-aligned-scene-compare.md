# Studio Aligned Scene Compare + Linked Timeline — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the two side-by-side Output panes (when comparing two prompt versions) with a single aligned scene table, and link scene selection bidirectionally to a labeled, status-colored comparison timeline.

**Architecture:** One pure Python service (`output_compare.build_output_compare`) aligns the two versions' annotation `panels` into scene/field/note rows with a per-row diff status and a single `word_diff`. The table partial and the timeline row-builder both consume that model, so their `data-scene-key`s match and a vanilla hover bridge cross-highlights. `word_diff` is promoted from a test mirror to a real service with a `diff_html(segs, side)` Jinja helper.

**Tech Stack:** FastAPI + Jinja2 (server-rendered HTMX partials), Alpine `Alpine.store('studio')`, vanilla JS bridge, pytest. No Node (ADR 0001).

**Spec:** `docs/specs/2026-06-02-studio-aligned-scene-compare-design.md`

**Environment notes:**
- Python: use `.venv/bin/python` / `.venv/bin/python -m pytest`.
- One pre-existing unit module fails to collect (`tests/unit/test_thumbnail_service_image.py` — `PIL` not installed). When running the whole unit dir, add `--ignore=tests/unit/test_thumbnail_service_image.py`.
- Commit after each task. Do NOT push unless the user asks.

---

## File Structure

- Create `backend/app/services/word_diff.py` — tokenize + `word_diff` (moved from the test mirror) + `diff_html(segs, side)`.
- Create `backend/app/services/output_compare.py` — `build_output_compare(cur_panels, cmp_panels)` + the alignment helpers.
- Modify `backend/app/routes/pages/templates.py` — register `diff_html` Jinja global.
- Modify `tests/unit/test_studio_word_diff.py` — import `word_diff` from the new module (drop the local copy); keep all existing tests.
- Create `tests/unit/test_word_diff_html.py` — `diff_html` side-filtering tests.
- Create `tests/unit/test_output_compare.py` — alignment model tests.
- Create `backend/app/templates/pages/_studio_compare_table.html` — the aligned table partial.
- Modify `backend/app/routes/pages/studio.py` — add `/studio/_compare` route; add `_overlay_rows_from_compare`; branch `_studio_player` to use it when comparing.
- Modify `backend/app/templates/pages/_studio_compare.html` — add the full-width compare-output region.
- Modify `backend/app/templates/pages/_studio_prompt_card.html` — gate the per-card output pane to non-compare.
- Modify `backend/app/templates/pages/_player_overlay.html` — range labels + status class + `data-scene-key`.
- Modify `backend/app/templates/pages/_studio_player.html` — pass `show_range_labels=True`.
- Modify `backend/app/static/studioStore.js` — add `selectedSceneKey`.
- Create `backend/app/static/studioSceneLink.js` — vanilla hover bridge.
- Modify `backend/app/templates/pages/studio.html` — include `studioSceneLink.js`.
- Modify `backend/app/static/app.css` — `--changed` token, `.pill.changed`, `.studio-compare-table` (`.sct-*`), `.range-label`, `.range-st-*`, `.is-linked`.
- Create `tests/integration/test_studio_compare_route.py`, `tests/integration/test_studio_compare_layout.py`, `tests/integration/test_studio_timeline_linkage.py`.

---

## Task 1: Promote `word_diff` to a service + `diff_html` helper

**Files:**
- Create: `backend/app/services/word_diff.py`
- Modify: `backend/app/routes/pages/templates.py`
- Modify: `tests/unit/test_studio_word_diff.py`
- Test: `tests/unit/test_word_diff_html.py`

- [ ] **Step 1: Create the service module**

Create `backend/app/services/word_diff.py`:

```python
"""LCS-aligned word-level inline diff (Word track-changes style).

Tokenizes into word + whitespace runs, LCS-aligns, and coalesces adjacent
same-type ops into segments {"type": "eq"|"ins"|"del", "text": ...}. This is
the authoritative implementation; tests/unit/test_studio_word_diff.py pins its
shape, and backend/app/static/studio-diff.js mirrors it for the live
client-side Prompt diff (keep the two in sync).
"""

from __future__ import annotations

import html as _html
import re
from typing import Any

from markupsafe import Markup


def tokenize(s: str | None) -> list[str]:
    """Split into word + whitespace tokens, preserving everything so the text
    is reconstructable by concatenation. Empty pieces are dropped."""
    if not s:
        return []
    return [t for t in re.split(r"(\s+)", s) if t != ""]


def word_diff(a_text: str | None, b_text: str | None) -> list[dict[str, Any]]:
    """LCS word diff from a_text (old) to b_text (new). Coalesced segments:
    {"type": "eq", ...} unchanged, {"type": "del", ...} only in old,
    {"type": "ins", ...} only in new."""
    A = tokenize(a_text)
    B = tokenize(b_text)
    n, m = len(A), len(B)
    lcs = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            if A[i] == B[j]:
                lcs[i][j] = lcs[i + 1][j + 1] + 1
            else:
                lcs[i][j] = max(lcs[i + 1][j], lcs[i][j + 1])
    ops: list[tuple[str, str]] = []
    i = j = 0
    while i < n and j < m:
        if A[i] == B[j]:
            ops.append(("eq", A[i]))
            i += 1
            j += 1
        elif lcs[i + 1][j] >= lcs[i][j + 1]:
            ops.append(("del", A[i]))
            i += 1
        else:
            ops.append(("ins", B[j]))
            j += 1
    while i < n:
        ops.append(("del", A[i]))
        i += 1
    while j < m:
        ops.append(("ins", B[j]))
        j += 1
    segs: list[dict[str, Any]] = []
    for typ, text in ops:
        if segs and segs[-1]["type"] == typ:
            segs[-1]["text"] += text
        else:
            segs.append({"type": typ, "text": text})
    return segs


def diff_html(segs: list[dict[str, Any]] | None, side: str = "both") -> Markup:
    """Render coalesced segments to escaped HTML with <ins>/<del> wrappers.

    side="left"  -> eq + del   (older text; deletions struck red)
    side="right" -> eq + ins   (newer text; insertions green)
    side="both"  -> eq + ins + del (one flowing block, e.g. notes)
    Returns Markup so templates need no `| safe`.
    """
    if side == "left":
        keep = {"eq", "del"}
    elif side == "right":
        keep = {"eq", "ins"}
    else:
        keep = {"eq", "ins", "del"}
    out: list[str] = []
    for s in segs or []:
        if s["type"] not in keep:
            continue
        t = _html.escape(s["text"])
        if s["type"] == "ins":
            out.append(f'<ins class="diff-ins">{t}</ins>')
        elif s["type"] == "del":
            out.append(f'<del class="diff-del">{t}</del>')
        else:
            out.append(t)
    return Markup("".join(out))
```

- [ ] **Step 2: Register the Jinja global**

In `backend/app/routes/pages/templates.py`, after the `smpte` global line, add:

```python
from backend.app.services.word_diff import diff_html

templates.env.globals["diff_html"] = diff_html
```

(Place the import with the other top-level imports; place the assignment next to `templates.env.globals["smpte"] = secs_to_smpte`.)

- [ ] **Step 3: Point the existing mirror test at the module**

In `tests/unit/test_studio_word_diff.py`, delete the local `_tokenize` and
`word_diff` function definitions and the `import re` / `from typing import Any`
that only they used, and add at the top (below the module docstring):

```python
from backend.app.services.word_diff import word_diff
```

Keep `_reconstruct` and every `test_*` exactly as-is.

- [ ] **Step 4: Run the moved tests — expect PASS**

Run: `.venv/bin/python -m pytest tests/unit/test_studio_word_diff.py -q`
Expected: all PASS (behaviour unchanged, just relocated).

- [ ] **Step 5: Write `diff_html` tests**

Create `tests/unit/test_word_diff_html.py`:

```python
from backend.app.services.word_diff import diff_html, word_diff


def test_left_keeps_eq_and_del_strikes_removed():
    segs = word_diff("the brown fox", "the red fox")
    html = str(diff_html(segs, "left"))
    assert '<del class="diff-del">brown</del>' in html
    assert "<ins" not in html
    assert "the " in html and " fox" in html


def test_right_keeps_eq_and_ins_marks_added():
    segs = word_diff("the brown fox", "the red fox")
    html = str(diff_html(segs, "right"))
    assert '<ins class="diff-ins">red</ins>' in html
    assert "<del" not in html


def test_both_keeps_ins_and_del():
    segs = word_diff("the brown fox", "the red fox")
    html = str(diff_html(segs, "both"))
    assert "<del" in html and "<ins" in html


def test_escapes_html_text():
    segs = word_diff("", "<script>")
    html = str(diff_html(segs, "right"))
    assert "&lt;script&gt;" in html
    assert "<script>" not in html


def test_none_segs_is_empty():
    assert str(diff_html(None, "both")) == ""
```

- [ ] **Step 6: Run — expect PASS**

Run: `.venv/bin/python -m pytest tests/unit/test_word_diff_html.py -q`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/word_diff.py backend/app/routes/pages/templates.py \
        tests/unit/test_studio_word_diff.py tests/unit/test_word_diff_html.py
git commit -m "refactor(studio): promote word_diff to a service + diff_html helper

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: `output_compare` alignment model

**Files:**
- Create: `backend/app/services/output_compare.py`
- Test: `tests/unit/test_output_compare.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_output_compare.py`:

```python
from backend.app.services.output_compare import build_output_compare


def _marker(in_s, out_s, name, desc=None):
    return {"in_secs": float(in_s), "out_secs": float(out_s),
            "name": name, "description": desc, "category": None}


def _panels(markers=None, fields=None, notes=None):
    return {"markers": markers or [], "fields": fields or [], "notes": notes}


# Mock 1 data: v4 (cmp) vs v5 (cur).
CMP = _panels(markers=[
    _marker(0, 7, "Celkovy pohled na statek se vzrostlym stromem"),
    _marker(7, 28, "Zeny pracuji na dvore u lavice"),
    _marker(28, 42, "Zena stoji u zdi domu"),
])
CUR = _panels(markers=[
    _marker(0, 7, "Celkovy pohled na statek se vzrostlym stromem"),
    _marker(7, 17, "Zena v satku loupe brambory na drevene lavici"),
    _marker(17, 24, "Dite v bilem cepci sedi u stolu vedle zeny"),
    _marker(24, 35, "Zena ve vzorovanych satech stoji u zdi domu"),
    _marker(35, 42, "Detail naradi a nadob u zdi staveni"),
])


def test_mock1_aligns_to_five_scenes_with_expected_statuses():
    model = build_output_compare(CUR, CMP)
    assert model["scene_count"] == 5
    assert [r["status"] for r in model["scenes"]] == [
        "unchanged", "changed", "added", "changed", "added",
    ]


def test_added_scene_has_no_cmp_side():
    model = build_output_compare(CUR, CMP)
    added = [r for r in model["scenes"] if r["status"] == "added"]
    assert added and all(r["cmp"] is None and r["cur"] is not None for r in added)


def test_scene_keys_are_unique_and_stable():
    model = build_output_compare(CUR, CMP)
    keys = [r["key"] for r in model["scenes"]]
    assert keys == [f"scene-{i}" for i in range(5)]


def test_changed_scene_segs_have_ins_and_del():
    model = build_output_compare(CUR, CMP)
    changed = next(r for r in model["scenes"] if r["status"] == "changed")
    types = {s["type"] for s in changed["segs"]}
    assert "ins" in types and "del" in types


def test_scene_side_carries_tc_dur_and_name():
    model = build_output_compare(CUR, CMP)
    first = model["scenes"][0]
    assert first["cur"]["tc"] == "0:00"
    assert first["cur"]["dur_s"] == 7
    assert first["cur"]["name"].startswith("Celkovy")


def test_removed_only_when_cur_empty():
    model = build_output_compare(_panels(), CMP)
    assert model["scene_count"] == 3
    assert {r["status"] for r in model["scenes"]} == {"removed"}
    assert all(r["cur"] is None for r in model["scenes"])


def test_added_only_when_cmp_empty():
    model = build_output_compare(CUR, _panels())
    assert {r["status"] for r in model["scenes"]} == {"added"}


def test_all_unchanged_when_identical():
    model = build_output_compare(CMP, CMP)
    assert {r["status"] for r in model["scenes"]} == {"unchanged"}


def test_empty_inputs_produce_empty_model():
    model = build_output_compare(_panels(), _panels())
    assert model["scene_count"] == 0
    assert model["scenes"] == []
    assert model["fields"] == []
    assert model["notes"] is None


def test_fields_align_by_identifier():
    cur = _panels(fields=[
        {"identifier": "location", "value": "Harbor Beach"},
        {"identifier": "mood", "value": "serene"},
    ])
    cmp = _panels(fields=[
        {"identifier": "location", "value": "Beach"},
        {"identifier": "weather", "value": "sunny"},
    ])
    model = build_output_compare(cur, cmp)
    by = {f["identifier"]: f for f in model["fields"]}
    assert by["location"]["status"] == "changed"
    assert by["mood"]["status"] == "added"
    assert by["weather"]["status"] == "removed"
    assert by["location"]["has_cmp"] and by["location"]["has_cur"]
    assert by["weather"]["has_cmp"] and not by["weather"]["has_cur"]


def test_notes_diff_present_when_changed():
    cur = _panels(notes="hello brave world")
    cmp = _panels(notes="hello world")
    model = build_output_compare(cur, cmp)
    assert model["notes"]["changed"] is True
    assert any(s["type"] == "ins" for s in model["notes"]["segs"])


def test_notes_none_when_both_empty():
    assert build_output_compare(_panels(), _panels())["notes"] is None
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/unit/test_output_compare.py -q`
Expected: FAIL with `ModuleNotFoundError: backend.app.services.output_compare`.

- [ ] **Step 3: Implement the service**

Create `backend/app/services/output_compare.py`:

```python
"""Align two prompt versions' annotation outputs into a scene-level compare
model for the Studio compare table and the linked timeline.

Pure, no I/O. Consumes the `panels` dicts that
backend/app/services/draft_view.build_draft_view produces (markers / fields /
notes) and returns aligned rows. Each row carries one word_diff; the table
renders eq+del on the left (cmp / older) and eq+ins on the right (cur / newer).
"""

from __future__ import annotations

from typing import Any

from backend.app.services.word_diff import word_diff


def _tc(secs: float | None) -> str:
    s = int(round(secs or 0))
    return f"{s // 60}:{s % 60:02d}"


def _out_or_default(m: dict) -> float:
    out = m.get("out_secs")
    return float(out) if out is not None else float(m.get("in_secs") or 0.0) + 1.0


def _dur_s(m: dict) -> int:
    return int(round(_out_or_default(m) - float(m.get("in_secs") or 0.0)))


def _marker_text(m: dict) -> str:
    name = (m.get("name") or "").strip()
    desc = (m.get("description") or "").strip()
    return f"{name}\n{desc}" if desc else name


def _overlaps(a: dict, b: dict) -> bool:
    return (
        float(a.get("in_secs") or 0.0) < _out_or_default(b)
        and float(b.get("in_secs") or 0.0) < _out_or_default(a)
    )


def _side(m: dict) -> dict[str, Any]:
    return {
        "in_secs": float(m.get("in_secs") or 0.0),
        "out_secs": m.get("out_secs"),
        "tc": _tc(m.get("in_secs")),
        "dur_s": _dur_s(m),
        "name": (m.get("name") or "").strip(),
    }


def _scene_row(idx, status, cmp_m, cur_m, segs) -> dict[str, Any]:
    return {
        "key": f"scene-{idx}",
        "status": status,
        "cmp": _side(cmp_m) if cmp_m else None,
        "cur": _side(cur_m) if cur_m else None,
        "segs": segs,
    }


def _align_scenes(cmp_markers: list[dict], cur_markers: list[dict]) -> list[dict]:
    rows: list[dict] = []
    i = j = 0
    n, m = len(cmp_markers), len(cur_markers)
    while i < n and j < m:
        a, b = cmp_markers[i], cur_markers[j]
        if _overlaps(a, b):
            at, bt = _marker_text(a), _marker_text(b)
            status = "unchanged" if at == bt else "changed"
            rows.append(_scene_row(len(rows), status, a, b, word_diff(at, bt)))
            i += 1
            j += 1
        elif _out_or_default(a) <= float(b.get("in_secs") or 0.0):
            rows.append(_scene_row(len(rows), "removed", a, None,
                                   word_diff(_marker_text(a), "")))
            i += 1
        else:
            rows.append(_scene_row(len(rows), "added", None, b,
                                   word_diff("", _marker_text(b))))
            j += 1
    while i < n:
        a = cmp_markers[i]
        rows.append(_scene_row(len(rows), "removed", a, None,
                               word_diff(_marker_text(a), "")))
        i += 1
    while j < m:
        b = cur_markers[j]
        rows.append(_scene_row(len(rows), "added", None, b,
                               word_diff("", _marker_text(b))))
        j += 1
    return rows


def _align_fields(cmp_fields: list[dict], cur_fields: list[dict]) -> list[dict]:
    cmp_by = {f["identifier"]: f for f in cmp_fields}
    cur_by = {f["identifier"]: f for f in cur_fields}
    rows: list[dict] = []
    for k in sorted(set(cmp_by) | set(cur_by)):
        c, u = cmp_by.get(k), cur_by.get(k)
        cv = (c.get("value") if c else "") or ""
        uv = (u.get("value") if u else "") or ""
        if c is None:
            status = "added"
        elif u is None:
            status = "removed"
        else:
            status = "unchanged" if cv == uv else "changed"
        rows.append({
            "key": f"field-{k}",
            "identifier": k,
            "status": status,
            "has_cmp": c is not None,
            "has_cur": u is not None,
            "segs": word_diff(cv, uv),
        })
    return rows


def _notes_diff(cmp_panels: dict, cur_panels: dict) -> dict | None:
    cn = (cmp_panels.get("notes") or "").strip()
    un = (cur_panels.get("notes") or "").strip()
    if not cn and not un:
        return None
    segs = word_diff(cn, un)
    return {"segs": segs, "changed": any(s["type"] != "eq" for s in segs)}


def build_output_compare(cur_panels: dict, cmp_panels: dict) -> dict[str, Any]:
    """Align cur (newer) vs cmp (older) panels into scene/field/note rows."""
    scenes = _align_scenes(cmp_panels.get("markers") or [],
                           cur_panels.get("markers") or [])
    fields = _align_fields(cmp_panels.get("fields") or [],
                           cur_panels.get("fields") or [])
    return {
        "scene_count": len(scenes),
        "scenes": scenes,
        "fields": fields,
        "notes": _notes_diff(cmp_panels, cur_panels),
    }
```

- [ ] **Step 4: Run — expect PASS**

Run: `.venv/bin/python -m pytest tests/unit/test_output_compare.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/output_compare.py tests/unit/test_output_compare.py
git commit -m "feat(studio): scene/field/note alignment model for output compare

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Compare table partial + `/studio/_compare` route

**Files:**
- Create: `backend/app/templates/pages/_studio_compare_table.html`
- Modify: `backend/app/routes/pages/studio.py`
- Test: `tests/integration/test_studio_compare_route.py`

- [ ] **Step 1: Create the partial**

Create `backend/app/templates/pages/_studio_compare_table.html`:

```html
{# Aligned scene compare table. Inputs:
     model            — build_output_compare() result | None (None => empty state)
     cur_version_num  — int | None  (for headings)
     cmp_version_num  — int | None
   diff_html(segs, side) is a Jinja global (services/word_diff.py).
#}
{% macro _pill(status) %}
  {% set _cls = {'unchanged': '', 'changed': 'changed', 'added': 'ok', 'removed': 'bad'} %}
  {% set _lbl = {'unchanged': 'UNCHANGED', 'changed': 'CHANGED', 'added': 'ADDED', 'removed': 'REMOVED'} %}
  <span class="pill {{ _cls[status] }}"><span class="led"></span>{{ _lbl[status] }}</span>
{% endmacro %}
{% if model is none %}
  <div class="muted">Click a clip in a folder to focus it.</div>
{% else %}
<div class="studio-compare-table">
  <div class="sct-head mono-cell muted">
    <span>SCENES &rarr; CatDV markers</span>
    <span class="grow"></span>
    <span>{{ model.scene_count }} aligned scene{{ '' if model.scene_count == 1 else 's' }}</span>
  </div>
  {% for row in model.scenes %}
    <div class="sct-row sct-st-{{ row.status }}" data-scene-key="{{ row.key }}">
      <div class="sct-cell sct-left">
        {% if row.cmp %}
          <div class="sct-tc mono">{{ row.cmp.tc }} &middot; {{ row.cmp.dur_s }}s</div>
          <div class="sct-text">{{ diff_html(row.segs, 'left') }}</div>
        {% else %}
          <div class="sct-empty muted">&mdash; no scene &mdash;</div>
        {% endif %}
      </div>
      <div class="sct-cell sct-right">
        {% if row.cur %}
          <div class="sct-rowhead">
            <span class="sct-tc mono">{{ row.cur.tc }} &middot; {{ row.cur.dur_s }}s</span>
            <span class="grow"></span>
            {{ _pill(row.status) }}
          </div>
          <div class="sct-text">{{ diff_html(row.segs, 'right') }}</div>
        {% else %}
          <div class="sct-empty muted">&mdash; no scene &mdash;</div>
        {% endif %}
      </div>
    </div>
  {% endfor %}

  {% if model.fields %}
  <div class="sct-subhead mono-cell muted">FIELDS</div>
  {% for f in model.fields %}
    <div class="sct-row sct-st-{{ f.status }}" data-scene-key="{{ f.key }}">
      <div class="sct-cell sct-left">
        {% if f.has_cmp %}
          <div class="sct-text"><span class="ident mono">{{ f.identifier }}</span>
            {{ diff_html(f.segs, 'left') }}</div>
        {% else %}<div class="sct-empty muted">&mdash; absent &mdash;</div>{% endif %}
      </div>
      <div class="sct-cell sct-right">
        {% if f.has_cur %}
          <div class="sct-rowhead">
            <span class="ident mono">{{ f.identifier }}</span>
            <span class="grow"></span>{{ _pill(f.status) }}
          </div>
          <div class="sct-text">{{ diff_html(f.segs, 'right') }}</div>
        {% else %}<div class="sct-empty muted">&mdash; absent &mdash;</div>{% endif %}
      </div>
    </div>
  {% endfor %}
  {% endif %}

  {% if model.notes %}
  <div class="sct-subhead mono-cell muted">NOTES</div>
  <div class="sct-notes mono">{{ diff_html(model.notes.segs, 'both') }}</div>
  {% endif %}
</div>
{% endif %}
```

- [ ] **Step 2: Write the failing route test**

Create `tests/integration/test_studio_compare_route.py`:

```python
"""GET /studio/_compare — aligned scene table partial."""

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


def _two_versions(client):
    r = client.post("/api/prompts", json={
        "name": "cmp", "media_kind": "any", "model": "gemini-2.5-pro",
        "target_map": {}, "output_schema": {}, "body": "v1",
    })
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    v1 = client.get(f"/api/prompts/{pid}").json()["latest_version_id"]
    client.post(f"/api/prompts/{pid}/versions/{v1}:promote")
    r = client.post(f"/api/prompts/{pid}/versions", json={"from_version_id": v1})
    v2 = r.json()["id"]
    return pid, v1, v2


def _seed_run_with_markers(client, version_id, clip_id, markers):
    """Insert a studio_run + marker review_items so panels render."""
    from backend.app import main as main_mod
    db_path = main_mod.app.state.core_ctx.settings.data_dir / "app.db"

    async def _seed():
        async with aiosqlite.connect(db_path) as db:
            cur = await db.execute(
                "INSERT INTO studio_run(prompt_version_id, clip_id, status, "
                "output_json, model, finished_at) VALUES (?, ?, 'ok', ?, "
                "'gemini-2.5-pro', '2026-05-27T00:00:00Z')",
                (version_id, clip_id, json.dumps({})),
            )
            run_id = cur.lastrowid
            for mk in markers:
                await db.execute(
                    "INSERT INTO review_item(studio_run_id, catdv_clip_id, kind, "
                    "proposed_value, decision) VALUES (?, ?, 'marker', ?, 'pending')",
                    (run_id, clip_id, json.dumps({
                        "name": mk["name"],
                        "in": {"secs": mk["in"], "frm": 0},
                        "out": {"secs": mk["out"], "frm": 0},
                    })),
                )
            await db.commit()
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_seed())
    finally:
        loop.close()


def test_empty_state_without_clip(client):
    _, v1, v2 = _two_versions(client)
    r = client.get(f"/studio/_compare?version_id={v2}&compare_id={v1}")
    assert r.status_code == 200
    assert "Click a clip" in r.text


def test_renders_aligned_scene_rows_with_status_and_diff(client):
    _, v1, v2 = _two_versions(client)
    _seed_run_with_markers(client, v1, 12041, [{"name": "Woman at bench", "in": 7, "out": 28}])
    _seed_run_with_markers(client, v2, 12041, [{"name": "Woman peeling potatoes", "in": 7, "out": 17}])
    r = client.get(f"/studio/_compare?version_id={v2}&compare_id={v1}&clip_id=12041")
    assert r.status_code == 200
    assert "studio-compare-table" in r.text
    assert "data-scene-key" in r.text
    assert "diff-ins" in r.text and "diff-del" in r.text
    assert "CHANGED" in r.text
    assert "aligned scene" in r.text


def test_added_scene_renders_no_scene_placeholder(client):
    _, v1, v2 = _two_versions(client)
    # cmp has no run (no markers); cur has one -> the scene is ADDED.
    _seed_run_with_markers(client, v2, 12041, [{"name": "New shot", "in": 0, "out": 5}])
    r = client.get(f"/studio/_compare?version_id={v2}&compare_id={v1}&clip_id=12041")
    assert r.status_code == 200
    assert "no scene" in r.text
    assert "ADDED" in r.text


def test_404_on_missing_version(client):
    _, v1, v2 = _two_versions(client)
    r = client.get(f"/studio/_compare?version_id=99999&compare_id={v1}&clip_id=12041")
    assert r.status_code == 404
```

> **Note for the implementer:** confirm the `review_item` table/column names
> against an existing seeder before running — see
> `tests/integration/test_studio_run_output_reuse.py` for the canonical INSERT.
> Adjust the INSERT in `_seed_run_with_markers` to match (column names, whether
> it is `review_item` vs `review_items`, and the studio-run FK column).

- [ ] **Step 3: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/integration/test_studio_compare_route.py -q`
Expected: FAIL (route 404/Not Found — endpoint doesn't exist yet).

- [ ] **Step 4: Add the route**

In `backend/app/routes/pages/studio.py`, add after `_studio_player` (near the
other `_studio_*` GET routes). Reuse the existing `_load_studio_panels`,
`get_core_ctx`, `_archive`, and `templates` already imported in that module:

```python
@router.get("/studio/_compare", response_class=HTMLResponse)
async def _studio_compare(
    request: Request,
    version_id: int,
    compare_id: int,
    clip_id: int | None = None,
):
    """Aligned scene compare table for (cur=version_id, cmp=compare_id) on a clip."""
    ctx = get_core_ctx(request)
    if clip_id is None:
        return templates.TemplateResponse(
            request, "pages/_studio_compare_table.html", {"model": None}
        )
    try:
        cur_v = await ctx.prompts_repo.get_version(ctx.db, version_id)
        cmp_v = await ctx.prompts_repo.get_version(ctx.db, compare_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="version not found")
    archive = _archive(request)
    _, cur_panels, _ = await _load_studio_panels(
        ctx, version=cur_v, clip_id=clip_id, archive=archive
    )
    _, cmp_panels, _ = await _load_studio_panels(
        ctx, version=cmp_v, clip_id=clip_id, archive=archive
    )
    from backend.app.services.output_compare import build_output_compare

    model = build_output_compare(cur_panels, cmp_panels)
    return templates.TemplateResponse(
        request,
        "pages/_studio_compare_table.html",
        {
            "model": model,
            "cur_version_num": cur_v.version_num,
            "cmp_version_num": cmp_v.version_num,
        },
    )
```

Ensure `from fastapi import HTTPException` is imported at the top of the module
(add it if absent — check the existing imports first).

- [ ] **Step 5: Run — expect PASS**

Run: `.venv/bin/python -m pytest tests/integration/test_studio_compare_route.py -q`
Expected: all PASS. If a seed INSERT errors, fix column names per the note in
Step 2 and re-run.

- [ ] **Step 6: Commit**

```bash
git add backend/app/templates/pages/_studio_compare_table.html \
        backend/app/routes/pages/studio.py \
        tests/integration/test_studio_compare_route.py
git commit -m "feat(studio): /studio/_compare aligned scene table partial + route

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Layout breakout — show the table when Output + comparing

**Files:**
- Modify: `backend/app/templates/pages/_studio_compare.html`
- Modify: `backend/app/templates/pages/_studio_prompt_card.html:94-104`
- Test: `tests/integration/test_studio_compare_layout.py`

- [ ] **Step 1: Write the failing layout test**

Create `tests/integration/test_studio_compare_layout.py`:

```python
"""The aligned table replaces the per-card Output panes only when comparing."""

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
    pid = r.json()["id"]
    v1 = client.get(f"/api/prompts/{pid}").json()["latest_version_id"]
    client.post(f"/api/prompts/{pid}/versions/{v1}:promote")
    v2 = client.post(f"/api/prompts/{pid}/versions",
                     json={"from_version_id": v1}).json()["id"]
    return pid, v1, v2


def test_compare_output_region_present_when_comparing(client):
    pid, v1, v2 = _two_versions(client)
    r = client.get(f"/studio?prompt_id={pid}&version_id={v2}&compare_version_id={v1}")
    html = r.text
    # Full-width compare-output region exists and is gated to output+compare.
    assert "studio-compare-output" in html
    assert "/studio/_compare?" in html
    # Per-card output panes are gated off while comparing.
    assert "compareVersionId === null" in html


def test_single_version_keeps_per_card_output(client):
    pid, _, v2 = _two_versions(client)
    r = client.get(f"/studio?prompt_id={pid}&version_id={v2}")
    html = r.text
    # No compare region rendered region content driver when not comparing is fine,
    # but the per-card output pane (run-slot) is still present.
    assert "run-slot" in html
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/integration/test_studio_compare_layout.py -q`
Expected: FAIL (`studio-compare-output` not in HTML).

- [ ] **Step 3: Gate the per-card output pane to non-compare**

In `backend/app/templates/pages/_studio_prompt_card.html`, change the output
pane's `x-show` (currently `x-show="mode === 'output'"` at line ~94) to:

```html
      <div x-show="mode === 'output' && compareVersionId === null"
           x-init="$nextTick(() => loadOutput())"
           x-effect="pendingRunSwap && loadOutput()">
```

(`compareVersionId` is already a getter on `studioPromptCard` — see
`studio.js`.)

- [ ] **Step 4: Add the full-width compare-output region**

In `backend/app/templates/pages/_studio_compare.html`, after the closing
`</div>` of `.studio-compare-row`, add:

```html
{# Full-width aligned scene table — shown only when comparing AND on the
   Output tab. Replaces the two per-card output panes (which gate themselves
   off via compareVersionId === null). Loads /studio/_compare and re-inits
   Alpine on the injected subtree (same pattern as studioPromptCard.loadOutput). #}
<div class="studio-compare-output"
     x-show="$store.studio.mode === 'output' && $store.studio.compareVersionId !== null"
     x-cloak
     x-data="{
       load() {
         const s = $store.studio;
         const slot = $refs.cmpSlot;
         if (!slot || s.compareVersionId === null) return;
         if (!s.focusedClipId) {
           slot.innerHTML = '<div class=\'muted\'>Click a clip in a folder to focus it.</div>';
           return;
         }
         fetch(`/studio/_compare?version_id=${s.activeVersionId}` +
               `&compare_id=${s.compareVersionId}&clip_id=${s.focusedClipId}`)
           .then(r => r.text())
           .then(h => { slot.innerHTML = h; window.htmxAlpine.reinit(slot); })
           .catch(err => {
             console.error('compare load failed', err);
             Alpine.store('toast').push(`Compare load failed: ${err.message || err}`,
                                        { level: 'error' });
           });
       }
     }"
     x-init="$nextTick(() => load())"
     x-effect="$store.studio.mode;
               ($store.studio.mode === 'output') && $store.studio.pendingRunSwap;
               $store.studio.focusedClipId; $store.studio.activeVersionId;
               $store.studio.compareVersionId; load()">
  <div class="run-slot" x-ref="cmpSlot"></div>
</div>
```

- [ ] **Step 5: Run — expect PASS**

Run: `.venv/bin/python -m pytest tests/integration/test_studio_compare_layout.py -q`
Expected: all PASS.

- [ ] **Step 6: Add minimal CSS so the region is full-width**

In `backend/app/static/app.css`, add near the other `.studio-compare*` rules:

```css
.studio-compare-output { grid-column: 1 / -1; overflow: auto; min-height: 0; }
```

(If `.studio-compare-row` is not a grid parent, instead use
`.studio-compare-output { width: 100%; overflow: auto; }` — verify against the
existing `.studio-compare` / `.studio-compare-row` layout in app.css.)

- [ ] **Step 7: Commit**

```bash
git add backend/app/templates/pages/_studio_compare.html \
        backend/app/templates/pages/_studio_prompt_card.html \
        backend/app/static/app.css \
        tests/integration/test_studio_compare_layout.py
git commit -m "feat(studio): show aligned table full-width on Output when comparing

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Timeline labels, status borders, shared scene keys

**Files:**
- Modify: `backend/app/routes/pages/studio.py` (`_build_overlay_row`, `_studio_player`)
- Modify: `backend/app/templates/pages/_player_overlay.html`
- Modify: `backend/app/templates/pages/_studio_player.html`
- Modify: `backend/app/static/app.css`
- Test: `tests/integration/test_studio_timeline_linkage.py`

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_studio_timeline_linkage.py`:

```python
"""Comparison timeline: scene labels, status classes, and shared keys."""

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


def _two_versions(client):
    r = client.post("/api/prompts", json={
        "name": "cmp", "media_kind": "any", "model": "gemini-2.5-pro",
        "target_map": {}, "output_schema": {}, "body": "v1",
    })
    pid = r.json()["id"]
    v1 = client.get(f"/api/prompts/{pid}").json()["latest_version_id"]
    client.post(f"/api/prompts/{pid}/versions/{v1}:promote")
    v2 = client.post(f"/api/prompts/{pid}/versions",
                     json={"from_version_id": v1}).json()["id"]
    return pid, v1, v2


def _seed(client, version_id, clip_id, markers):
    from backend.app import main as main_mod
    db_path = main_mod.app.state.core_ctx.settings.data_dir / "app.db"

    async def _do():
        async with aiosqlite.connect(db_path) as db:
            cur = await db.execute(
                "INSERT INTO studio_run(prompt_version_id, clip_id, status, "
                "output_json, model, finished_at) VALUES (?, ?, 'ok', ?, "
                "'gemini-2.5-pro', '2026-05-27T00:00:00Z')",
                (version_id, clip_id, json.dumps({})),
            )
            rid = cur.lastrowid
            for mk in markers:
                await db.execute(
                    "INSERT INTO review_item(studio_run_id, catdv_clip_id, kind, "
                    "proposed_value, decision) VALUES (?, ?, 'marker', ?, 'pending')",
                    (rid, clip_id, json.dumps({
                        "name": mk["name"],
                        "in": {"secs": mk["in"], "frm": 0},
                        "out": {"secs": mk["out"], "frm": 0},
                    })),
                )
            await db.commit()
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_do())
    finally:
        loop.close()


def test_timeline_has_labels_status_and_scene_keys_when_comparing(client):
    _, v1, v2 = _two_versions(client)
    _seed(client, v1, 12041, [{"name": "Woman at bench", "in": 7, "out": 28}])
    _seed(client, v2, 12041, [{"name": "Woman peeling potatoes", "in": 7, "out": 17}])
    r = client.get(f"/studio/_player?clip_id=12041&version_id={v2}&compare_id={v1}")
    assert r.status_code == 200
    html = r.text
    assert "range-label" in html           # visible label text
    assert "data-scene-key" in html        # linkage key on ranges
    assert "range-st-" in html             # status class
    assert "Woman" in html                 # actual scene name rendered


def test_scene_keys_match_table(client):
    _, v1, v2 = _two_versions(client)
    _seed(client, v1, 12041, [{"name": "A", "in": 7, "out": 28}])
    _seed(client, v2, 12041, [{"name": "B", "in": 7, "out": 17}])
    tl = client.get(f"/studio/_player?clip_id=12041&version_id={v2}&compare_id={v1}").text
    tbl = client.get(f"/studio/_compare?version_id={v2}&compare_id={v1}&clip_id=12041").text
    assert 'data-scene-key="scene-0"' in tl
    assert 'data-scene-key="scene-0"' in tbl
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/integration/test_studio_timeline_linkage.py -q`
Expected: FAIL (`range-label` / `data-scene-key` absent from the overlay).

- [ ] **Step 3: Add the compare-aware row builder**

In `backend/app/routes/pages/studio.py`, add after `_build_overlay_row`:

```python
async def _overlay_rows_from_compare(
    ctx, clip_id: int, *, cur_version_id: int, cmp_version_id: int, archive
) -> list[dict]:
    """Build both timeline rows from the shared compare model so each range
    carries the same scene_key + status as the compare table."""
    from backend.app.services.output_compare import build_output_compare

    try:
        cur_v = await ctx.prompts_repo.get_version(ctx.db, cur_version_id)
        cmp_v = await ctx.prompts_repo.get_version(ctx.db, cmp_version_id)
    except LookupError:
        return []
    _, cur_panels, _ = await _load_studio_panels(
        ctx, version=cur_v, clip_id=clip_id, archive=archive
    )
    _, cmp_panels, _ = await _load_studio_panels(
        ctx, version=cmp_v, clip_id=clip_id, archive=archive
    )
    model = build_output_compare(cur_panels, cmp_panels)
    cur_ranges: list[dict] = []
    cmp_ranges: list[dict] = []
    for row in model["scenes"]:
        if row["cur"]:
            cur_ranges.append({
                "in_secs": row["cur"]["in_secs"], "out_secs": row["cur"]["out_secs"],
                "name": row["cur"]["name"], "scene_key": row["key"],
                "status": row["status"],
            })
        if row["cmp"]:
            cmp_ranges.append({
                "in_secs": row["cmp"]["in_secs"], "out_secs": row["cmp"]["out_secs"],
                "name": row["cmp"]["name"], "scene_key": row["key"],
                "status": row["status"],
            })
    return [
        {"key": f"v{cur_v.version_num}", "ranges": cur_ranges,
         "cls": "range-cur", "alpine_list": None, "x_show": None},
        {"key": f"v{cmp_v.version_num}", "ranges": cmp_ranges,
         "cls": "range-cmp", "alpine_list": None, "x_show": None},
    ]
```

Then in `_studio_player`, replace the `rows` assembly block (the
`rows: list[dict] = []` ... two `if` appends) with:

```python
    rows: list[dict] = []
    if version_id is not None and compare_id is not None:
        rows = await _overlay_rows_from_compare(
            ctx, clip_id, cur_version_id=version_id,
            cmp_version_id=compare_id, archive=archive,
        )
    else:
        if version_id is not None:
            row = await _build_overlay_row(ctx, clip_id, version_id, cls="range-cur")
            if row is not None:
                rows.append(row)
        if compare_id is not None:
            row = await _build_overlay_row(ctx, clip_id, compare_id, cls="range-cmp")
            if row is not None:
                rows.append(row)
```

- [ ] **Step 4: Render labels + status + key in the overlay**

In `backend/app/templates/pages/_player_overlay.html`, in the **non-draft**
`{% else %}` range branch (the plain `<div class="range...">`), replace it with:

```html
        <div class="range{% if 'range-draft' in row.cls %} draft-range{% endif %}{% if m.status is defined and m.status %} range-st-{{ m.status }}{% endif %}"
             {% if m.scene_key is defined and m.scene_key %}data-scene-key="{{ m.scene_key }}"{% endif %}
             {% if row.alpine_list %}:class="{ active: isMarkerActive({{ row.alpine_list }}[{{ loop.index0 }}]) }"{% endif %}
             style="left: {{ (m.in_secs / duration_secs) * 100 }}%; width: {{ (((m.out_secs or m.in_secs + 1) - m.in_secs) / duration_secs) * 100 }}%"
             title="{{ m.name }}">
          {% if show_range_labels is defined and show_range_labels and m.name %}<span class="range-label">{{ m.name }}</span>{% endif %}
        </div>
```

- [ ] **Step 5: Pass `show_range_labels` from the studio player**

In `backend/app/templates/pages/_studio_player.html`, inside the `{% with %}`
block that sets `show_legend = True`, add a line:

```html
      show_range_labels = True,
```

- [ ] **Step 6: Add timeline CSS**

In `backend/app/static/app.css`, add near the `.timeline .range` rules:

```css
.range-label {
  display: block; padding: 0 5px; font-size: 11px; line-height: 18px;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; color: var(--text);
}
.timeline .range.range-st-unchanged { border-color: var(--info); }
.timeline .range.range-st-changed   { border-color: var(--changed); }
.timeline .range.range-st-added     { border-color: var(--good); }
.timeline .range.range-st-removed   { border-color: var(--bad); }
```

(The `--changed` token is added in Task 6 Step 4; if running this task first,
add `--changed: #b794f6;` to `:root` now.)

- [ ] **Step 7: Run — expect PASS**

Run: `.venv/bin/python -m pytest tests/integration/test_studio_timeline_linkage.py -q`
Expected: all PASS.

- [ ] **Step 8: Commit**

```bash
git add backend/app/routes/pages/studio.py \
        backend/app/templates/pages/_player_overlay.html \
        backend/app/templates/pages/_studio_player.html \
        backend/app/static/app.css \
        tests/integration/test_studio_timeline_linkage.py
git commit -m "feat(studio): labeled, status-colored compare timeline with shared scene keys

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: Bidirectional selection linkage (store + vanilla bridge + styling)

**Files:**
- Modify: `backend/app/static/studioStore.js`
- Create: `backend/app/static/studioSceneLink.js`
- Modify: `backend/app/templates/pages/studio.html`
- Modify: `backend/app/static/app.css`
- Test: `tests/unit/test_studio_scene_link.py`

- [ ] **Step 1: Add `selectedSceneKey` to the store**

In `backend/app/static/studioStore.js`, in the `Alpine.store('studio', { ... })`
state block (e.g. right after `savedTick: 0,`), add:

```javascript
    // Scene-compare linkage: the scene-key currently hovered/selected in the
    // compare table or on the timeline. The vanilla bridge (studioSceneLink.js)
    // keeps this in sync and toggles `.is-linked` on matching DOM nodes.
    selectedSceneKey: null,
```

- [ ] **Step 2: Create the vanilla bridge**

Create `backend/app/static/studioSceneLink.js`:

```javascript
/* Scene-compare linkage bridge (vanilla).

   The compare table rows (_studio_compare_table.html) and the timeline ranges
   (_player_overlay.html) both carry `data-scene-key`. Both are injected via
   HTMX innerHTML, where Alpine directives on directive-less subtrees don't
   reliably wire up (see studio.js window.studio shim rationale). So linkage is
   plain delegated DOM events: hovering any [data-scene-key] highlights every
   element with the same key (table row + timeline blocks), and mirrors the key
   into Alpine.store('studio').selectedSceneKey for any reactive consumers. */
(function () {
  function applyLinked(key) {
    document.querySelectorAll('[data-scene-key].is-linked')
      .forEach((el) => el.classList.remove('is-linked'));
    if (!key) return;
    document.querySelectorAll(`[data-scene-key="${CSS.escape(key)}"]`)
      .forEach((el) => el.classList.add('is-linked'));
  }

  function setKey(key) {
    applyLinked(key);
    const store = window.Alpine && window.Alpine.store('studio');
    if (store) store.selectedSceneKey = key;
  }

  document.addEventListener('mouseover', (evt) => {
    const el = evt.target.closest('[data-scene-key]');
    if (el) setKey(el.getAttribute('data-scene-key'));
  });
  document.addEventListener('mouseout', (evt) => {
    const el = evt.target.closest('[data-scene-key]');
    if (el) setKey(null);
  });

  window.studioSceneLink = { setKey, applyLinked };
})();
```

- [ ] **Step 3: Include the script**

In `backend/app/templates/pages/studio.html`, next to
`<script src="/static/studio-diff.js"></script>`, add:

```html
  <script src="/static/studioSceneLink.js"></script>
```

- [ ] **Step 4: Add token + linkage + pill CSS**

In `backend/app/static/app.css`, add `--changed` to `:root` (near `--good` /
`--bad`):

```css
  --changed:   #b794f6;
```

And add the CHANGED pill variant (next to `.pill.bad`) and the linkage
highlight:

```css
.pill.changed { color: var(--changed);
  border-color: color-mix(in oklab, var(--changed) 35%, transparent); }
.pill.changed .led { background: var(--changed); box-shadow: 0 0 6px var(--changed); }

[data-scene-key].is-linked { outline: 2px solid var(--accent);
  outline-offset: -2px; }
.timeline .range[data-scene-key].is-linked { filter: brightness(1.25); }
```

Also add the compare-table layout (place with the other `.studio-*` rules):

```css
.studio-compare-table { display: flex; flex-direction: column; gap: 8px; padding: 10px; }
.sct-head, .sct-subhead { display: flex; align-items: center; padding: 2px 4px; }
.sct-row { display: grid; grid-template-columns: 1fr 1fr; gap: 10px;
  border: 1px solid var(--surface-2); border-radius: 8px; padding: 10px; }
.sct-cell { min-width: 0; }
.sct-rowhead { display: flex; align-items: center; gap: 8px; margin-bottom: 4px; }
.sct-tc { color: var(--text-3); font-size: 12px; }
.sct-text { white-space: pre-wrap; }
.sct-empty { font-style: italic;
  background: repeating-linear-gradient(45deg, transparent, transparent 6px,
    color-mix(in oklab, var(--text-4) 18%, transparent) 6px,
    color-mix(in oklab, var(--text-4) 18%, transparent) 12px);
  border-radius: 6px; padding: 10px; }
.sct-notes { white-space: pre-wrap; padding: 10px; border: 1px solid var(--surface-2);
  border-radius: 8px; }
.grow { flex: 1 1 auto; }
```

(Verify `.grow` isn't already defined globally; if it is, drop that line.)

- [ ] **Step 5: Write a structural test**

Create `tests/unit/test_studio_scene_link.py`:

```python
"""Scene-link bridge wiring (static-asset structural checks)."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LINK_JS = (ROOT / "backend" / "app" / "static" / "studioSceneLink.js").read_text()
STORE_JS = (ROOT / "backend" / "app" / "static" / "studioStore.js").read_text()
STUDIO_HTML = (ROOT / "backend" / "app" / "templates" / "pages" / "studio.html").read_text()


def test_store_has_selected_scene_key():
    assert "selectedSceneKey" in STORE_JS


def test_bridge_toggles_is_linked_by_data_scene_key():
    assert "data-scene-key" in LINK_JS
    assert "is-linked" in LINK_JS
    assert "selectedSceneKey" in LINK_JS


def test_studio_page_includes_bridge_script():
    assert "studioSceneLink.js" in STUDIO_HTML
```

- [ ] **Step 6: Run — expect PASS**

Run: `.venv/bin/python -m pytest tests/unit/test_studio_scene_link.py -q`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/app/static/studioStore.js backend/app/static/studioSceneLink.js \
        backend/app/templates/pages/studio.html backend/app/static/app.css \
        tests/unit/test_studio_scene_link.py
git commit -m "feat(studio): bidirectional scene↔timeline selection highlight

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: Full-suite regression + manual acceptance

**Files:** none (verification only)

- [ ] **Step 1: Run the studio + diff suites**

Run:
```bash
.venv/bin/python -m pytest tests/unit -k "studio or word_diff or output_compare or scene_link or anno_panels" \
  --ignore=tests/unit/test_thumbnail_service_image.py -q
.venv/bin/python -m pytest tests/integration -k "studio" -q
```
Expected: all PASS. Fix any regression before proceeding (likely culprits: the
`_studio_prompt_card` output-pane `x-show` change, or the `_studio_player` rows
branch).

- [ ] **Step 2: Run the architecture guards**

Run:
```bash
.venv/bin/python -m pytest tests/unit/test_templates_shared.py \
  tests/unit/test_no_sync_fs_in_async.py tests/unit/test_no_x_data_stack.py \
  tests/unit/test_htmx_alpine_single_lifecycle.py -q
.venv/bin/lint-imports
```
Expected: all PASS (the new code uses the shared `templates` env, adds no sync
fs in async, no `_x_dataStack`, and routes import no `httpx`).

- [ ] **Step 3: Manual acceptance (running app)**

Start the server via the `server-start` skill (single-seat discipline). Then
walk the five **Manual acceptance flows** in
`docs/specs/2026-06-02-studio-aligned-scene-compare-design.md`:
1. Aligned table replaces Output when comparing (word highlights, status pills,
   "— no scene —", "N aligned scenes").
2. Prompt-tab diff unchanged.
3. Timeline shows labeled, status-colored scenes (green added, purple changed,
   blue unchanged).
4. Hover/click a table row highlights the matching timeline block and vice
   versa.
5. Single-version Output (no compare) still shows the per-card panels.

Stop the server via the `server-stop` skill (graceful SIGTERM — frees the seat).

- [ ] **Step 4: Write the ADR**

Create `docs/adr/0052-studio-aligned-scene-compare.md` (MADR-lite: Context /
Alternatives / Decision / Consequences) capturing: server-side alignment +
`word_diff` promotion (vs client-side), the greedy time-overlap alignment and
its 1→N limitation, the table-replaces-Output-when-comparing layout, and the
vanilla `data-scene-key` linkage bridge (vs Alpine on HTMX-injected nodes). Add
the row to `docs/decisions.md`.

- [ ] **Step 5: Commit the ADR**

```bash
git add docs/adr/0052-studio-aligned-scene-compare.md docs/decisions.md
git commit -m "docs(adr): 0052 studio aligned scene compare + linked timeline

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Self-Review notes (for the implementer)

- **Seed schema:** the `review_item` INSERTs in Tasks 3 & 5 are written from the
  draft_view shape (`kind='marker'`, `proposed_value` with `in/out.secs`). Before
  first run, diff them against `tests/integration/test_studio_run_output_reuse.py`
  / `test_studio_prompt_card_route.py` (which already seed runs) and fix table /
  column names (`review_item` vs `review_items`, the studio-run FK column name).
  This is the single most likely break point.
- **`get_version` signature:** Task 3/5 assume `ctx.prompts_repo.get_version(ctx.db, id)`
  raises `LookupError` when missing — confirmed by `_build_overlay_row`. Mirror it.
- **`compareVersionId` getter:** Task 4 relies on `studioPromptCard.compareVersionId`
  existing — it does (`studio.js`).
- **CSS `.grow`:** used in the partial header; if not already global, the Task 6
  rule defines it. Don't double-define.
```
