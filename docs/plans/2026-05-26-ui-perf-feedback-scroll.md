# UI Responsiveness: Feedback, Local Assets, Cache Scroll — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the UI feel responsive — serve htmx/Alpine/fonts locally to kill render-blocking CDN loads, show immediate click/action feedback via a top progress bar + pressed state, and fix the Cache page so it scrolls like every other page.

**Architecture:** Pure frontend/asset changes, no behavior or data-path changes. Vendor the two JS libraries and two variable fonts into `backend/app/static/vendor/` (served by the existing `/static` mount). Add one small dependency-free script (`nav-feedback.js`) that drives a fixed top progress bar from htmx lifecycle events and from same-page navigation clicks. Fix the CSS grid so the `main` track is viewport-bounded, letting the Cache page's own `overflow-y: auto` engage.

**Tech Stack:** FastAPI + Jinja2 server-rendered templates, htmx 1.9.10, Alpine 3.14.1, plain CSS (`app.css`), pytest. Lint/type gate: `ruff`, `basedpyright` (baseline), `import-linter`, `interrogate` (tests excluded).

**Spec:** `docs/specs/2026-05-26-ui-perf-feedback-scroll-design.md`

---

## File Structure

- `backend/app/static/vendor/htmx.min.js` — vendored htmx (new)
- `backend/app/static/vendor/alpine.min.js` — vendored Alpine (new)
- `backend/app/static/vendor/fonts/inter-latin-wght-normal.woff2` — vendored Inter variable font (new)
- `backend/app/static/vendor/fonts/jetbrains-mono-latin-wght-normal.woff2` — vendored JetBrains Mono variable font (new)
- `backend/app/static/nav-feedback.js` — progress bar + click/htmx feedback (new)
- `backend/app/static/app.css` — add `@font-face`, progress-bar + feedback styles, grid scroll fix (modify)
- `backend/app/templates/pages/layout.html` — swap CDN refs for local paths, add progress element + script (modify)
- `tests/unit/test_layout_assets.py` — regression guard for local assets / ordering (new)

Pinned versions match what `layout.html` references today: htmx `1.9.10`, Alpine `3.14.1`.

---

## Task 1: Vendor htmx + Alpine locally

**Files:**
- Create: `backend/app/static/vendor/htmx.min.js`
- Create: `backend/app/static/vendor/alpine.min.js`
- Create: `tests/unit/test_layout_assets.py`
- Modify: `backend/app/templates/pages/layout.html`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_layout_assets.py`:

```python
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_LAYOUT = _ROOT / "backend" / "app" / "templates" / "pages" / "layout.html"
_STATIC = _ROOT / "backend" / "app" / "static"


def test_layout_does_not_reference_unpkg():
    html = _LAYOUT.read_text(encoding="utf-8")
    assert "unpkg.com" not in html


def test_layout_references_local_js_vendor():
    html = _LAYOUT.read_text(encoding="utf-8")
    assert "/static/vendor/htmx.min.js" in html
    assert "/static/vendor/alpine.min.js" in html


def test_htmx_loads_before_alpine():
    html = _LAYOUT.read_text(encoding="utf-8")
    assert html.index("htmx.min.js") < html.index("alpine.min.js")


def test_vendored_js_exists_and_nonempty():
    for rel in ("vendor/htmx.min.js", "vendor/alpine.min.js"):
        p = _STATIC / rel
        assert p.exists(), f"missing vendored asset: {rel}"
        assert p.stat().st_size > 1024, f"vendored asset too small: {rel}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/unit/test_layout_assets.py -v`
Expected: FAIL — `test_layout_does_not_reference_unpkg` (unpkg still present), `test_layout_references_local_js_vendor` (paths absent), `test_vendored_js_exists_and_nonempty` (files missing).

- [ ] **Step 3: Download the vendored JS (pinned)**

