# Studio Layout Toggles Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add three VS-Code-style header toggles (left of Run) that show/hide the video list, show/hide the player, and switch the prompt/output between under-player and right-of-player layouts, with localStorage persistence.

**Architecture:** Pure CSS-grid modifier classes on the existing `.studio-body` / `.studio-right` DOM — no nodes move, so Alpine/HTMX wiring is untouched. State lives in the `studioPage` Alpine component; prefs load from `window.__studioPrefs` (set by a pre-paint inline script) and save to `localStorage`.

**Tech Stack:** Jinja2 partials, Alpine.js v3, vanilla CSS (design tokens), pytest (template/CSS/integration guards). Python: `.venv/bin/python`.

**Spec:** `docs/specs/2026-05-28-studio-layout-toggles-design.md`

---

## File Structure

- **Create** `backend/app/templates/icons/_panel_left.svg` — list toggle glyph.
- **Create** `backend/app/templates/icons/_player_frame.svg` — player toggle glyph.
- **Create** `backend/app/templates/icons/_layout_under.svg` — layout glyph (under mode).
- **Create** `backend/app/templates/icons/_layout_right.svg` — layout glyph (right mode).
- **Modify** `backend/app/static/app.css` — `.no-list`, `.layout-right`, `.studio-layout-toggles`; remove dead `.studio-player-min` / `.studio-show-player` rules.
- **Modify** `backend/app/static/studio.js` — `studioPage` prefs state + toggles; remove `playerMinimized`/`minimizePlayer`/`restorePlayer`/shim; add `get layout()` to `studioPromptCard`.
- **Modify** `backend/app/templates/pages/_studio_header.html` — toggle group; remove `▭` restore button.
- **Modify** `backend/app/templates/pages/_studio_player.html` — remove `−` minimise button.
- **Modify** `backend/app/templates/pages/studio.html` — pre-paint script + `:class` bindings.
- **Modify** `backend/app/templates/pages/_studio_prompt_card.html` — gate `+ Compare` to `under` layout.
- **Create** `tests/unit/test_studio_layout_toggles_css.py`
- **Create** `tests/unit/test_studio_layout_toggles_markup.py`
- **Create** `tests/integration/test_studio_layout_toggles.py`
- **Create** `docs/adr/0040-studio-layout-toggles.md` + update `docs/decisions.md`.

---

## Task 1: Toggle icons

**Files:**
- Create: `backend/app/templates/icons/_panel_left.svg`
- Create: `backend/app/templates/icons/_player_frame.svg`
- Create: `backend/app/templates/icons/_layout_under.svg`
- Create: `backend/app/templates/icons/_layout_right.svg`

Static assets — no failing test (verified by the markup/integration tests in later tasks). Match the existing stroke style in `icons/_play.svg` (`viewBox="0 0 24 24"`, `width/height="18"`, `stroke-width="1.7"`, `currentColor`).

- [ ] **Step 1: Create `_panel_left.svg`** (rectangle with a left divider = sidebar)

```html
<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="5" width="18" height="14" rx="1.5"/><line x1="9" y1="5" x2="9" y2="19"/></svg>
```

- [ ] **Step 2: Create `_player_frame.svg`** (frame with a play triangle = video player)

```html
<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="5" width="18" height="14" rx="1.5"/><polygon points="10 9 15 12 10 15" fill="currentColor" stroke="none"/></svg>
```

- [ ] **Step 3: Create `_layout_under.svg`** (horizontal divider = prompt/output under player)

```html
<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="5" width="18" height="14" rx="1.5"/><line x1="3" y1="13" x2="21" y2="13"/></svg>
```

- [ ] **Step 4: Create `_layout_right.svg`** (vertical divider = prompt/output right of player)

```html
<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="5" width="18" height="14" rx="1.5"/><line x1="13" y1="5" x2="13" y2="19"/></svg>
```

- [ ] **Step 5: Commit**

```bash
git add backend/app/templates/icons/_panel_left.svg backend/app/templates/icons/_player_frame.svg backend/app/templates/icons/_layout_under.svg backend/app/templates/icons/_layout_right.svg
git commit -m "feat(studio,icons): layout-toggle glyphs (panel-left, player-frame, layout under/right)"
```

---

## Task 2: CSS — layout modifier classes + toggle group

**Files:**
- Test: `tests/unit/test_studio_layout_toggles_css.py`
- Modify: `backend/app/static/app.css`

- [ ] **Step 1: Write the failing CSS guard test**

