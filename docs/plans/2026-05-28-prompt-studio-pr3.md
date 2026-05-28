# Prompt Studio — PR3 (polish) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the umbrella spec's PR3 polish slice — Run-button state machine with cancel + ✓ Done flash, consistent empty/error shells across the right pane, and a visual audit that swaps phantom-token fallbacks and raw rgba colors for real `:root` tokens.

**Architecture:** No new endpoints, no new templates, no new JS files. The cancel flow reuses `POST /api/jobs/{job_id}/cancel`. The state machine adds three fields and three methods to `studioPage()` in `studio.js` and a single computed label. The CSS audit is a tight list of replacements in `app.css` plus two new `:root` tokens (`--range-cur`, `--range-cmp`). The folder-list partial migrates one inline-styled input to `.txt sm` and two buttons to `ui.button(...)`. A pure-Python mirror of the JS label state machine lives in `tests/_helpers/studio_state.py` so future changes touch one place.

**Tech Stack:** Python 3.13, FastAPI, aiosqlite (SQLite), Jinja2, Alpine.js v3, HTMX, pytest, ruff, basedpyright.

**Spec:** `docs/specs/2026-05-28-prompt-studio-pr3-design.md`
**Predecessor plan:** `docs/plans/2026-05-27-prompt-studio-pr2.md`
**Predecessor ADRs:** 0033, 0034, 0037, 0038

---

## File map

**Create:**

- `tests/_helpers/__init__.py` — empty if not already present
- `tests/_helpers/studio_state.py` — pure-Python mirror of `runButtonLabel()`
- `tests/unit/test_studio_run_button_label.py` — label state-machine unit tests
- `tests/unit/test_studio_css_no_phantom_tokens.py` — CSS audit gate (grep)
- `tests/unit/test_studio_css_has_range_tokens.py` — `:root` contains new tokens
- `tests/integration/test_studio_run_button_state.py` — header template renders state-aware bindings
- `tests/integration/test_studio_run_output_empty_states.py` — `_studio_run_output.html` shells
- `tests/integration/test_studio_player_persists_during_run.py` — player slot survives run swaps
- `docs/adr/0039-prompt-studio-pr3-polish.md` — ADR

**Modify:**

- `backend/app/static/app.css`
  - `:root`: add `--range-cur`, `--range-cmp`
  - lines ~1979, 1989, 1990, 1998, 2009, 2010, 2024, 2025, 2027: swap phantom-token fallbacks
  - lines ~2001, 2004, 2014, 2016, 2018, 2019: swap raw rgba colors to tokens / color-mix
  - add `.run-empty`, `.run-error`, `.run-error-h`, `.run-error-msg` rules
  - add `.studio-folder-new` rule
  - add `:focus-visible` rules for `.pc-vchip .btn`, `.pc-vmenu-item`, `.btn-diff-toggle`, `.studio-clip-card`
- `backend/app/static/studio.js`
  - `studioPage()`: add `cancelling`, `doneFlashUntilMs`, `runJobId` state; add `runButtonLabel()` getter; add `runOrCancel()`, `cancel()` methods; capture `job_id` in `runOnFocusedClip()` and set `doneFlashUntilMs` on success; tick at 1Hz; bail from `_poll` early when `!running`
- `backend/app/templates/pages/_studio_header.html`
  - Replace the dual `<template x-if>` block with a single `<span x-text="runButtonLabel()">`
  - Switch `@click="runOnFocusedClip()"` → `@click="runOrCancel()"`
  - Update `:disabled` to include `cancelling || doneFlashUntilMs > 0`
- `backend/app/templates/pages/_studio_run_output.html`
  - Remove `.muted` on `.run-empty` divs (the new `.run-empty` rule supplies the tint); drop the inline `style="padding:6px 0;"` on `.run-stats`
- `backend/app/templates/pages/_studio_folder_list.html`
  - "+ New folder" and "Create" buttons → use the same `.btn ghost sm` / `.btn primary sm` shape via `ui.button(...)`
  - New-folder wrapper `style="..."` → `class="studio-folder-new"`
  - `<input>` `style="..."` → bare `<input class="txt sm">`
  - Empty-state div uses `.muted` without inline padding (let parent space it via a small CSS rule)
- `backend/app/templates/pages/_studio_compare.html`
  - `style="display:none"` on `.cmp-slot` → `x-show="false"` initial value via Alpine state, OR drop the attribute and let the `{% if compare_version %}` guard handle SSR — keep cmp-slot empty when no compare; add `x-cloak` to avoid flash
- `docs/design-language.md` — one paragraph naming `--range-cur` / `--range-cmp`
- `docs/decisions.md` — append ADR 0039 row

**No changes:**

- `backend/app/routes/studio.py` (POST `/api/studio/runs` already returns `job_id`)
- `backend/app/routes/jobs.py` (cancel endpoint already exists at `POST /{job_id}/cancel`)
- `backend/app/services/annotator.py` (already cooperative-cancels)
- `_studio_prompt_card.html`, `_studio_player.html`, `_studio_version_picker.html`, `_studio_clip_card.html`, `_studio_diff.html` (all in scope only for the focus-ring rules in `app.css`)

---

## TDD discipline

Every task follows: **red test → verify red → minimal impl → verify green → commit**. CSS audits use `grep`-style assertions against the served `app.css` text. Pure-state JS logic is mirrored in Python so the state machine is unit-testable without a browser. Manual-verification-only steps are called out explicitly per task. Commit after every green step — small commits make review trivial and roll-back easy.

Run unit tests with `.venv/bin/pytest -q <path>`. Run the whole suite once at the end with `.venv/bin/python -m pytest -x -q`.

---

## Task 1: CSS — add range tokens and audit gate

Define `--range-cur` / `--range-cmp` in `:root` and add a test that fails if anyone re-introduces a phantom-token fallback or raw rgba range color.

**Files:**
- Create: `tests/unit/test_studio_css_has_range_tokens.py`
- Create: `tests/unit/test_studio_css_no_phantom_tokens.py`
- Modify: `backend/app/static/app.css`

- [ ] **Step 1: Write the failing token-existence test**

`tests/unit/test_studio_css_has_range_tokens.py`:

```python
"""The :root block must define the studio range overlay tokens used by
.ranges.range-cur and .ranges.range-cmp. PR3 introduces these so the
range colors track the palette instead of hardcoded rgba."""

from pathlib import Path


def test_root_defines_range_cur_and_range_cmp():
    css = Path("backend/app/static/app.css").read_text()
    # The :root block lives at the top of the file.
    root_block = css.split(":root", 1)[1].split("}", 1)[0]
    assert "--range-cur:" in root_block, "missing --range-cur in :root"
    assert "--range-cmp:" in root_block, "missing --range-cmp in :root"


def test_range_tokens_use_color_mix_with_existing_tokens():
    css = Path("backend/app/static/app.css").read_text()
    root_block = css.split(":root", 1)[1].split("}", 1)[0]
    # Spec locks --range-cur to color-mix(... var(--info) ...) and
    # --range-cmp to color-mix(... var(--accent) ...).
    cur_line = next(
        ln for ln in root_block.splitlines() if "--range-cur" in ln
    )
    cmp_line = next(
        ln for ln in root_block.splitlines() if "--range-cmp" in ln
    )
    assert "color-mix" in cur_line and "var(--info)" in cur_line
    assert "color-mix" in cmp_line and "var(--accent)" in cmp_line
```