```bash
mkdir -p backend/app/static/vendor
curl -fsSL https://unpkg.com/htmx.org@1.9.10/dist/htmx.min.js \
  -o backend/app/static/vendor/htmx.min.js
curl -fsSL https://unpkg.com/alpinejs@3.14.1/dist/cdn.min.js \
  -o backend/app/static/vendor/alpine.min.js
ls -l backend/app/static/vendor/
```

Expected: both files present, each > 1 KB (htmx ~48 KB, alpine ~43 KB).

- [ ] **Step 4: Update `layout.html` to reference local JS**

In `backend/app/templates/pages/layout.html`, replace the htmx CDN line:

```html
  <script src="https://unpkg.com/htmx.org@1.9.10"></script>
```

with:

```html
  <script src="/static/vendor/htmx.min.js"></script>
```

and replace the Alpine CDN line:

```html
  <script defer src="https://unpkg.com/alpinejs@3.14.1/dist/cdn.min.js"></script>
```

with:

```html
  <script defer src="/static/vendor/alpine.min.js"></script>
```

Leave the `{# player.js before Alpine ... #}` comment and the four `/static/*.js` defer scripts exactly as they are — the htmx-before-Alpine ordering is preserved.

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/pytest tests/unit/test_layout_assets.py -v`
Expected: PASS (4 passed).

- [ ] **Step 6: Commit**

```bash
git add backend/app/static/vendor/htmx.min.js backend/app/static/vendor/alpine.min.js \
        backend/app/templates/pages/layout.html tests/unit/test_layout_assets.py
git commit -m "perf(ui): vendor htmx + alpine locally to drop render-blocking CDN load"
```

---

## Task 2: Self-host the web fonts

**Files:**
- Create: `backend/app/static/vendor/fonts/inter-latin-wght-normal.woff2`
- Create: `backend/app/static/vendor/fonts/jetbrains-mono-latin-wght-normal.woff2`
- Modify: `backend/app/static/app.css`
- Modify: `backend/app/templates/pages/layout.html`
- Modify: `tests/unit/test_layout_assets.py`

- [ ] **Step 1: Extend the test (failing)**

Append to `tests/unit/test_layout_assets.py`:

```python
_CSS = _STATIC / "app.css"


def test_layout_does_not_reference_google_fonts():
    html = _LAYOUT.read_text(encoding="utf-8")
    assert "fonts.googleapis.com" not in html
    assert "fonts.gstatic.com" not in html


def test_css_self_hosts_fonts():
    css = _CSS.read_text(encoding="utf-8")
    assert "@font-face" in css
    assert "/static/vendor/fonts/inter-latin-wght-normal.woff2" in css
    assert "/static/vendor/fonts/jetbrains-mono-latin-wght-normal.woff2" in css


def test_vendored_fonts_exist_and_nonempty():
    for rel in (
        "vendor/fonts/inter-latin-wght-normal.woff2",
        "vendor/fonts/jetbrains-mono-latin-wght-normal.woff2",
    ):
        p = _STATIC / rel
        assert p.exists(), f"missing vendored font: {rel}"
        assert p.stat().st_size > 1024, f"vendored font too small: {rel}"
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/unit/test_layout_assets.py -v`
Expected: FAIL on the three new tests (google fonts still referenced, no `@font-face`, font files missing).

- [ ] **Step 3: Download the variable fonts (from @fontsource via jsDelivr)**

These are single-file variable fonts (one woff2 covers all weights). The current design uses Inter 400–700 and JetBrains Mono 400–600 — both within the variable range.

```bash
mkdir -p backend/app/static/vendor/fonts
curl -fsSL "https://cdn.jsdelivr.net/npm/@fontsource-variable/inter@5/files/inter-latin-wght-normal.woff2" \
  -o backend/app/static/vendor/fonts/inter-latin-wght-normal.woff2
curl -fsSL "https://cdn.jsdelivr.net/npm/@fontsource-variable/jetbrains-mono@5/files/jetbrains-mono-latin-wght-normal.woff2" \
  -o backend/app/static/vendor/fonts/jetbrains-mono-latin-wght-normal.woff2