Create `tests/unit/test_studio_layout_toggles_css.py`:

```python
"""Guards the CSS rules that drive the studio layout toggles. The
toggles are pure CSS modifier classes on .studio-body / .studio-right;
if these selectors go missing the toggles silently no-op."""

from pathlib import Path

CSS = Path("backend/app/static/app.css")


def test_no_list_rule_exists():
    css = CSS.read_text()
    assert ".studio-body.no-list" in css, "missing .no-list grid rule"


def test_layout_right_rule_exists():
    css = CSS.read_text()
    assert ".studio-right.layout-right" in css, "missing .layout-right grid rule"


def test_layout_toggles_group_styled():
    css = CSS.read_text()
    assert ".studio-layout-toggles" in css, "missing toggle-group rule"


def test_dead_player_minimise_css_removed():
    css = CSS.read_text()
    assert ".studio-player-min" not in css, (
        "the player minimise button is removed; its CSS should go too"
    )
    assert ".studio-show-player" not in css, (
        "the header restore button is removed; its CSS should go too"
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_studio_layout_toggles_css.py -q`
Expected: FAIL — `.studio-body.no-list` missing; `.studio-player-min` still present.

- [ ] **Step 3: Add the layout CSS**

In `backend/app/static/app.css`, find the existing block:

```css
.studio-body.no-player .studio-player-slot {
  display: none;
}
```

Immediately after it, add:

```css
.studio-body.no-list {
  grid-template-columns: 1fr;
}
.studio-body.no-list .studio-videos {
  display: none;
}

/* Prompt/output beside the player (vs stacked under it). The player
   slot and the compare block are siblings in .studio-right; flipping
   the grid from rows to columns places them side-by-side (player left,
   prompt/output right) without moving any DOM. */
.studio-right.layout-right {
  grid-template-columns: minmax(360px, 1fr) 1fr;
  grid-template-rows: 1fr;
}
.studio-right.layout-right .studio-player-slot {
  border-bottom: none;
  border-right: 1px solid var(--line);
}
/* With the player hidden in right mode, the prompt/output fills the row. */
.studio-body.no-player .studio-right.layout-right {
  grid-template-columns: 1fr;
}
```

- [ ] **Step 4: Add the toggle-group CSS**

Find `.studio-hdr .grow {` and after its closing `}` add:

```css
.studio-layout-toggles {
  display: flex;
  gap: 4px;
  margin-right: 4px;
}
.studio-layout-toggles .btn.active {
  background: var(--accent-2);
  color: var(--text);
}
```

- [ ] **Step 5: Remove the dead minimise/restore CSS**

Delete these two blocks entirely (the buttons they style are removed in Tasks 4):

```css
.studio-player-min {
  position: absolute;
  top: 6px;
  right: 6px;
  z-index: 2;
  width: 24px;
  height: 24px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: 1px solid var(--line);
  border-radius: 4px;
  background: rgba(20, 24, 29, 0.7);
  color: var(--text-2);
  cursor: pointer;
  font-size: 16px;
  line-height: 1;
  padding: 0;
}
.studio-player-min:hover {
  color: var(--text);
  background: var(--surface-2);
  border-color: var(--line-2);
}
```

```css
.studio-show-player {
  width: 28px;
  height: 28px;
  margin-right: 6px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: 1px solid var(--line);
  border-radius: 4px;
  background: transparent;
  color: var(--text-2);
  cursor: pointer;
  font-size: 14px;
  line-height: 1;
  padding: 0;
}
.studio-show-player:hover {
  color: var(--text);
  background: var(--surface-2);
  border-color: var(--line-2);
}
```

Keep the `.studio-player { position: relative; }` rule (harmless; the wrapper stays).

- [ ] **Step 6: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/test_studio_layout_toggles_css.py -q`
Expected: PASS (4 tests).

- [ ] **Step 7: Commit**

```bash
git add backend/app/static/app.css tests/unit/test_studio_layout_toggles_css.py
git commit -m "feat(studio,css): layout-toggle grid classes (.no-list, .layout-right) + toggle group; drop dead minimise CSS"
```

---

## Task 3: studio.js — prefs state + toggles

**Files:**
- Modify: `backend/app/static/studio.js`

No JS unit-test harness exists in this repo (pure JS logic is mirrored in Python only when non-trivial; this is three booleans, so YAGNI). Behaviour is covered by the integration render (Task 5) and manual flows. Make the edits precisely.

- [ ] **Step 1: Remove the `window.studio.minimizePlayer` shim**

In `studio.js`, delete this block (comment + method) from the `window.studio = { … }` object:

```js
  // The minimise button lives inside the player wrapper, whose own
  // x-data="player(...)" creates a nested scope that doesn't expose
  // studioPage methods directly. Route the click through this shim.
  minimizePlayer() {
    this._root()?.minimizePlayer();
  },
