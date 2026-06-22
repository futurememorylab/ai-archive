# Annotation Follow-Playback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a clip plays, highlight the currently-active marker on the timeline (already done) AND in the annotation column, auto-scrolling the column to keep the active marker visible with minimal movement.

**Architecture:** Reuse the existing `isMarkerActive(m)` (the single "is this marker playing now?" predicate that already drives the orange timeline highlight) to also drive an `.active` class on the column cards — so highlighting is purely reactive, no new state. A `$watch('current')` in the player component calls `followActiveAnno()`, which reads the visible marker cards from the DOM, picks the first active one (pure helper `annoActiveAnchorIndex`), and computes a comfort-band nearest-edge scroll (pure helper `annoComputeScroll`). A brief manual-scroll pause keeps the app from fighting the user.

**Tech Stack:** Vanilla JS + Alpine.js (CDN, no build step), Jinja2 templates, plain CSS with design tokens. Tests are pytest text-scan / template-render guards (this is a Python-only repo — ADR 0001; there is no JS test runner).

## Global Constraints

- **Times are in seconds** everywhere (`in_secs` / `out_secs` / `current`) — existing convention; never convert to frames except for display via `tc()`.
- **One source of truth for "active":** reuse `player.js::isMarkerActive(m)` for both timeline and column. Do not add a parallel predicate.
- **Scope:** feature targets the clip-detail page only — **published + draft markers**, `review_mode=True`. `_anno_panels.html` is also used by Studio with `review_mode=False`; the new bindings must NOT appear there (Studio's scope has no player methods — `tests/unit/test_anno_panels_review_mode.py::test_studio_read_only_has_no_player_only_method_refs`).
- **Design tokens only:** highlight uses `var(--accent)` (#f5a623) — no raw hex (`docs/design-language.md`; `tests/unit/test_design_language_guard.py`).
- **Tuning values (verbatim from spec):** comfort-band margin = **20%** of viewport height top & bottom; manual-scroll resume after **4000 ms**; smooth-vs-instant threshold = **1 viewport height**.
- **No new Alpine store, no `location.reload`, no network calls** — this is pure client-side view behaviour.
- Markers arrive **sorted ascending by `in_secs`** (clip_detail view-model guarantee; player.js header comment) — `annoActiveAnchorIndex` relies on it for "first active = earliest-starting".

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `backend/app/static/player.js` | Player Alpine component + transport | Add module-scope pure helpers `annoActiveAnchorIndex` / `annoComputeScroll`; add follow state + `followActiveAnno()` + `_programScroll()`; wire `$watch` + manual-scroll listener in `init()`; clear suspension in `seek()`. |
| `backend/app/templates/pages/_anno_panels.html` | Published annotation list (shared w/ Studio) | On the published marker `<article>`, **gated by `{% if review_mode %}`**: add `data-anno-marker` + `data-in`/`data-out` + `:class="{ active: isMarkerActive({...}) }"`. |
| `backend/app/templates/pages/_anno_draft.html` | Draft review list (clip page only) | On the draft marker card, add `data-anno-marker` + `:data-in`/`:data-out` and merge `active: isMarkerActive(m)` into its existing `:class`. |
| `backend/app/static/app.css` | Styles + tokens | Add `.marker.active` / `.ri-card.ri-marker.active` highlight rule (inset box-shadow + tint, `var(--accent)`). |
| `tests/unit/test_anno_follow_playback.py` | Guard tests | New file: assert helpers exist in player.js, bindings/data-attrs render correctly (and are absent in Studio), CSS rule present, driver wiring present. |

---

## Task 1: Pure scroll-math helpers in player.js

Two `this`-free, DOM-free functions so the comfort-band arithmetic and anchor selection are simple to reason about and guard-testable by text scan.

**Files:**
- Modify: `backend/app/static/player.js` (add at module top, above `document.addEventListener("alpine:init", …)` — currently line 5)
- Test: `tests/unit/test_anno_follow_playback.py` (create)

**Interfaces:**
- Produces:
  - `annoActiveAnchorIndex(markers, current) -> number` — index of the first marker whose `[in_secs, out_secs]` window contains `current` (40 ms fallback when `out_secs == null`); `-1` when none (gap). `markers` is `[{in_secs:number, out_secs:number|null}, …]` in ascending `in_secs` order.
  - `annoComputeScroll({scrollTop, viewportHeight, cardTop, cardHeight, bandMargin}) -> {scrollTo:number, behavior:'smooth'|'auto'} | null` — `cardTop` is the anchor card's top relative to the scroller viewport top. Returns `null` when the card is already fully inside the comfort band.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_anno_follow_playback.py`:

```python
"""Guards for the annotation follow-playback feature (clip-detail page).

Python-only repo (ADR 0001): no JS runner, so the pure scroll-math is
verified by the manual acceptance flows in the spec. These guards pin the
wiring contract — helper names, DOM data-attrs/active bindings, CSS rule,
and the Studio read-only exclusion — so it can't silently regress.
"""

from pathlib import Path

from backend.app.routes.pages.templates import templates

ROOT = Path(__file__).resolve().parents[2]
PLAYER_JS = (ROOT / "backend/app/static/player.js").read_text()
APP_CSS = (ROOT / "backend/app/static/app.css").read_text()


def test_pure_helpers_defined():
    # Module-scope, this-free helpers — the testable core of the feature.
    assert "function annoActiveAnchorIndex(" in PLAYER_JS
    assert "function annoComputeScroll(" in PLAYER_JS


def test_anchor_helper_returns_minus_one_sentinel():
    # Gap -> -1 (not null/undefined); followActiveAnno relies on `< 0`.
    assert "return -1;" in PLAYER_JS


def test_compute_scroll_uses_viewport_threshold_for_behavior():
    # Smooth for small corrections, instant ('auto') for jumps > 1 viewport.
    assert '"auto"' in PLAYER_JS and '"smooth"' in PLAYER_JS
    assert "viewportHeight" in PLAYER_JS
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_anno_follow_playback.py -v`
Expected: FAIL — `assert "function annoActiveAnchorIndex(" in PLAYER_JS` (helpers not defined yet).

- [ ] **Step 3: Add the helpers at the top of player.js**

Insert immediately before line 5 (`document.addEventListener("alpine:init", () => {`):

```javascript
// ─── follow-playback pure helpers ──────────────────────────────────
// Kept free of `this` and the DOM so the comfort-band logic is easy to
// reason about. Behavioural coverage is the spec's manual acceptance flows;
// tests/unit/test_anno_follow_playback.py pins their presence.

// Index of the first marker whose [in,out] window contains `current`
// (markers arrive in_secs-ascending, so "first" = earliest-starting). A
// marker with no out_secs gets a 40ms window, matching isMarkerActive.
// Returns -1 when the playhead is in a gap.
function annoActiveAnchorIndex(markers, current) {
  for (let i = 0; i < markers.length; i++) {
    const m = markers[i];
    if (m.in_secs == null) continue;
    const out = m.out_secs != null ? m.out_secs : m.in_secs + 0.04;
    if (current >= m.in_secs && current <= out) return i;
  }
  return -1;
}

// Comfort-band, nearest-edge scroll. The band is the viewport inset by
// `bandMargin` top and bottom. If the anchor card is fully inside the band,
// return null (no movement — this is why an already-visible marker, e.g. the
// first one, never scrolls). Otherwise scroll the minimum needed to bring the
// card to the nearest band edge. Jumps longer than one viewport are instant
// ('auto') so a far seek doesn't slowly glide.
function annoComputeScroll({ scrollTop, viewportHeight, cardTop, cardHeight, bandMargin }) {
  const bandTop = bandMargin;
  const bandBottom = viewportHeight - bandMargin;
  const cardBottom = cardTop + cardHeight;
  let target;
  if (cardTop < bandTop) {
    target = scrollTop + (cardTop - bandTop);          // bring top to band top
  } else if (cardBottom > bandBottom) {
    target = scrollTop + (cardBottom - bandBottom);    // bring bottom to band bottom
  } else {
    return null;                                        // already inside the band
  }
  if (target < 0) target = 0;
  const behavior = Math.abs(target - scrollTop) > viewportHeight ? "auto" : "smooth";
  return { scrollTo: target, behavior };
}

```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/test_anno_follow_playback.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/static/player.js tests/unit/test_anno_follow_playback.py
git commit -m "feat(#80): pure helpers for follow-playback scroll math"
```

---

## Task 2: Column highlight bindings + CSS (published + draft markers)

Make the column cards light up reactively when their marker is active, reusing `isMarkerActive`. Add the DOM hooks (`data-anno-marker` + `data-in`/`data-out`) that Task 3's scroller reads. This task delivers visible highlighting on its own — independently testable before any auto-scroll exists.

**Files:**
- Modify: `backend/app/templates/pages/_anno_panels.html:50` (published `<article class="marker">`)
- Modify: `backend/app/templates/pages/_anno_draft.html:45-46` (draft `.ri-card.ri-marker`)
- Modify: `backend/app/static/app.css` (add rule after the marker base rules, near line 807 / 2490)
- Test: `tests/unit/test_anno_follow_playback.py` (append)

**Interfaces:**
- Consumes: `isMarkerActive(m)` from player.js (exists, line 250).
- Produces: DOM contract for Task 3 — marker cards carry attribute `data-anno-marker`, `data-in` = `in_secs`, `data-out` = `out_secs` (empty string `""` when null). Published card hooks render only when `review_mode` is true.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_anno_follow_playback.py`:

```python
_PANELS = {
    "markers": [{
        "name": "Scene 1", "in_secs": 1.5, "out_secs": 5.6,
        "category": "x", "description": "d", "item_id": 7, "decision": "pending",
    }],
    "fields": [], "notes": None, "big_notes": None, "note_items": [],
    "fps": 25.0,
}


def _render_panels(review_mode=None):
    ctx = {
        "panels": _PANELS, "scope": "published",
        "clip": {"fps": 25.0, "kind": "video"}, "show_history": False,
    }
    if review_mode is not None:
        ctx["review_mode"] = review_mode
    return templates.env.get_template("pages/_anno_panels.html").render(**ctx)


def test_published_marker_has_follow_hooks_in_review_mode():
    html = _render_panels()  # defaults review_mode=True (clip page)
    assert "data-anno-marker" in html
    assert 'data-in="1.5"' in html
    assert 'data-out="5.6"' in html
    assert "isMarkerActive({in_secs: 1.5, out_secs: 5.6" in html


def test_published_marker_follow_hooks_absent_in_studio():
    # Studio (review_mode=False) shares this partial but has no player scope;
    # isMarkerActive there would break Alpine.initTree.
    html = _render_panels(review_mode=False)
    assert "data-anno-marker" not in html
    assert "isMarkerActive" not in html


def test_css_has_active_card_rule():
    assert ".marker.active" in APP_CSS
    assert ".ri-card.ri-marker.active" in APP_CSS


def test_draft_marker_has_follow_hooks():
    draft = (ROOT / "backend/app/templates/pages/_anno_draft.html").read_text()
    assert "data-anno-marker" in draft
    assert ':data-in="m.in_secs"' in draft
    assert "active: isMarkerActive(m)" in draft
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_anno_follow_playback.py -v`
Expected: FAIL — `test_published_marker_has_follow_hooks_in_review_mode` (no `data-anno-marker` yet).

- [ ] **Step 3a: Add follow hooks to the published marker card**

In `backend/app/templates/pages/_anno_panels.html`, replace line 50:

```html
    <article class="marker" @click="seek({{ m.in_secs }})">
```

with (hooks gated to `review_mode` so Studio stays clean):

```html
    <article class="marker"
             {% if review_mode %}data-anno-marker
             data-in="{{ m.in_secs }}" data-out="{{ m.out_secs if m.out_secs is not none else '' }}"
             :class="{ active: isMarkerActive({in_secs: {{ m.in_secs }}, out_secs: {{ m.out_secs if m.out_secs is not none else 'null' }} }) }"{% endif %}
             @click="seek({{ m.in_secs }})">
```

- [ ] **Step 3b: Add follow hooks to the draft marker card**

In `backend/app/templates/pages/_anno_draft.html`, replace lines 45-46:

```html
    <div class="ri-card ri-marker" :class="{ editing: editingItemId === m.item_id }"
         @click="seek(m.in_secs)" title="Jump to marker and play">
```

with:

```html
    <div class="ri-card ri-marker" data-anno-marker
         :data-in="m.in_secs" :data-out="m.out_secs ?? ''"
         :class="{ editing: editingItemId === m.item_id, active: isMarkerActive(m) }"
         @click="seek(m.in_secs)" title="Jump to marker and play">
```

- [ ] **Step 3c: Add the highlight CSS**

In `backend/app/static/app.css`, after the `.ri-card.ri-marker:hover` rule (line 2490), add:

```css
/* Follow-playback: the marker whose segment is under the playhead. Inset
   shadow (not a border) so highlighting never reflows the card. Mirrors the
   timeline .range.active accent. */
.marker.active,
.ri-card.ri-marker.active {
  box-shadow: inset 3px 0 0 0 var(--accent);
  background: color-mix(in oklab, var(--accent) 12%, var(--panel));
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/test_anno_follow_playback.py -v`
Expected: PASS (7 tests). Also run the shared-partial guard to confirm no Studio regression:
Run: `.venv/bin/python -m pytest tests/unit/test_anno_panels_review_mode.py tests/unit/test_design_language_guard.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/templates/pages/_anno_panels.html backend/app/templates/pages/_anno_draft.html backend/app/static/app.css tests/unit/test_anno_follow_playback.py
git commit -m "feat(#80): highlight the active marker card in the annotation column"
```

---

## Task 3: Follow driver — watch, scroll, manual-scroll pause

Wire `current` changes to the comfort-band scroll, reading the visible cards from the DOM. Add the brief manual-scroll pause and resume-on-seek. This consumes Task 1's helpers and Task 2's DOM contract.

**Files:**
- Modify: `backend/app/static/player.js` — add state fields (near line 26), wiring in `init()` (after line 69), `seek()` (line 187), and new methods `_programScroll` / `followActiveAnno` (e.g. after `isMarkerActive`, line 254).
- Test: `tests/unit/test_anno_follow_playback.py` (append)

**Interfaces:**
- Consumes: `annoActiveAnchorIndex`, `annoComputeScroll` (Task 1); marker cards with `data-anno-marker` / `data-in` / `data-out` (Task 2); existing `this.$root`, `this.current`, `this.scope`, `this.$refs.video`.
- Produces: no new public API; behaviour only.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_anno_follow_playback.py`:

```python
def test_driver_wired_in_player():
    # Watch on current drives the scroll; scope watch re-centres on tab switch.
    assert 'this.$watch("current"' in PLAYER_JS
    assert 'this.$watch("scope"' in PLAYER_JS
    assert "followActiveAnno()" in PLAYER_JS


def test_driver_reads_visible_cards_only():
    # offsetParent filter excludes the hidden scope/tab (display:none).
    assert "[data-anno-marker]" in PLAYER_JS
    assert "offsetParent" in PLAYER_JS


def test_manual_scroll_pause_and_resume_on_seek():
    assert "followSuspended" in PLAYER_JS
    assert "4000" in PLAYER_JS              # resume window
    assert "_selfScrolling" in PLAYER_JS    # ignore our own programmatic scroll
    # seek() (timeline / card click) is intentional nav -> resumes immediately.
    assert "this.followSuspended = false" in PLAYER_JS
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_anno_follow_playback.py::test_driver_wired_in_player -v`
Expected: FAIL — `this.$watch("current"` not present yet.

- [ ] **Step 3a: Add follow state fields**

In `backend/app/static/player.js`, after line 26 (`editingItemId: null,`), add:

```javascript

    // ─── follow-playback state ────────────────────────────────────
    // followSuspended: true while a manual scroll temporarily wins over
    // auto-follow. _selfScrolling: true during our own programmatic scroll so
    // the scroll listener doesn't mistake it for the user.
    followSuspended: false,
    _selfScrolling: false,
    _followResumeTimer: null,
    _selfScrollTimer: null,
```

- [ ] **Step 3b: Wire the watches + manual-scroll listener in `init()`**

In `init()`, immediately after line 69 (`v.addEventListener("ended", buffOff);`) and before the closing `},`, add:

```javascript

      // Follow-playback: keep the active marker visible in the column.
      // `current` updates ~4×/sec (frame-quantized above); `scope` re-centres
      // when switching published⇄draft.
      this.$watch("current", () => this.followActiveAnno());
      this.$watch("scope", () => this.followActiveAnno());
      const annoBody = this.$root && this.$root.querySelector(".anno-body");
      if (annoBody) {
        annoBody.addEventListener("scroll", () => {
          if (this._selfScrolling) return;   // our own scroll, not the user's
          this.followSuspended = true;
          clearTimeout(this._followResumeTimer);
          this._followResumeTimer = setTimeout(() => {
            this.followSuspended = false;
          }, 4000);
        });
      }
```

- [ ] **Step 3c: Resume follow on intentional seek**

In `seek()` (line 187), insert at the very top of the method body, before `const v = this.$refs.video;`:

```javascript
      // A seek (timeline click or annotation-card click) is intentional
      // navigation — cancel any manual-scroll pause so the list snaps to it.
      this.followSuspended = false;
      clearTimeout(this._followResumeTimer);
```

- [ ] **Step 3d: Add `_programScroll` and `followActiveAnno`**

After `isMarkerActive` (closes at line 254), add:

```javascript

    // Scroll the column, flagging it as our own so the scroll listener
    // doesn't read it as a manual scroll. Smooth scroll emits events over a
    // few hundred ms, so hold the flag longer for 'smooth' than 'auto'.
    _programScroll(el, top, behavior) {
      this._selfScrolling = true;
      clearTimeout(this._selfScrollTimer);
      el.scrollTo({ top, behavior });
      this._selfScrollTimer = setTimeout(() => {
        this._selfScrolling = false;
      }, behavior === "smooth" ? 700 : 100);
    },

    // Keep the active marker card visible. Reads the visible scope/tab's cards
    // straight from the DOM (hidden scopes are display:none → offsetParent
    // null), picks the first active one, and applies a comfort-band scroll.
    followActiveAnno() {
      if (this.followSuspended) return;
      const body = this.$root && this.$root.querySelector(".anno-body");
      if (!body) return;
      const cards = Array.from(body.querySelectorAll("[data-anno-marker]"))
        .filter(el => el.offsetParent !== null);
      if (!cards.length) return;
      const markers = cards.map(el => ({
        in_secs: parseFloat(el.dataset.in),
        out_secs: el.dataset.out === "" ? null : parseFloat(el.dataset.out),
      }));
      const idx = annoActiveAnchorIndex(markers, this.current);
      if (idx < 0) return;                    // playhead in a gap: don't move
      const bodyRect = body.getBoundingClientRect();
      const cardRect = cards[idx].getBoundingClientRect();
      const plan = annoComputeScroll({
        scrollTop: body.scrollTop,
        viewportHeight: body.clientHeight,
        cardTop: cardRect.top - bodyRect.top,
        cardHeight: cardRect.height,
        bandMargin: body.clientHeight * 0.2,
      });
      if (!plan) return;                      // already inside the comfort band
      this._programScroll(body, plan.scrollTo, plan.behavior);
    },
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/test_anno_follow_playback.py -v`
Expected: PASS (all tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/static/player.js tests/unit/test_anno_follow_playback.py
git commit -m "feat(#80): auto-scroll annotation column to follow playback"
```

---

## Task 4: Full-suite check + ADR

Confirm nothing else regressed and record the design decisions.

**Files:**
- Create: `docs/adr/NNNN-annotation-follow-playback.md` (next number after the last in `docs/adr/`)
- Modify: `docs/decisions.md` (append the new ADR row)

- [ ] **Step 1: Run the relevant guard suites**

Run: `.venv/bin/python -m pytest tests/unit/test_anno_follow_playback.py tests/unit/test_anno_panels_review_mode.py tests/unit/test_design_language_guard.py tests/unit/test_no_x_data_stack.py -v`
Expected: all PASS.

- [ ] **Step 2: Write the ADR**

Determine the next number: `ls docs/adr/ | sort | tail -1`. Create `docs/adr/NNNN-annotation-follow-playback.md` (MADR-lite) capturing: (a) reuse `isMarkerActive` as the single active-predicate for timeline + column; (b) comfort-band nearest-edge scroll with the 20% / 4000ms / 1-viewport constants and why minimal-movement beats centering; (c) anchor = first active card in `in_secs` order for stable overlap behaviour; (d) Python text-scan guards instead of a JS test runner (no node tooling — consistent with ADR 0001), with behaviour covered by the spec's manual acceptance flows.

- [ ] **Step 3: Update the decisions index**

Append the new ADR entry to the table in `docs/decisions.md`.

- [ ] **Step 4: Commit**

```bash
git add docs/adr docs/decisions.md
git commit -m "docs(#80): ADR for annotation follow-playback"
```

---

## Manual acceptance flows

Run on a dev server (use the `server-start` skill — single-seat discipline). Reference clip: `http://127.0.0.1:8765/clips/889070`. Use a clip with enough markers to overflow the column.

1. **Timeline + column highlight in sync.** Press play. As the playhead enters each marker segment, the timeline range turns amber **and** the matching column card gets the inset-amber highlight; both clear when the playhead leaves the segment.
2. **No move when already visible.** With the first marker on screen, play through it — the card highlights, the list does not scroll.
3. **Auto-scroll to off-screen active (minimal).** Keep playing until the active marker would fall below the visible area — the list scrolls just enough to bring it to the lower comfort edge (≈80% down), not centered, not flush to the very bottom.
4. **Timeline seek follows (instant on far jumps).** Click the timeline near the end — the column jumps (no slow glide) so the now-active card is visible and highlighted.
5. **Click-to-seek stays consistent.** Click a marker card lower in the list — the video seeks to its start (existing behaviour), the card highlights, and the list does not jump away from it.
6. **Gap = no movement.** Scrub to a point between two markers — no card highlighted, list unchanged.
7. **Manual-scroll pause.** During playback, scroll the column away from the active card — it stays put for ~4s (no immediate snap-back), then resumes following.
8. **Draft scope.** Switch to the Draft tab on a clip with draft markers; repeat flows 1–3 — draft cards highlight and the draft list auto-scrolls identically.