ls -l backend/app/static/vendor/fonts/
```

Expected: two woff2 files, each tens of KB. If either URL 404s, list the directory to find the current latin `wght-normal` filename and adjust:
`curl -s "https://data.jsdelivr.com/v1/packages/npm/@fontsource-variable/inter@5" | grep -o 'inter-latin[^"]*woff2'`

- [ ] **Step 4: Add `@font-face` to `app.css`**

In `backend/app/static/app.css`, immediately after the `:root { ... }` token block (i.e. right before the `/* ─── base ─── */` comment), insert:

```css
/* ─── self-hosted fonts (variable: one file covers all weights) ───────── */
@font-face {
  font-family: "Inter";
  font-style: normal;
  font-weight: 100 900;
  font-display: swap;
  src: url("/static/vendor/fonts/inter-latin-wght-normal.woff2") format("woff2");
}
@font-face {
  font-family: "JetBrains Mono";
  font-style: normal;
  font-weight: 100 800;
  font-display: swap;
  src: url("/static/vendor/fonts/jetbrains-mono-latin-wght-normal.woff2") format("woff2");
}
```

(The `--f-sans` / `--f-mono` tokens already name `"Inter"` / `"JetBrains Mono"` with system fallbacks, so no other CSS changes are needed.)

- [ ] **Step 5: Remove the Google Fonts references from `layout.html`**

In `backend/app/templates/pages/layout.html`, delete these three lines:

```html
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
```

Leave the `<link rel="stylesheet" href="/static/app.css">` line — that's where the `@font-face` now lives.

- [ ] **Step 6: Run to verify it passes**

Run: `.venv/bin/pytest tests/unit/test_layout_assets.py -v`
Expected: PASS (all tests, including the original Task 1 ones).

- [ ] **Step 7: Commit**

```bash
git add backend/app/static/vendor/fonts backend/app/static/app.css \
        backend/app/templates/pages/layout.html tests/unit/test_layout_assets.py
git commit -m "perf(ui): self-host Inter + JetBrains Mono, drop Google Fonts CDN"
```

---

## Task 3: Click/action feedback (top progress bar + pressed state)

**Files:**
- Create: `backend/app/static/nav-feedback.js`
- Modify: `backend/app/templates/pages/layout.html`
- Modify: `backend/app/static/app.css`
- Modify: `tests/unit/test_layout_assets.py`

- [ ] **Step 1: Extend the test (failing)**

Append to `tests/unit/test_layout_assets.py`:

```python
def test_layout_includes_nav_feedback():
    html = _LAYOUT.read_text(encoding="utf-8")
    assert "/static/nav-feedback.js" in html
    assert 'id="app-progress"' in html