```

(Ensure the preceding `removeClip(...)` method keeps its trailing comma and the object still closes with `};`.)

- [ ] **Step 2: Convert the `studioPage` factory to read prefs**

Find:

```js
  Alpine.data('studioPage', (initial) => ({
    promptId: initial.promptId,
```

Replace the opening line with a body that reads `window.__studioPrefs`:

```js
  Alpine.data('studioPage', (initial) => {
    const prefs = window.__studioPrefs || { showList: true, showPlayer: true, layout: 'under' };
    return {
    promptId: initial.promptId,
```

Then find the matching close of the factory object. It currently ends:

```js
      window.history.replaceState({}, '', `${window.location.pathname}?${p.toString()}`);
    },
  }));
```

Replace `  }));` with `    },\n  };\n  });` so the returned object and the arrow function both close:

```js
      window.history.replaceState({}, '', `${window.location.pathname}?${p.toString()}`);
    },
  };
  });
```

> Note: only the FIRST `}));` (the one closing `studioPage`) changes. `modelPicker`, `archivePicker`, `studioFolders`, `studioPromptCard` factories below are untouched.

- [ ] **Step 3: Swap `playerMinimized` for the prefs fields**

Find:

```js
    focusedClipId: initial.focusedClipId ?? null,
    playerMinimized: false,
```

Replace with:

```js
    focusedClipId: initial.focusedClipId ?? null,
    showList: prefs.showList,
    showPlayer: prefs.showPlayer,
    layout: prefs.layout,            // 'under' | 'right'
```

- [ ] **Step 4: Replace `minimizePlayer`/`restorePlayer` with the toggles**

Find:

```js
    minimizePlayer() {
      this.playerMinimized = true;
    },

    restorePlayer() {
      this.playerMinimized = false;
    },
```

Replace with:

```js
    toggleList() {
      this.showList = !this.showList;
      this._saveLayoutPrefs();
    },

    togglePlayer() {
      this.showPlayer = !this.showPlayer;
      this._saveLayoutPrefs();
    },

    setLayout(v) {
      if (v !== 'under' && v !== 'right') return;
      this.layout = v;
      // Compare needs the wide stacked layout; close it when going right.
      if (v === 'right' && this.compareVersionId) this.closeCompare();
      this._saveLayoutPrefs();
    },

    _saveLayoutPrefs() {
      try {
        localStorage.setItem('studio.layoutPrefs', JSON.stringify({
          showList: this.showList,
          showPlayer: this.showPlayer,
          layout: this.layout,
        }));
      } catch (err) {
        console.error('studio layout prefs save failed', err);
      }
    },
```

- [ ] **Step 5: Add the `get layout()` proxy to `studioPromptCard`**

Find (in the `studioPromptCard` factory):

```js
    get compareVersionId() { return this._page()?.compareVersionId; },
```

Add immediately after:

```js
    get layout()           { return this._page()?.layout; },
```

- [ ] **Step 6: Verify no stale references remain**

Run: `grep -nE 'playerMinimized|minimizePlayer|restorePlayer' backend/app/static/studio.js`
Expected: no output.

Run: `node --check backend/app/static/studio.js` (source `~/.nvm/nvm.sh` first if `node` is not on PATH).
Expected: no syntax errors (exit 0, no output).

- [ ] **Step 7: Commit**

```bash
git add backend/app/static/studio.js
git commit -m "feat(studio): layout-prefs state (showList/showPlayer/layout) + localStorage; drop playerMinimized"
```

---

## Task 4: Templates — toggle group, button removals, layout bindings, compare gating

**Files:**
- Test: `tests/unit/test_studio_layout_toggles_markup.py`
- Modify: `backend/app/templates/pages/_studio_header.html`
- Modify: `backend/app/templates/pages/_studio_player.html`
- Modify: `backend/app/templates/pages/studio.html`
- Modify: `backend/app/templates/pages/_studio_prompt_card.html`

- [ ] **Step 1: Write the failing markup guard test**

Create `tests/unit/test_studio_layout_toggles_markup.py`:

```python
"""Static markup guards for the studio layout toggles. The minimise (−)
and restore (▭) single-purpose buttons are replaced by the three header
toggles; guard both the removals and the additions."""