- [ ] **Step 2: Write the failing phantom-token gate**

`tests/unit/test_studio_css_no_phantom_tokens.py`:

```python
"""Phantom-token fallbacks (e.g. var(--bg-3, #1f1f1f) where --bg-3 has
never been defined in :root) silently render the hex fallback forever
and lie about being design-system compliant. PR3 removes them; this
gate prevents regressions."""

import re
from pathlib import Path

PHANTOM_TOKENS = (
    "--bg-3",
    "--accent-fade",
    "--border",
    "--fg-muted",
)

# These raw rgba strings were studio-specific; PR3 replaces them with
# tokens / color-mix(). Re-introducing them would mean the audit was
# undone.
BANNED_RGBA = (
    "rgba(74, 144, 226",
    "rgba(220, 140, 60",
    "rgba(220, 60, 60",
    "rgba(60, 180, 90",
)


def _root_defines(css: str, name: str) -> bool:
    root_block = css.split(":root", 1)[1].split("}", 1)[0]
    return re.search(rf"^\s*{re.escape(name)}\s*:", root_block, re.MULTILINE) is not None


def test_no_phantom_token_fallbacks():
    css = Path("backend/app/static/app.css").read_text()
    for tok in PHANTOM_TOKENS:
        if _root_defines(css, tok):
            # Real token — fallback is fine.
            continue
        # Otherwise no `var(<tok>, ...)` occurrence is allowed.
        pattern = re.compile(rf"var\(\s*{re.escape(tok)}\b")
        for m in pattern.finditer(css):
            line_no = css[: m.start()].count("\n") + 1
            raise AssertionError(
                f"phantom-token fallback for {tok} at app.css:{line_no} — "
                f"PR3 spec mandates replacement with a real token."
            )


def test_no_raw_studio_rgba_colors():
    css = Path("backend/app/static/app.css").read_text()
    for needle in BANNED_RGBA:
        if needle in css:
            line_no = css[: css.index(needle)].count("\n") + 1
            raise AssertionError(
                f"raw rgba color {needle!r} reintroduced at app.css:{line_no} — "
                f"PR3 spec replaced these with tokens / color-mix()."
            )
```

- [ ] **Step 3: Run both tests to verify they fail**

```bash
.venv/bin/pytest -q tests/unit/test_studio_css_has_range_tokens.py tests/unit/test_studio_css_no_phantom_tokens.py
```

Expected: **FAIL** on both files — `--range-cur` / `--range-cmp` are not defined; phantom-token fallbacks at lines 1979, 1989, 1990, 1998, 2009, 2010, 2024, 2025, 2027 still exist; the four raw rgba strings still exist.

- [ ] **Step 4: Add the two tokens to `:root`**

In `backend/app/static/app.css`, inside the `:root { ... }` block, after the `--info` line, add:

```css
  --range-cur: color-mix(in oklab, var(--info)   45%, transparent);
  --range-cmp: color-mix(in oklab, var(--accent) 45%, transparent);
```

- [ ] **Step 5: Replace phantom-token fallbacks**

Edit `backend/app/static/app.css` at each line listed below. Find each `var(...)` and rewrite it to the new value.

| Approx line | Old | New |
|---|---|---|
| 1979 | `background: var(--bg-2, #161616); border: 1px solid var(--border, #2a2a2a);` | `background: var(--panel-2); border: 1px solid var(--line);` |
| 1989 | `var(--bg-3, #1f1f1f)` | `var(--hover)` |
| 1990 | `var(--accent-fade, #2b3a4d)` | `var(--accent-2)` |
| 1998 | `var(--border, #1f1f1f)` | `var(--line)` |
| 2009 | `var(--accent, #4a90e2)` | `var(--accent)` |
| 2010 | `var(--accent-fade, #2b3a4d)` | `var(--accent-2)` |
| 2024 | `var(--border, #1f1f1f)` | `var(--line)` |
| 2025 | `var(--bg-2, #0f1216)` | `var(--bg-2)` |
| 2027 | `var(--fg-muted, #888)` | `var(--text-3)` |

- [ ] **Step 6: Replace the four raw studio rgba colors**

In `backend/app/static/app.css`:

```css
/* line ~2001 */
.diff-row.diff-del .diff-cell-a { background: color-mix(in oklab, var(--bad)  18%, transparent); }
/* line ~2004 */
.diff-row.diff-ins .diff-cell-b { background: color-mix(in oklab, var(--good) 18%, transparent); }

/* line ~2014 */
.ranges.range-cur .range { background: var(--range-cur); }
/* line ~2016 */
.ranges.range-cmp .range { background: var(--range-cmp); }
/* line ~2018 */
.legend-range-cur { color: var(--info); }
/* line ~2019 */
.legend-range-cmp { color: var(--accent); }
```

- [ ] **Step 7: Run both tests to verify they pass**

```bash
.venv/bin/pytest -q tests/unit/test_studio_css_has_range_tokens.py tests/unit/test_studio_css_no_phantom_tokens.py
```

Expected: **PASS**.

- [ ] **Step 8: Commit**

```bash
git add backend/app/static/app.css \
        tests/unit/test_studio_css_has_range_tokens.py \
        tests/unit/test_studio_css_no_phantom_tokens.py
git commit -m "refactor(studio,css): add --range-cur/--range-cmp tokens; drop phantom-token fallbacks"
```

---

## Task 2: CSS — `.run-empty` and `.run-error` shells

Add real styling to the four right-pane empty / error states so they're visually consistent. The markup stays the same; the classes pick up dedicated rules.

**Files:**
- Modify: `backend/app/static/app.css`
- Modify: `backend/app/templates/pages/_studio_run_output.html`
- Create: `tests/integration/test_studio_run_output_empty_states.py`

- [ ] **Step 1: Write the failing integration test**

`tests/integration/test_studio_run_output_empty_states.py`:

```python
"""The right-pane empty / error states all render inside the dedicated
.run-empty / .run-error shells. PR3 adds visible styling to those
shells; the markup contract here is the regression guard."""

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


def _make_prompt(client):
    r = client.post("/api/prompts", json={
        "name": "ee", "media_kind": "any", "model": "gemini-2.5-pro",
        "target_map": {}, "output_schema": {}, "body": "x",
    })
    return r.json()["prompt_id"], r.json()["version_id"]


def _seed_run(app, *, version_id, clip_id, status, output_json=None, error=None):
    async def _go():
        async with aiosqlite.connect(app.state.ctx.db_path) as db:
            await db.execute(
                "INSERT INTO studio_run(prompt_version_id, clip_id, status, "
                "output_json, error, model, finished_at) "
                "VALUES (?, ?, ?, ?, ?, 'gemini-2.5-pro', '2026-05-28T00:00:00Z')",
                (version_id, clip_id, status,
                 json.dumps(output_json) if output_json else None,
                 error),
            )
            await db.commit()
    asyncio.get_event_loop().run_until_complete(_go())


def test_no_version_renders_run_empty_shell(client):
    r = client.get("/studio/_run?prompt_version_id=9999&clip_id=12041")
    assert r.status_code == 200
    assert 'class="run-empty"' in r.text or 'class="run-empty muted"' in r.text
    assert "Unknown version" in r.text


def test_no_run_renders_run_empty_shell(client):
    _, vid = _make_prompt(client)
    r = client.get(f"/studio/_run?prompt_version_id={vid}&clip_id=99999")
    assert r.status_code == 200
    assert "run-empty" in r.text
    assert "No run yet" in r.text


def test_pending_run_renders_run_empty_shell(client):
    _, vid = _make_prompt(client)
    from backend.app import main as main_mod
    _seed_run(main_mod.app, version_id=vid, clip_id=12041, status="pending")
    r = client.get(f"/studio/_run?prompt_version_id={vid}&clip_id=12041")
    assert r.status_code == 200
    assert "run-empty" in r.text
    assert "Running" in r.text


def test_error_run_renders_run_error_shell(client):
    _, vid = _make_prompt(client)
    from backend.app import main as main_mod
    _seed_run(main_mod.app, version_id=vid, clip_id=12041,
              status="error", error="Gemini API: 503 backend overloaded")
    r = client.get(f"/studio/_run?prompt_version_id={vid}&clip_id=12041")
    assert r.status_code == 200
    assert 'class="run-error"' in r.text
    assert 'class="run-error-h"' in r.text
    assert 'class="run-error-msg"' in r.text
    assert "Gemini API: 503 backend overloaded" in r.text


def test_error_run_message_is_selectable(client):
    """Error messages must use user-select: text (PR3 polish). The
    enclosing CSS rule for .run-error-msg includes user-select: text."""
    from pathlib import Path
    css = Path("backend/app/static/app.css").read_text()
    # Locate the .run-error-msg block.
    assert ".run-error-msg" in css, "missing .run-error-msg rule"
    # Find the rule block and assert user-select: text is in it.
    rule_start = css.index(".run-error-msg")
    rule_end = css.index("}", rule_start)
    assert "user-select" in css[rule_start:rule_end]
    assert "word-break" in css[rule_start:rule_end]
```

- [ ] **Step 2: Run test to verify it fails**

```bash
.venv/bin/pytest -q tests/integration/test_studio_run_output_empty_states.py
```

Expected: **FAIL** — `.run-error-h` / `.run-error-msg` classes exist in the markup already (added in the post-PR2 work), but the CSS rules don't define `user-select` / `word-break` yet. The first four tests should pass on the existing markup; the fifth fails.

If any of the first four ALSO fail, it's because the partial wraps the divs in `class="run-empty muted"` and the test substring check accepts either — confirm the assertion uses `"run-empty" in r.text`. Adjust if a different shape is in place.

- [ ] **Step 3: Add the CSS rules**

In `backend/app/static/app.css`, find the `/* === Studio: prompt-card diff view */` block (~line 1992) and just before it (or after the version-chip block — exact placement is editorial) add:

```css
/* === Studio: right-pane empty / error shells ========================= */
.run-empty {
  padding: 12px 14px;
  color: var(--text-3);
  font-size: 12px;
  line-height: 1.5;
}
.run-empty b { color: var(--text-2); font-weight: 600; }

.run-error {
  padding: 12px 14px;
  background: var(--panel-2);
  border-left: 3px solid var(--bad);
  border-radius: var(--r-2);
}
.run-error-h {
  font-size: 12px;
  color: var(--text-2);
  margin-bottom: 4px;
}
.run-error-h b { color: var(--bad); font-weight: 600; }
.run-error-msg {
  font-family: var(--f-mono);
  font-size: 12px;
  color: var(--text);
  white-space: pre-wrap;
  word-break: break-word;
  user-select: text;
}

.run-stats { padding: 6px 0; }
```

- [ ] **Step 4: Drop the inline `style=` on `.run-stats`**

In `backend/app/templates/pages/_studio_run_output.html`, change line 33 from:

```jinja
  <div class="run-stats mono-cell muted" style="padding:6px 0;">
```

to:

```jinja
  <div class="run-stats mono-cell muted">
```

(The CSS rule above supplies the padding.)

- [ ] **Step 5: Run tests to verify they pass**

```bash
.venv/bin/pytest -q tests/integration/test_studio_run_output_empty_states.py
```

Expected: **PASS** (5 tests).

- [ ] **Step 6: Commit**

```bash
git add backend/app/static/app.css \
        backend/app/templates/pages/_studio_run_output.html \
        tests/integration/test_studio_run_output_empty_states.py
git commit -m "feat(studio,css): polished .run-empty / .run-error shells"
```

---

## Task 3: Run-button state machine — pure-Python mirror + unit tests

Define the label state machine as a single function so we can unit-test it deterministically, then mirror that function in `studio.js` in Task 4.

**Files:**
- Create: `tests/_helpers/__init__.py` (empty, only if missing)
- Create: `tests/_helpers/studio_state.py`
- Create: `tests/unit/test_studio_run_button_label.py`

- [ ] **Step 1: Verify `tests/_helpers/` exists**

```bash
ls tests/_helpers/ 2>/dev/null || mkdir -p tests/_helpers && touch tests/_helpers/__init__.py
```

If it didn't exist, the empty `__init__.py` makes it importable. If it did exist, leave it alone.

- [ ] **Step 2: Write the failing unit tests**

`tests/unit/test_studio_run_button_label.py`:

```python
"""Pure-Python mirror of `runButtonLabel()` from studio.js — the
authoritative source for what the Run button says in each state.

The JS implementation in studio.js MUST produce the same string for
the same inputs. Both implementations are short by design.
"""

import pytest

from tests._helpers.studio_state import run_button_label


def test_idle_with_version():
    assert run_button_label(
        running=False, cancelling=False,
        done_flash_until_ms=0, now_ms=1000.0,
        active_version_num=3, elapsed_label="0:00",
    ) == "▶ Run on this clip · v3"


def test_idle_with_no_version_uses_question_mark():
    assert run_button_label(
        running=False, cancelling=False,
        done_flash_until_ms=0, now_ms=1000.0,
        active_version_num=None, elapsed_label="0:00",
    ) == "▶ Run on this clip · v?"


def test_running_renders_elapsed():
    assert run_button_label(
        running=True, cancelling=False,
        done_flash_until_ms=0, now_ms=5000.0,
        active_version_num=3, elapsed_label="0:05",
    ) == "⟳ Running… 0:05"


def test_running_renders_with_minute_elapsed():
    assert run_button_label(
        running=True, cancelling=False,
        done_flash_until_ms=0, now_ms=90000.0,
        active_version_num=3, elapsed_label="1:30",
    ) == "⟳ Running… 1:30"


def test_cancelling_overrides_running():
    assert run_button_label(
        running=True, cancelling=True,
        done_flash_until_ms=0, now_ms=5000.0,
        active_version_num=3, elapsed_label="0:05",
    ) == "⟳ Cancelling…"


def test_done_flash_when_active_overrides_everything():
    # done_flash_until_ms is in the future → flash is active.
    assert run_button_label(
        running=False, cancelling=False,
        done_flash_until_ms=12000.0, now_ms=11500.0,
        active_version_num=3, elapsed_label="0:00",
    ) == "✓ Done"


def test_done_flash_expired_returns_to_idle():
    # now > done_flash_until_ms → flash is expired; label is idle.
    assert run_button_label(
        running=False, cancelling=False,
        done_flash_until_ms=10000.0, now_ms=11500.0,
        active_version_num=3, elapsed_label="0:00",
    ) == "▶ Run on this clip · v3"


def test_done_flash_takes_precedence_over_running_mid_transition():
    """Brief moment where running has flipped false and the flash is
    set — label should already read Done."""
    assert run_button_label(
        running=False, cancelling=False,
        done_flash_until_ms=12000.0, now_ms=11500.0,
        active_version_num=3, elapsed_label="0:42",
    ) == "✓ Done"


@pytest.mark.parametrize("v,expected", [
    (1, "▶ Run on this clip · v1"),
    (10, "▶ Run on this clip · v10"),
    (99, "▶ Run on this clip · v99"),
])
def test_version_number_renders_verbatim(v, expected):
    assert run_button_label(
        running=False, cancelling=False,
        done_flash_until_ms=0, now_ms=0,
        active_version_num=v, elapsed_label="0:00",
    ) == expected
```