def test_nav_feedback_script_exists():
    p = _STATIC / "nav-feedback.js"
    assert p.exists()
    assert p.stat().st_size > 256
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/unit/test_layout_assets.py -v`
Expected: FAIL on `test_layout_includes_nav_feedback` and `test_nav_feedback_script_exists`.

- [ ] **Step 3: Create `backend/app/static/nav-feedback.js`**

```javascript
// Lightweight click/action feedback. No external dependency.
// Drives the #app-progress top bar from (a) htmx requests and
// (b) full-page navigation clicks (clip rows, rail links).
(function () {
  "use strict";

  function bar() {
    return document.getElementById("app-progress");
  }

  var active = 0;

  function start() {
    var b = bar();
    if (!b) return;
    active++;
    b.classList.remove("done");
    void b.offsetWidth; // reflow so re-adding .active restarts the transition
    b.classList.add("active");
  }

  function done() {
    var b = bar();
    if (!b) return;
    active = Math.max(0, active - 1);
    if (active > 0) return;
    b.classList.remove("active");
    b.classList.add("done");
  }

  function isBackgroundPoller(detail) {
    var elt = detail && detail.elt;
    var trg = elt && elt.getAttribute ? elt.getAttribute("hx-trigger") : null;
    return !!trg && trg.indexOf("every") !== -1;
  }

  // htmx requests (tabs, version pickers, bulk refresh). htmx already adds the
  // built-in `.htmx-request` class to the requesting element for the dim/cursor
  // styles — we only manage the progress bar here.
  document.body.addEventListener("htmx:beforeRequest", function (e) {
    if (isBackgroundPoller(e.detail)) return;
    start();
  });
  document.body.addEventListener("htmx:afterRequest", function (e) {
    if (isBackgroundPoller(e.detail)) return;
    done();
  });

  // Full-page navigations: same-origin links and rows wired with
  // onclick="location.href=...". Capture phase so we react before navigation.
  function isPlainClick(ev) {
    return !ev.defaultPrevented && ev.button === 0 &&
      !ev.metaKey && !ev.ctrlKey && !ev.shiftKey && !ev.altKey;
  }
  document.addEventListener("click", function (ev) {
    if (!isPlainClick(ev)) return;
    var nav = ev.target.closest('a[href], [onclick*="location.href"]');
    if (!nav) return;
    if (nav.target === "_blank") return;
    var href = nav.getAttribute("href");
    if (href && (href.charAt(0) === "#")) return;
    nav.classList.add("is-navigating");
    start();
  }, true);

  // Back/forward (bfcache) restore: clear any stuck bar.
  window.addEventListener("pageshow", function () {
    var b = bar();
    if (b) { b.classList.remove("active"); b.classList.remove("done"); }
    active = 0;
    var stuck = document.querySelectorAll(".is-navigating");
    for (var i = 0; i < stuck.length; i++) stuck[i].classList.remove("is-navigating");
  });
})();
```

- [ ] **Step 4: Wire it into `layout.html`**

In `backend/app/templates/pages/layout.html`, add the script reference among the other `/static` defer scripts — place it immediately **before** the Alpine line so it loads after htmx:

```html
  <script defer src="/static/nav-feedback.js"></script>
  <script defer src="/static/vendor/alpine.min.js"></script>
```

Then add the progress-bar element as the very first child of `<body>`:

```html
<body>
  <div id="app-progress" aria-hidden="true"></div>
```

(Insert the `<div>` directly after the existing `<body>` tag; leave the Jinja `{%- set _ctx ... -%}` lines that follow untouched.)

- [ ] **Step 5: Add the feedback styles to `app.css`**

Append to the end of `backend/app/static/app.css`:

```css
/* ─── click / action feedback ────────────────────────────────────────── */
#app-progress {
  position: fixed;
  top: 0; left: 0;
  height: 2px;
  width: 0;
  background: var(--accent);
  z-index: 9999;
  opacity: 0;
  pointer-events: none;
}
#app-progress.active {
  opacity: 1;
  width: 90%;
  transition: width 8s cubic-bezier(0.1, 0.7, 0.6, 1), opacity 0.15s ease;
}
#app-progress.done {
  width: 100%;
  opacity: 0;
  transition: width 0.2s ease, opacity 0.3s ease 0.1s;
}

/* htmx adds .htmx-request to the in-flight element automatically */
.htmx-request {
  opacity: 0.6;
  cursor: progress;
  pointer-events: none;
}

/* full-page navigation: immediate press feedback on the clicked row/link */
.is-navigating {
  opacity: 0.6;
  cursor: progress;
}
```

- [ ] **Step 6: Run to verify it passes**

Run: `.venv/bin/pytest tests/unit/test_layout_assets.py -v`
Expected: PASS (all tests).

- [ ] **Step 7: Commit**

```bash
git add backend/app/static/nav-feedback.js backend/app/static/app.css \
        backend/app/templates/pages/layout.html tests/unit/test_layout_assets.py