from pathlib import Path

HDR = Path("backend/app/templates/pages/_studio_header.html")
SP = Path("backend/app/templates/pages/_studio_player.html")
CARD = Path("backend/app/templates/pages/_studio_prompt_card.html")


def test_minimise_button_removed_from_studio_player():
    sp = SP.read_text()
    assert "studio-player-min" not in sp
    assert "minimizePlayer" not in sp


def test_restore_button_removed_from_header():
    hdr = HDR.read_text()
    assert "studio-show-player" not in hdr
    assert "restorePlayer" not in hdr


def test_header_has_three_layout_toggles():
    hdr = HDR.read_text()
    assert 'class="studio-layout-toggles"' in hdr
    for which in ("list", "player", "layout"):
        assert f'data-studio-toggle="{which}"' in hdr, f"missing {which} toggle"


def test_compare_button_gated_to_under_layout():
    card = CARD.read_text()
    assert "layout === 'under'" in card, (
        "+ Compare must be gated to the under-player layout"
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_studio_layout_toggles_markup.py -q`
Expected: FAIL — toggles absent; minimise/restore buttons still present.

- [ ] **Step 3: Remove the minimise button from `_studio_player.html`**

Delete this block (comment + button):

```html
  {# Minimise button. Lives inside the player's nested x-data, so calling
     a studioPage method directly from Alpine would resolve against
     player()'s scope. Route through the window.studio shim (same pattern
     used by clip-card onclick handlers). #}
  <button type="button" class="studio-player-min" title="Hide player"
          onclick="window.studio.minimizePlayer()">−</button>
```

- [ ] **Step 4: Replace the restore button with the toggle group in `_studio_header.html`**

Delete this block:

```html
  {# Restore-player icon. Only meaningful when a clip is focused AND the
     user has minimised the player — otherwise the player slot's natural
     visibility is already correct. #}
  <button type="button" class="studio-show-player"
          x-show="playerMinimized && focusedClipId"
          x-cloak
          @click="restorePlayer()"
          title="Show player">▭</button>
```

In its place (still after `<span class="grow"></span>`, before the `{% if active_version %}` Run button), insert:

```html
  {# Layout toggles (left of Run). Pure view-state on studioPage; each
     toggles a CSS modifier class via the :class bindings in studio.html.
     data-studio-toggle markers are asserted by the markup guard test. #}
  <div class="studio-layout-toggles">
    <button type="button" class="btn icon sm" data-studio-toggle="list"
            :class="showList && 'active'"
            @click="toggleList()"
            :title="showList ? 'Hide video list' : 'Show video list'">
      {% include "icons/_panel_left.svg" %}
    </button>
    <button type="button" class="btn icon sm" data-studio-toggle="player"
            :class="showPlayer && 'active'"
            @click="togglePlayer()"
            :title="showPlayer ? 'Hide player' : 'Show player'">
      {% include "icons/_player_frame.svg" %}
    </button>
    <button type="button" class="btn icon sm" data-studio-toggle="layout"
            @click="setLayout(layout === 'under' ? 'right' : 'under')"
            :title="layout === 'under' ? 'Prompt/output: move right of player' : 'Prompt/output: move under player'">
      <span x-show="layout === 'under'">{% include "icons/_layout_under.svg" %}</span>
      <span x-show="layout === 'right'" x-cloak>{% include "icons/_layout_right.svg" %}</span>
    </button>
  </div>
```

- [ ] **Step 5: Update `studio.html` bindings + pre-paint script**

Find:

```html
  <div class="studio-body{% if not focused_clip_id %} no-player{% endif %}"
       :class="{ 'no-player': !focusedClipId || playerMinimized }">
    <aside class="studio-videos">
      {% include "pages/_studio_folder_list.html" %}
    </aside>
    <section class="studio-right">
      <div class="studio-player-slot" data-studio-player-slot></div>
      <div class="studio-compare">
        {% include "pages/_studio_compare.html" %}
      </div>
    </section>
  </div>
```

Replace with (updated `:class`, `layout-right` binding on `.studio-right`, and a pre-paint script right after the body div):

```html
  <div class="studio-body{% if not focused_clip_id %} no-player{% endif %}"
       :class="{ 'no-list': !showList, 'no-player': !showPlayer || !focusedClipId }">
    <aside class="studio-videos">
      {% include "pages/_studio_folder_list.html" %}
    </aside>
    <section class="studio-right" :class="{ 'layout-right': layout === 'right' }">
      <div class="studio-player-slot" data-studio-player-slot></div>
      <div class="studio-compare">
        {% include "pages/_studio_compare.html" %}
      </div>
    </section>
  </div>
  {# First-paint layout from saved prefs, before Alpine boots, to avoid a
     flash of the default layout. Sets window.__studioPrefs (read by the
     studioPage factory so its initial :class eval already matches) and
     stamps the modifier classes synchronously. #}
  <script>
    (function () {
      var prefs = { showList: true, showPlayer: true, layout: 'under' };
      try {
        var p = JSON.parse(localStorage.getItem('studio.layoutPrefs') || '{}');
        if (p.showList === false) prefs.showList = false;
        if (p.showPlayer === false) prefs.showPlayer = false;
        if (p.layout === 'right') prefs.layout = 'right';
      } catch (e) {}
      window.__studioPrefs = prefs;
      var body = document.querySelector('.studio-body');
      var right = document.querySelector('.studio-right');
      if (body && !prefs.showList) body.classList.add('no-list');
      if (body && !prefs.showPlayer) body.classList.add('no-player');
      if (right && prefs.layout === 'right') right.classList.add('layout-right');
    })();
  </script>
```

- [ ] **Step 6: Gate `+ Compare` to under-layout in `_studio_prompt_card.html`**

Find:

```html
      <button type="button" class="btn sm"
              x-show="compareVersionId === null"
              @click="openCompare()">+ Compare</button>
```

Replace the `x-show` line:

```html
      <button type="button" class="btn sm"
              x-show="compareVersionId === null && layout === 'under'"
              @click="openCompare()">+ Compare</button>
```

- [ ] **Step 7: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/test_studio_layout_toggles_markup.py -q`
Expected: PASS (4 tests).

- [ ] **Step 8: Commit**

```bash
git add backend/app/templates/pages/_studio_header.html backend/app/templates/pages/_studio_player.html backend/app/templates/pages/studio.html backend/app/templates/pages/_studio_prompt_card.html tests/unit/test_studio_layout_toggles_markup.py
git commit -m "feat(studio): header layout toggles + first-paint prefs; remove minimise/restore buttons; gate compare to under-layout"
```

---

## Task 5: Integration test — toggles render on the studio page

**Files:**
- Test: `tests/integration/test_studio_layout_toggles.py`

- [ ] **Step 1: Write the integration test**

Create `tests/integration/test_studio_layout_toggles.py` (mirrors the fixture/helper pattern in `tests/integration/test_studio_player_persists_during_run.py`):

```python
"""The studio page renders the three layout toggles in its header and
the layout :class bindings on the body/right panes."""

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
        "name": "ps", "media_kind": "any", "model": "gemini-2.5-pro",
        "target_map": {}, "output_schema": {}, "body": "x",
    })
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    return pid


def test_studio_page_renders_layout_toggles(client):
    pid = _make_prompt(client)
    page = client.get(f"/studio?prompt_id={pid}")
    assert page.status_code == 200
    assert 'class="studio-layout-toggles"' in page.text
    for which in ("list", "player", "layout"):
        assert f'data-studio-toggle="{which}"' in page.text
    # Layout bindings present on the panes.
    assert "'no-list': !showList" in page.text
    assert "'layout-right': layout === 'right'" in page.text
```

- [ ] **Step 2: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/integration/test_studio_layout_toggles.py -q`
Expected: PASS (1 test). (The templates from Task 4 are already in place, so this is green on first run — that is expected for an end-to-end render guard layered on already-tested partials.)

- [ ] **Step 3: Run the full studio/player/css suite for regressions**

Run: `.venv/bin/python -m pytest tests/unit tests/integration -q -k "player or studio or css or button" --ignore=tests/unit/test_thumbnail_service_image.py`
Expected: all PASS (the `test_thumbnail_service_image.py` PIL import error is pre-existing and unrelated — ignored).

- [ ] **Step 4: Commit**

```bash
git add tests/integration/test_studio_layout_toggles.py
git commit -m "test(studio): integration guard for layout-toggle rendering"
```

---

## Task 6: ADR + decisions index + manual verification

**Files:**
- Create: `docs/adr/0040-studio-layout-toggles.md`
- Modify: `docs/decisions.md`

- [ ] **Step 1: Confirm 0040 is the next ADR number**

Run: `ls docs/adr/ | sort | tail -3`
Expected: highest existing is `0039-…`. If not, use the next free number and adjust the filename/heading below.

- [ ] **Step 2: Write the ADR**

Create `docs/adr/0040-studio-layout-toggles.md`:

```markdown
# 0040. Studio layout toggles (list / player / prompt-output position)

**Date:** 2026-05-28
**Status:** Accepted

## Context

The studio packed four regions (video list, player, prompt/output card,
compare card) into one screen with no space management beyond an ad-hoc
player minimise (−) button and a header restore (▭) icon — two
single-purpose affordances for one region. We wanted VS-Code-style
layout toggles to show/hide the list and player and to switch the
prompt/output between under-player and right-of-player.

## Alternatives

- **JS relocating DOM nodes between containers per layout.** Rejected:
  the studio already fights Alpine `initTree` / HTMX re-scan timing
  (see `studio.js`); moving the player/card subtrees would re-trigger
  those hazards.
- **Per-layout templates.** Rejected: duplicates the player + card
  includes.
- **Server-side / per-prompt persistence.** Rejected as over-built;
  layout is a per-browser viewing preference.

## Decision

Pure CSS-grid modifier classes on the existing DOM. `.studio-right`
holds the player slot and the compare block as siblings; flipping it
from `grid-template-rows` to `grid-template-columns` (`.layout-right`)
puts them side-by-side with no DOM moves. `.no-list` / `.no-player`
collapse the respective regions. State lives in `studioPage`
(`showList`, `showPlayer`, `layout`), persisted to
`localStorage['studio.layoutPrefs']` and applied pre-paint via a small
inline script that also seeds `window.__studioPrefs` (read by the
Alpine factory so its first `:class` eval matches — no flash). The
minimise/restore buttons are removed in favour of the header player
toggle. `+ Compare` is gated to `under` layout; switching to `right`
auto-closes an open compare.

## Consequences

- One coherent toggle cluster replaces two single-purpose buttons.
- No DOM moves ⇒ Alpine/HTMX wiring on player/card/compare is untouched.
- Layout is per-browser, not shared via URL; opening the same studio
  URL elsewhere uses that browser's saved prefs (acceptable — layout is
  a viewing preference, not shareable state).
- Right-mode split is fixed (`minmax(360px,1fr) 1fr`); a draggable
  splitter is a future ask if it bites.
```

- [ ] **Step 3: Add the row to `docs/decisions.md`**

Open `docs/decisions.md`, find the index table, and append a row consistent with the existing format, e.g.:

```markdown
| 0040 | Studio layout toggles (list / player / prompt-output position) | 2026-05-28 | Accepted |
```

(Match the exact column layout already in the file — check the header row first.)

- [ ] **Step 4: Manual verification on the running server**

The dev server runs with `--reload`; template/JS/CSS edits are already live (each reload re-runs the ~18s CatDV login). Then walk the spec's **Manual acceptance flows** §1–6:
1. List toggle hides/shows the video-list column.
2. Player toggle hides/shows the player; confirm no `−` corner button and no `▭` header icon.
3. Layout toggle moves prompt/output under ⇄ right of the player.
4. `+ Compare` only in under-layout; switching to right auto-closes compare.
5. Reload persists the chosen layout (no flash).
6. No-clip state: toggles work without errors.

Record pass/fail per flow.

- [ ] **Step 5: Commit**

```bash
git add docs/adr/0040-studio-layout-toggles.md docs/decisions.md
git commit -m "docs(studio): ADR 0040 layout toggles + decisions index"
```

---

## Self-Review notes

- **Spec coverage:** list toggle (T2/T4), player toggle + button removals (T2/T3/T4), layout under/right (T2/T3/T4), compare gating + auto-close (T3/T4), persistence + first-paint (T3/T4), icons (T1), tests (T2/T4/T5), ADR (T6), manual flows (T6 §4). All spec sections mapped.
- **Type/name consistency:** `showList` / `showPlayer` / `layout` ('under'|'right'), `toggleList()` / `togglePlayer()` / `setLayout()` / `_saveLayoutPrefs()`, `window.__studioPrefs`, `data-studio-toggle="list|player|layout"`, classes `.no-list` / `.no-player` / `.layout-right`, localStorage key `studio.layoutPrefs` — used identically across all tasks.
- **No placeholders:** every code step shows full content.
```