- [ ] **Step 3: Run tests to verify they fail (module doesn't exist)**

```bash
.venv/bin/pytest -q tests/unit/test_studio_run_button_label.py
```

Expected: **FAIL** — `ModuleNotFoundError: tests._helpers.studio_state`.

- [ ] **Step 4: Implement the mirror**

`tests/_helpers/studio_state.py`:

```python
"""Pure-Python mirror of studio.js's runButtonLabel().

Keep this function ≤ 10 lines and verbatim-equivalent to the JS in
backend/app/static/studio.js. When the JS changes, this file changes
in the same commit; both implementations are reviewed together.
"""

from __future__ import annotations


def run_button_label(
    *,
    running: bool,
    cancelling: bool,
    done_flash_until_ms: float,
    now_ms: float,
    active_version_num: int | None,
    elapsed_label: str,
) -> str:
    if done_flash_until_ms and now_ms < done_flash_until_ms:
        return "✓ Done"
    if cancelling:
        return "⟳ Cancelling…"
    if running:
        return f"⟳ Running… {elapsed_label}"
    v = active_version_num if active_version_num is not None else "?"
    return f"▶ Run on this clip · v{v}"
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
.venv/bin/pytest -q tests/unit/test_studio_run_button_label.py
```

Expected: **PASS** (11 tests).

- [ ] **Step 6: Commit**

```bash
git add tests/_helpers/__init__.py \
        tests/_helpers/studio_state.py \
        tests/unit/test_studio_run_button_label.py
git commit -m "test(studio): pure-Python mirror of runButtonLabel state machine"
```

---

## Task 4: Run-button state machine — JS impl + template bindings

Mirror the Python state machine in `studio.js`, add `runJobId` / `cancelling` / `doneFlashUntilMs`, add `runOrCancel()` + `cancel()`, capture `job_id` from the run POST, switch the ticker to 1Hz, and replace the header partial's dual `<template x-if>` with a single `<span x-text="runButtonLabel()">`.

**Files:**
- Modify: `backend/app/static/studio.js`
- Modify: `backend/app/templates/pages/_studio_header.html`
- Create: `tests/integration/test_studio_run_button_state.py`

- [ ] **Step 1: Write the failing integration test**

`tests/integration/test_studio_run_button_state.py`:

```python
"""The studio header binds the Run button to runOrCancel() and to the
new computed label. Static-only test (asserts the rendered template)
— behavioral verification happens via the JS mirror test."""

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


def _make_prompt(client):
    r = client.post("/api/prompts", json={
        "name": "rb", "media_kind": "any", "model": "gemini-2.5-pro",
        "target_map": {}, "output_schema": {}, "body": "x",
    })
    return r.json()["prompt_id"], r.json()["version_id"]


def test_run_button_uses_runOrCancel_and_label_getter(client):
    pid, vid = _make_prompt(client)
    r = client.get(f"/studio?prompt_id={pid}")
    assert r.status_code == 200
    html = r.text
    # Single computed label, not dual <template x-if> blocks.
    assert "runButtonLabel()" in html
    # Click target is the dispatcher, not the bare run method.
    assert "runOrCancel()" in html
    # Old dual-template shape is gone.
    assert "▶ Run on this clip · v" not in html or "x-text=" in html
    # Disabled binding picks up cancelling + doneFlashUntilMs.
    assert "cancelling" in html
    assert "doneFlashUntilMs" in html


def test_run_button_disabled_when_no_focused_clip(client):
    pid, _ = _make_prompt(client)
    r = client.get(f"/studio?prompt_id={pid}")
    assert r.status_code == 200
    # The :disabled binding includes !focusedClipId.
    assert ":disabled=" in r.text
    assert "focusedClipId" in r.text
```

- [ ] **Step 2: Run test to verify it fails**

```bash
.venv/bin/pytest -q tests/integration/test_studio_run_button_state.py
```

Expected: **FAIL** — `runButtonLabel()`, `runOrCancel()`, `cancelling`, `doneFlashUntilMs` are not yet in the rendered page.

- [ ] **Step 3: Update `studio.js`**

In `backend/app/static/studio.js`, locate the `studioPage` Alpine.data block (around line 116). Update the state and methods:

```js
Alpine.data('studioPage', (initial) => ({
  promptId: initial.promptId,
  activeVersionId: initial.activeVersionId,
  activeVersionNum: initial.activeVersionNum,
  activeModel: initial.activeModel,
  compareVersionId: initial.compareVersionId,
  compareVersionNum: initial.compareVersionNum,
  mode: 'prompt',
  focusedClipId: initial.focusedClipId ?? null,
  playerMinimized: false,

  // ── Run-button state machine ────────────────────────────────────────
  running: false,
  cancelling: false,
  runId: null,
  runJobId: null,
  runStartMs: 0,
  runningElapsedLabel: '0:00',
  doneFlashUntilMs: 0,
  _nowMs: 0,           // bumped each ticker firing so x-text re-evaluates

  pendingRunSwap: 0,

  init() {
    if (this.focusedClipId) this.refreshPlayer();
    // 1Hz ticker drives both the elapsed label and the done-flash expiry.
    setInterval(() => {
      const now = performance.now();
      this._nowMs = now;  // touch reactive state so the getter re-runs
      if (this.running) {
        const s = Math.floor((now - this.runStartMs) / 1000);
        this.runningElapsedLabel = window.fmtTimecode(s);
      }
      if (this.doneFlashUntilMs && now >= this.doneFlashUntilMs) {
        this.doneFlashUntilMs = 0;
      }
    }, 1000);
  },

  runButtonLabel() {
    // Mirror of tests/_helpers/studio_state.py::run_button_label
    // Touch _nowMs so Alpine re-evaluates this getter on each tick.
    const now = this._nowMs || performance.now();
    if (this.doneFlashUntilMs && now < this.doneFlashUntilMs) return '✓ Done';
    if (this.cancelling) return '⟳ Cancelling…';
    if (this.running) return `⟳ Running… ${this.runningElapsedLabel}`;
    const v = (this.activeVersionNum !== null && this.activeVersionNum !== undefined)
      ? this.activeVersionNum : '?';
    return `▶ Run on this clip · v${v}`;
  },

  async runOrCancel() {
    if (this.cancelling || this.doneFlashUntilMs) return;
    if (this.running) return this.cancel();
    return this.runOnFocusedClip();
  },

  async cancel() {
    if (!this.runJobId || this.cancelling) return;
    this.cancelling = true;
    try {
      await fetch(`/api/jobs/${this.runJobId}/cancel`, { method: 'POST' });
    } catch (err) {
      console.error('cancel failed', err);
    } finally {
      this.running = false;
      this.cancelling = false;
      this.pendingRunSwap++;
    }
  },

  focusClip(clipId) {
    this.focusedClipId = clipId;
    this.pendingRunSwap++;
    this._writeUrl();
    this.refreshPlayer();
  },

  minimizePlayer() { this.playerMinimized = true; },
  restorePlayer()  { this.playerMinimized = false; },

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

  seekFocusedClip(secs) {
    const playerEl = document.querySelector('.studio-player');
    if (!playerEl || !playerEl._x_dataStack) return;
    const player = playerEl._x_dataStack[0];
    if (typeof player.seek === 'function') player.seek(secs);
  },

  async runOnFocusedClip() {
    if (!this.activeVersionId || !this.focusedClipId || this.running) return;
    this.running = true;
    this.runStartMs = performance.now();
    this.runningElapsedLabel = '0:00';
    let finalStatus = null;
    try {
      const res = await fetch('/api/studio/runs', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          prompt_version_id: this.activeVersionId,
          clip_id: this.focusedClipId,
          model: this.activeModel || null,
        }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const {run_id, job_id} = await res.json();
      this.runId = run_id;
      this.runJobId = job_id ?? null;
      finalStatus = await this._poll(run_id);
    } catch (err) {
      console.error('studio run failed', err);
    } finally {
      this.running = false;
      this.runJobId = null;
      this.pendingRunSwap++;
      // ✓ Done flash on success only — no flash for error / cancelled.
      if (finalStatus === 'ok') {
        this.doneFlashUntilMs = performance.now() + 1200;
      }
    }
  },

  async _poll(runId) {
    while (this.running) {
      await new Promise(r => setTimeout(r, 1000));
      if (!this.running) return null;  // cancel() flipped it
      const res = await fetch(`/api/studio/runs/${runId}`);
      if (!res.ok) return null;
      const run = await res.json();
      if (run.status === 'ok' || run.status === 'error' || run.status === 'cancelled') {
        return run.status;
      }
    }
    return null;
  },

  async openCompare() {
    // unchanged from before — leave as-is
    // (keep whatever existed at this position; the surrounding state block
    // changes above don't touch openCompare's body)
  },
  // ... rest of studioPage unchanged
}));
```

NOTE: When applying this edit, preserve the existing `openCompare`,
`closeCompare`, `_writeUrl`, etc. methods that follow in the file —
only the run-related state + methods are rewritten.

- [ ] **Step 4: Update the header partial**

In `backend/app/templates/pages/_studio_header.html`, replace lines
71–81 (the entire `<button class="btn primary studio-run-btn">` block):

```jinja
    <button class="btn primary studio-run-btn"
            :disabled="!focusedClipId || cancelling || doneFlashUntilMs > 0"
            @click="runOrCancel()"
            :title="focusedClipId ? '' : 'Click a clip in a folder to focus it'">
      <span x-text="runButtonLabel()">▶ Run on this clip · v{{ active_version.version_num }}</span>
    </button>
```

(The inner `<span>` keeps the initial server-rendered label so the
button doesn't briefly read empty before Alpine hydrates.)

- [ ] **Step 5: Run the integration test to verify it passes**

```bash
.venv/bin/pytest -q tests/integration/test_studio_run_button_state.py
```

Expected: **PASS** (2 tests).

- [ ] **Step 6: Run the full studio integration suite to check for regressions**

```bash
.venv/bin/pytest -q tests/integration/test_studio_page.py \
                    tests/integration/test_studio_api.py
```

Expected: **PASS**.

- [ ] **Step 7: Manual smoke**

Start the dev server (`server-start` skill). Walk through manual
acceptance flows 1–5 from the spec. Specifically:

- Idle label matches the active version.
- Click Run → label becomes `⟳ Running… 0:00`, increments at 1Hz.
- Wait for completion → label flashes `✓ Done` for ~1.2s, then
  returns to idle.
- Click Run, click again mid-run → label briefly shows
  `⟳ Cancelling…`, then returns to idle. The associated job is
  cancelled (verify via `curl http://localhost:8765/api/jobs?…`
  or by reloading the studio and checking the Output tab state).

If anything looks wrong, `server-stop` cleanly (SIGTERM only,
**never -9** — see CLAUDE.md), fix, and re-run.

- [ ] **Step 8: Commit**

```bash
git add backend/app/static/studio.js \
        backend/app/templates/pages/_studio_header.html \
        tests/integration/test_studio_run_button_state.py
git commit -m "feat(studio): run-button state machine — cancel, done flash, 1Hz ticker"
```

---

## Task 5: Player-slot persistence guard

A small guard test that the studio player slot is in the static page
HTML and survives Output-tab re-renders. The slot lives in
`studio.html`, not in `_studio_run_output.html`, so this is structural
not behavioral — but the spec calls for it explicitly so it deserves
a named test.

**Files:**
- Create: `tests/integration/test_studio_player_persists_during_run.py`

- [ ] **Step 1: Write the test**

`tests/integration/test_studio_player_persists_during_run.py`:

```python
"""The studio player slot lives in studio.html, NOT in
_studio_run_output.html — so re-fetching the run partial while a run
is in progress does not collapse the player. This test guards the
structural separation: the player slot's DOM marker must appear in
the studio.html render but NOT in the run partial.
"""

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


def _make_prompt(client):
    r = client.post("/api/prompts", json={
        "name": "ps", "media_kind": "any", "model": "gemini-2.5-pro",
        "target_map": {}, "output_schema": {}, "body": "x",
    })
    return r.json()["prompt_id"], r.json()["version_id"]


def _seed_run(app, *, version_id, clip_id, status):
    async def _go():
        async with aiosqlite.connect(app.state.ctx.db_path) as db:
            await db.execute(
                "INSERT INTO studio_run(prompt_version_id, clip_id, status, "
                "output_json, model, finished_at) "
                "VALUES (?, ?, ?, ?, 'gemini-2.5-pro', '2026-05-28T00:00:00Z')",
                (version_id, clip_id, status,
                 json.dumps({"scenes": []}) if status == "ok" else None),
            )
            await db.commit()
    asyncio.get_event_loop().run_until_complete(_go())


def test_player_slot_in_page_not_in_run_partial(client):
    pid, vid = _make_prompt(client)
    page = client.get(f"/studio?prompt_id={pid}")
    assert page.status_code == 200
    # The page has the player slot marker.
    assert "data-studio-player-slot" in page.text

    # Pending run — the partial renders the "Running" empty state but
    # does NOT include the player slot.
    from backend.app import main as main_mod
    _seed_run(main_mod.app, version_id=vid, clip_id=12041, status="pending")
    partial = client.get(f"/studio/_run?prompt_version_id={vid}&clip_id=12041")
    assert partial.status_code == 200
    assert "data-studio-player-slot" not in partial.text, (
        "Run partial must not redefine the player slot — that would "
        "remount the player on every Output tab refresh."
    )
```

- [ ] **Step 2: Run test**

```bash
.venv/bin/pytest -q tests/integration/test_studio_player_persists_during_run.py
```

Expected: **PASS** on current code (this is a structural guard, not a
behavior change). If it fails, the run partial is rendering the slot
wrapper — fix that in `_studio_run_output.html` before continuing.

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_studio_player_persists_during_run.py
git commit -m "test(studio): guard that run partial doesn't redefine player slot"
```

---

## Task 6: Folder list — design-language compliance

Replace the new-folder input/buttons inline `style=` attributes and
`.mini` modifier with the canonical `.txt sm` / `.btn sm` primitives.

**Files:**
- Modify: `backend/app/templates/pages/_studio_folder_list.html`
- Modify: `backend/app/static/app.css`
- Create: `tests/integration/test_studio_folder_list_polish.py`

- [ ] **Step 1: Write the failing test**

`tests/integration/test_studio_folder_list_polish.py`:

```python
"""Folder list new-folder input + buttons use canonical primitives.
PR3 visual audit removes the inline style= and the undefined .mini
button modifier."""

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


def test_folder_list_uses_canonical_primitives(client):
    r = client.get("/studio")
    assert r.status_code == 200
    # New-folder wrapper uses a class, not inline style.
    assert "studio-folder-new" in r.text
    # The inline style="display:flex;..." string is gone.
    assert 'style="display:flex;gap:6px;padding:8px 12px' not in r.text
    # The .mini button modifier is gone (use .sm).
    assert "btn ghost mini" not in r.text
    assert "btn primary mini" not in r.text
    # The bare-input inline font-size override is gone.
    assert 'style="flex:1;font-size:12px' not in r.text
    # The empty-state inline padding is gone — class-driven.
    assert 'style="padding:12px"' not in r.text


def test_folder_list_input_uses_txt_class(client):
    r = client.get("/studio")
    assert r.status_code == 200
    # The new-folder input is .txt sm (canonical input class).
    assert 'class="txt sm"' in r.text
```

- [ ] **Step 2: Run test to verify it fails**

```bash
.venv/bin/pytest -q tests/integration/test_studio_folder_list_polish.py
```

Expected: **FAIL** — current template uses `.mini` and inline styles.

- [ ] **Step 3: Rewrite `_studio_folder_list.html`**

Replace the entire file `backend/app/templates/pages/_studio_folder_list.html` with:

```jinja
{# Folder tree with single-expand-at-a-time behavior.

   Each folder header toggles an inline panel that HTMX-loads its clip cards
   from /studio/_folder?folder_id=X. "+ Add from archive" inside the
   expanded panel opens the archive picker modal.
#}
<div class="studio-folders" x-data="studioFolders({{ focused_folder_id or 'null' }})">
  <div class="studio-folders-hdr">
    <span>Folders</span>
    <span class="grow"></span>
    <button class="btn ghost sm" @click="newFolderOpen = !newFolderOpen">+ New folder</button>
  </div>

  <div x-show="newFolderOpen" x-cloak class="studio-folder-new">
    <input type="text" class="txt sm" placeholder="folder name…"
           x-model="newFolderName" @keyup.enter="createFolder()" />
    <button class="btn primary sm" @click="createFolder()">Create</button>
  </div>

  <div class="studio-folders-list">
    {% for f in folders %}
      <div class="studio-folder" :class="expandedId === {{ f.id }} && 'open'">
        <div class="studio-folder-row" @click="toggle({{ f.id }})">
          <span class="twist" x-text="expandedId === {{ f.id }} ? '▾' : '▸'"></span>
          <span class="name">{{ f.name }}</span>
          <span class="count">{{ f.clip_count }}</span>
        </div>
        <div class="studio-folder-kids" x-show="expandedId === {{ f.id }}" x-cloak
             hx-get="/studio/_folder?folder_id={{ f.id }}{% if active_version %}&active_version_id={{ active_version.id }}{% endif %}{% if focused_clip_id %}&clip_id={{ focused_clip_id }}{% endif %}"
             hx-trigger="intersect once"
             hx-swap="innerHTML">
          <div class="muted">loading…</div>
        </div>
      </div>
    {% endfor %}
    {% if not folders %}
      <div class="studio-folders-empty muted">No folders yet. Create one above.</div>
    {% endif %}
  </div>
</div>
```

- [ ] **Step 4: Add the supporting CSS rules**

In `backend/app/static/app.css`, find the `.studio-folders` block (search for that selector). Just below it (or near other studio-folder rules), add:

```css
.studio-folder-new {
  display: flex;
  gap: 6px;
  padding: 8px 12px;
  border-bottom: 1px solid var(--line);
}
.studio-folder-new .txt { flex: 1; }
.studio-folders-empty { padding: 12px; }
```

- [ ] **Step 5: Run test to verify it passes**

```bash
.venv/bin/pytest -q tests/integration/test_studio_folder_list_polish.py
```

Expected: **PASS** (2 tests).

- [ ] **Step 6: Manual smoke**

Reload `/studio` in the browser. The "+ New folder" button looks
identical in size/weight to other `.btn ghost sm` buttons on the
page. Toggle the new-folder panel — the input is the same height
as other inputs site-wide; the layout is flush with the folder
list. Type a name and hit Enter — folder creates and the panel
closes.

- [ ] **Step 7: Commit**

```bash
git add backend/app/templates/pages/_studio_folder_list.html \
        backend/app/static/app.css \
        tests/integration/test_studio_folder_list_polish.py
git commit -m "refactor(studio,ui): folder list — canonical .txt/.btn primitives"
```

---

## Task 7: `_studio_compare.html` — drop inline display:none

Replace `style="display:none"` on the cmp-slot with `x-cloak` + an
empty-by-default container. The SSR `{% if compare_version %}`
guard already handles the "is there a cmp card?" question; the
inline style is purely defensive against an Alpine-not-yet-loaded
flash, which `x-cloak` already covers globally.

**Files:**
- Modify: `backend/app/templates/pages/_studio_compare.html`

- [ ] **Step 1: Visual diff before**

```bash
grep -n "display:none" backend/app/templates/pages/_studio_compare.html
```

Expected output: one line, around line 20.

- [ ] **Step 2: Rewrite the cmp-slot wrapper**

In `backend/app/templates/pages/_studio_compare.html`, replace lines
19–27:

```jinja
  <div class="cmp-slot" data-cmp-slot>
    {% if compare_version %}
      {% with side='cmp', active_version=compare_version, version=compare_version,
             versions=versions, clip_id=None, run=None, panels=None, clip={'fps': 25.0} %}
        {% include "pages/_studio_prompt_card.html" %}
      {% endwith %}
    {% endif %}
  </div>
```

(The `style="display:none"` is gone. When there's no `compare_version`,
the div renders empty, which is invisible on the layout — same effect,
no inline style.)

- [ ] **Step 3: Run the full studio integration suite to confirm no regression**

```bash
.venv/bin/pytest -q tests/integration/test_studio_page.py \
                    tests/integration/test_studio_compare.py 2>/dev/null \
                    || .venv/bin/pytest -q tests/integration/test_studio_page.py
```

Expected: **PASS**.

- [ ] **Step 4: Manual smoke**

Reload `/studio?prompt_id=N` (no compare). The compare row shows only
the cur card. No visible empty space where the cmp slot would be.
Click `+ Compare` — the cmp card appears. Click `×` on the cmp card —
the cmp slot becomes empty again, no inline-style flash.

- [ ] **Step 5: Commit**

```bash
git add backend/app/templates/pages/_studio_compare.html
git commit -m "refactor(studio): drop inline display:none on .cmp-slot"
```

---

## Task 8: Focus-visible outlines

Add focus-ring rules for the PR2 surfaces that didn't have them.

**Files:**
- Modify: `backend/app/static/app.css`

- [ ] **Step 1: Locate the studio CSS block**

```bash
grep -n "/* === Studio:" backend/app/static/app.css
```

Note the line of the final studio block (the legend rules, around
line 2019).

- [ ] **Step 2: Append the focus-visible rules**

In `backend/app/static/app.css`, after the last studio block (right
before the `/* === Review mode` block, ~line 2021), add:

```css
/* === Studio: focus-visible outlines ================================= */
.pc-vchip .btn:focus-visible,
.pc-vmenu-item:focus-visible,
.btn-diff-toggle:focus-visible,
.studio-clip-card:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 1px;
}
```

- [ ] **Step 3: Manual smoke**

Reload `/studio` and tab through the page from the URL bar. Each of:
- version chip on either card
- a hovered/focused version-picker dropdown item
- the `Diff vs v{cur}` button
- any clip card

should draw a 2px orange (`--accent`) outline when keyboard-focused.

Note: this is visual-only — no failing automated test exists here.
The CSS audit gate from Task 1 still protects the studio block from
phantom-token regressions.

- [ ] **Step 4: Commit**

```bash
git add backend/app/static/app.css
git commit -m "feat(studio,css): focus-visible outlines on PR2 surfaces"
```

---

## Task 9: ADR + docs/decisions.md + design-language.md mention

Write the PR3 ADR (MADR-lite, mirror the shape of ADRs 0033/0034/0037/0038), append the row to `docs/decisions.md`, and add a one-paragraph mention of the two new tokens to `docs/design-language.md`.

**Files:**
- Create: `docs/adr/0039-prompt-studio-pr3-polish.md`
- Modify: `docs/decisions.md`
- Modify: `docs/design-language.md`

- [ ] **Step 1: Read an existing ADR for shape reference**

```bash
sed -n '1,40p' docs/adr/0038-studio-output-via-review-items.md
```

This confirms the heading + metadata pattern.

- [ ] **Step 2: Write the ADR**

`docs/adr/0039-prompt-studio-pr3-polish.md`:

```markdown
# 0039. Prompt Studio PR3 — Polish

**Date:** 2026-05-28
**Status:** Accepted

## Context

PR1 (shell + single-clip run loop), PR2 (version-compare prompt-card +
line diffs + range overlay), and the post-PR2 refactor (shared player
chrome, output via review_items, focused-clip-in-URL) are merged.
The umbrella spec's third bucket — polish — was left to PR3.

Three concrete polish needs:

1. The Run button has no cancel affordance even though the underlying
   jobs pipeline already supports cancellation.
2. Empty/error states across the right pane render as plain `.muted`
   text with no shared shape, and error messages don't wrap or
   user-select cleanly.
3. The PR2-introduced CSS (`.pc-vchip`, `.pc-vmenu`, `.pc-diff`,
   `.cmp-card`, range overlays) predates the consolidation of
   `docs/design-language.md` as the source of truth and references
   tokens that don't exist (`--bg-3`, `--accent-fade`, `--border`,
   `--fg-muted`) with raw hex fallbacks that silently override.

## Alternatives

**A. Add a separate Cancel button next to Run.** Rejected. Two
buttons for one operation widen the surface; the umbrella spec
positions the Run button as "the single live control" for the
focused clip. The annotator already supports cooperative cancel
between job items, so reusing the same click target — dispatching
on the `running` flag — is a one-method addition.

**B. Define the cur/cmp range colors as new semantic tokens
(`--info-strong`, `--accent-strong`).** Rejected. The two
colors are studio-specific affordances — they live above the
timeline, not in semantic UI roles. Naming them
`--range-cur` / `--range-cmp` documents their purpose; using
`color-mix(in oklab, var(--info) 45%, transparent)` keeps them
tracked to the palette without inventing a parallel naming scheme.

**C. Port the React prototype's `styles.css` verbatim.** Rejected
per CLAUDE.md "Frontend: explore before implementing" — the design
language has diverged on purpose since PR1. The prototype is a
reference of intent; the in-codebase tokens are the source of
truth.

**D. Add a server-side `is_cancellable` boolean to the studio run
response so the frontend can decide.** Rejected. The frontend
already knows the state (`running === true` ⇒ cancellable). Adding
a server hint is wire-format churn for no information gain.

## Decision

1. **Cancel via existing jobs endpoint.** `POST /api/studio/runs`
   already returns `job_id`; the frontend captures it, and clicking
   the Run button while `running === true` issues
   `POST /api/jobs/{job_id}/cancel`. No new server route.

2. **`✓ Done` flash on success only.** A `doneFlashUntilMs`
   timestamp on `studioPage()`, cleared by the 1Hz ticker. No flash
   on error or cancelled status.

3. **Pure-Python mirror of `runButtonLabel()`** at
   `tests/_helpers/studio_state.py`, kept short enough to be
   verbatim-equivalent to the JS in `studio.js`. Same pattern PR2
   used for `lineDiff()`.

4. **`.run-empty` / `.run-error` gain dedicated CSS rules.** Error
   messages are mono, pre-wrap, word-break, and user-selectable.

5. **Two new `:root` tokens — `--range-cur`, `--range-cmp` —
   defined via `color-mix(in oklab, var(--info|--accent) 45%,
   transparent)`.** Same idiom as `--accent-2`.

6. **Phantom-token fallbacks deleted.** Each replaced with the
   closest existing token: `--bg-3 → --hover`,
   `--accent-fade → --accent-2`, `--border → --line`,
   `--fg-muted → --text-3`. Audit gate in
   `tests/unit/test_studio_css_no_phantom_tokens.py` prevents
   regression.

7. **`.btn-close-cmp` simplified to `.btn sm icon` (no custom
   class).** Aligns with design-language: modifiers on `.btn`,
   not parallel button classes.

8. **`.studio-clip-card .remove-x` retained as-is.** It's a
   positioned-absolute corner X with no shared behavior with
   `.btn`; the custom class is documented as an exception.

## Consequences

- The Run button is now stateful (idle / running / cancelling /
  done). The state machine has eight cases; the unit-test mirror
  covers all of them.
- The cancel flow is cooperative: clicking cancel while
  Gemini is mid-call leaves the studio_run in whatever terminal
  state it eventually reaches. Documented in the spec (flow 4)
  and in the spec's Risks section.
- Visual changes are subtle. Phantom-token swaps shift several
  surface colors by a small amount (e.g. `--bg-3` fallback
  `#1f1f1f` → `--hover` rgba(255,255,255,0.04) on dark `--panel`
  is darker than the hex). The manual acceptance flows verify
  every consumer visually.
- The audit gate (Task 1) is the durable contract: any future
  developer who tries to wire up `var(--border, …)` again fails
  CI. Same for the four banned raw rgba strings.
```

- [ ] **Step 3: Append to `docs/decisions.md`**

Find the most recent ADR row in `docs/decisions.md` (likely 0038) and append a new row immediately after:

```markdown
| [0039](adr/0039-prompt-studio-pr3-polish.md) | 2026-05-28 | Accepted | Prompt Studio PR3 polish — run-button cancel, ✓ Done flash, empty/error shells, visual audit to design-language tokens. |
```

(Match the exact column shape used in the existing table — read the file first to be sure.)

- [ ] **Step 4: Mention the new tokens in `docs/design-language.md`**

Find the section in `docs/design-language.md` that lists the `:root`
tokens (search for `--accent-2` or `--info`). Append one paragraph
or a couple of bullets:

```markdown
### Studio range overlay

Two tokens describe the cur / cmp range colors that overlay the
player timeline:

- `--range-cur` — `color-mix(in oklab, var(--info)   45%, transparent)`
- `--range-cmp` — `color-mix(in oklab, var(--accent) 45%, transparent)`

These are studio-specific affordances but they live in `:root`
alongside the palette so they track future palette shifts. The
legend dots reuse the source tokens (`--info` / `--accent`)
without the alpha mix.
```

- [ ] **Step 5: Commit**

```bash
git add docs/adr/0039-prompt-studio-pr3-polish.md \
        docs/decisions.md \
        docs/design-language.md
git commit -m "docs(studio): ADR 0039 + decisions index + design-language note"
```

---

## Task 10: Full-suite verification + manual acceptance flows

Run the entire test suite green, then walk every manual acceptance
flow on a running dev server with notes.

- [ ] **Step 1: Run the full test suite**

```bash
.venv/bin/python -m pytest -x -q
```

Expected: **PASS** (no failures).

If anything fails:
- Studio tests: diagnose and fix in a follow-up commit.
- Unrelated tests: read the failure carefully — PR3 should not
  touch anything outside `studio*` and `app.css`. If you broke
  something unexpected, revert + re-do that task.

- [ ] **Step 2: Start the dev server (graceful)**

Use the `server-start` skill. Confirm port 8765 isn't already in use
and that no other uvicorn is alive (CLAUDE.md "CatDV session discipline").

- [ ] **Step 3: Walk acceptance flow 1 — idle label tracks active version**

(See spec for the precise expected behavior.) Tick the spec's
checkbox in your head; if anything is wrong, fix and re-test.

- [ ] **Step 4: Walk acceptance flow 2 — running state with ticking elapsed**

- [ ] **Step 5: Walk acceptance flow 3 — ✓ Done flash on success**

- [ ] **Step 6: Walk acceptance flow 4 — cancel mid-run**

- [ ] **Step 7: Walk acceptance flow 5 — no ✓ Done on error**

(Easiest reproduction: temporarily set a bad `GEMINI_API_KEY` in
`.env`, restart, click Run. Don't forget to restore the key
afterwards.)

- [ ] **Step 8: Walk acceptance flow 6 — all four empty states**

- [ ] **Step 9: Walk acceptance flow 7 — range overlay colors via tokens**

Use dev tools "Computed" panel to verify the rule references
`var(--range-cur)` / `var(--range-cmp)`.

- [ ] **Step 10: Walk acceptance flow 8 — picker dropdown tokens**

- [ ] **Step 11: Walk acceptance flow 9 — diff highlights**

- [ ] **Step 12: Walk acceptance flow 10 — folder list polish**

- [ ] **Step 13: Walk acceptance flow 11 — focus rings**

- [ ] **Step 14: Walk acceptance flow 12 — clip detail regression**

Open `/clips/{any-id}`. Compare timeline / marker / playhead / anno
panels visually against a memory of pre-PR3 state. No diff expected.

- [ ] **Step 15: Walk acceptance flow 13 — review-mode pages regression**

- [ ] **Step 16: Shut down the dev server gracefully**

Use the `server-stop` skill. Confirm the log shows
`Application shutdown complete.` (CatDV seat released).
**Never `kill -9`.**

- [ ] **Step 17: Commit any acceptance-flow fixes (if any)**

```bash
git status
# If there are pending diffs from flow fixes:
git add <files>
git commit -m "fix(studio): <what the manual walk-through caught>"
```

If no fixes were needed, skip this step.

---

## Task 11: Finishing — push branch + PR

- [ ] **Step 1: Final sanity check**

```bash
git log --oneline origin/main..HEAD
git diff --stat origin/main..HEAD
```

Confirm the commits tell the PR3 story cleanly and the diff is
scoped to the spec.

- [ ] **Step 2: Push the branch**

```bash
git push -u origin polish/prompt-studio-pr3
```

(If push fails on a network error, retry up to 4 times with 2s/4s/8s/16s
exponential backoff per the session's git-push policy.)

- [ ] **Step 3: Open the PR**

Use the GitHub MCP `mcp__github__create_pull_request` tool with:
- title: `studio: PR3 polish — run-button cancel, empty/error shells, design-language audit`
- base: `main`
- head: `polish/prompt-studio-pr3`
- body: short summary linking to the spec / plan / ADR (see template
  below)

PR body template:

```markdown
## Summary

The umbrella PR3 polish slice for Prompt Studio:

- **Run button state machine:** click while running → cancels via the
  existing jobs endpoint. `✓ Done` flashes for 1.2s on success only
  (no flash on error / cancel). Elapsed-time ticker is 1Hz.
- **Empty / error shells:** `.run-empty` and `.run-error` get
  dedicated rules. Error messages mono, pre-wrap, word-break,
  user-selectable.
- **Visual audit:** phantom-token fallbacks (`--bg-3`, `--accent-fade`,
  `--border`, `--fg-muted`) replaced with the real tokens they
  silently shadowed. Raw rgba range/diff colors replaced with
  `color-mix(...)` from the palette. Two new tokens —
  `--range-cur` / `--range-cmp` — in `:root`. Folder-list input/buttons
  now use `.txt sm` / `.btn sm` primitives. Focus-visible outlines on
  PR2 surfaces.

No new endpoints, no schema, no JS files.

## Docs

- Spec: `docs/specs/2026-05-28-prompt-studio-pr3-design.md`
- Plan: `docs/plans/2026-05-28-prompt-studio-pr3.md`
- ADR: `docs/adr/0039-prompt-studio-pr3-polish.md`

## Test plan

- [x] `.venv/bin/python -m pytest -x -q` green
- [x] Manual acceptance flows 1–13 from the spec walked on a live
      dev server. Notes:
      - Flow 4 (cancel mid-run) behaves cooperatively — if the
        Gemini call has already returned by the time cancel is
        clicked, the studio_run reaches `ok` while the parent job
        is `cancelled`. Documented in spec.
      - Flow 7 (range tokens) verified via dev tools computed style.

## Closes

Closes the umbrella spec `docs/specs/2026-05-26-prompt-studio-design.md`
— PR3 is the final slice; there is no PR4.
```

- [ ] **Step 4: Report the PR URL to the user**

After the PR is created, paste the URL back in chat so the user can
see it.

---

## Self-review checklist (run after writing the plan)

Walk through the spec section by section; for each requirement, point
to a task that implements it:

| Spec section | Plan task(s) |
|---|---|
| Goal 1: Run-button state machine | Tasks 3, 4 |
| Goal 2: Empty / error polish | Tasks 2, 5 |
| Goal 3: Visual audit | Tasks 1, 6, 7, 8 |
| Spec: Cancel via existing endpoint | Task 4 |
| Spec: ✓ Done flash success-only | Tasks 3, 4 |
| Spec: 1Hz ticker | Task 4 |
| Spec: New `:root` tokens | Task 1 |
| Spec: Phantom-token swaps | Task 1 |
| Spec: Raw rgba → tokens | Task 1 |
| Spec: `.run-empty` / `.run-error` rules | Task 2 |
| Spec: Folder list refactor | Task 6 |
| Spec: `.cmp-slot` inline-style removal | Task 7 |
| Spec: Focus-visible rules | Task 8 |
| Spec: ADR + decisions.md + design-language.md | Task 9 |
| Spec: Manual acceptance flows walked | Task 10 |
| Definition of done: PR opened | Task 11 |

No spec requirements without a backing task; no placeholders; no
"TBD" or "TODO" entries. The implementation is ~9 small commits and
should land in one PR.