git commit -m "feat(ui): top progress bar + pressed state for click/htmx feedback"
```

---

## Task 4: Fix the Cache page scroll

**Files:**
- Modify: `backend/app/static/app.css`

Root cause (from the spec): the `.app` grid row is `1fr` (defaults to `min-height: auto`) and `.main` has `overflow: hidden` with no `min-height: 0`, so tall content grows the `main` track past the viewport and the Cache page's own `overflow-y: auto` never engages.

- [ ] **Step 1: Constrain the grid track**

In `backend/app/static/app.css`, in the `.app` rule, change:

```css
  grid-template-rows:    40px 1fr;
```

to:

```css
  grid-template-rows:    40px minmax(0, 1fr);
```

- [ ] **Step 2: Allow `.main` to shrink below content size**

In the `.main` rule (`grid-area: main; overflow: hidden; display: flex; flex-direction: column; min-width: 0;`), add `min-height: 0;`:

```css
.main {
  grid-area: main;
  overflow: hidden;
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
}
```

- [ ] **Step 3: Run the existing asset tests (no regression)**

Run: `.venv/bin/pytest tests/unit/test_layout_assets.py -v`
Expected: PASS (these tests don't touch the grid, but confirm nothing else broke in `app.css`).

- [ ] **Step 4: Manual browser verification**

Follow the CatDV single-seat discipline in `CLAUDE.md` before starting a server: check `lsof`/`ps` for an existing instance and reuse it; if you must start one, shut it down with `kill -TERM` and confirm `Application shutdown complete.` in the log.

Verify in a browser (resize the window short to force overflow):
- **Cache page** (`/cache`): you can scroll down to the last inventory/queue row. Switch tabs (All / Queue / Local / AI) — still scrollable.
- **Clips** (`/`), **Clip detail** (`/clips/{id}`), **Prompts** (`/prompts`): still scroll correctly via their own inner containers — no double scrollbar, no clipped content, topbar/rail stay fixed.

- [ ] **Step 5: Commit**

```bash
git add backend/app/static/app.css
git commit -m "fix(ui): cache page scrolls — bound grid main track to viewport"
```

---

## Task 5: Full gate + end-to-end manual verification

**Files:** none (verification only)

- [ ] **Step 1: Run the full lint/type/test gate**

```bash
.venv/bin/ruff check backend tests
.venv/bin/ruff format --check backend tests
.venv/bin/basedpyright backend tests
.venv/bin/lint-imports
.venv/bin/pytest
```

Expected: all green. (The new test file lives under `tests/`, which `interrogate` excludes, so no docstrings are required on the test functions.)

- [ ] **Step 2: Manual asset + feedback verification in the browser**

Respecting the CatDV seat discipline (see Task 4 Step 4):
- **Local assets:** open DevTools → Network, hard-reload a page. Confirm `htmx.min.js`, `alpine.min.js`, and the two `*.woff2` files load from `/static/...` (not `unpkg.com` / `fonts.gstatic.com`). Bonus: block `unpkg.com` in DevTools and confirm the page still works.
- **Feedback:** click a clip row, a rail link, and a Cache tab. Confirm the top progress bar appears immediately on click and the clicked element shows the dim/`progress` cursor — i.e. you can tell instantly that the click registered. Confirm the 2s/5s/10s background pollers (connection pill, sync drawer, cache queue) do **not** flash the bar.

- [ ] **Step 3: Final confirmation**

Confirm working tree is clean (`git status`) and all four feature commits are present (`git log --oneline -5`). Report results; do not merge or open a PR unless asked.

---

## Notes for the implementer

- **No data-path changes.** Do not touch `adapter.py`, cache TTLs, or convert navigations to `hx-boost` — those were explicitly deferred in the spec.
- **CatDV seat discipline is mandatory** when running a dev server (see `CLAUDE.md`): one seat, check before starting, `kill -TERM` to shut down so the session is released.
- **ADR:** per `CLAUDE.md`, if the font-vendoring approach (single-file variable fonts via @fontsource) or the "feedback-only, no hx-boost" scoping would make a future reader ask *why*, add a short ADR under `docs/adr/` and update `docs/decisions.md` before finishing. The grid one-liner and the asset move are self-evident from the diff and don't need one.
```
