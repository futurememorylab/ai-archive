# UI Design-Language Consolidation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace duplicated/hand-rolled UI code with one small reusable library (tokens + canonical CSS classes + Jinja macros + `format.js`), migrate existing templates onto it, fix 8 UI issues, and document it so future agents reuse rather than re-create.

**Architecture:** No-Node/no-build (ADR 0001). Four layers: CSS custom-property tokens, canonical CSS component classes in `app.css`, parameterized Jinja macros in `templates/components/_ui.html`, and global JS helpers in `static/format.js`. Templates migrate onto these; Alpine-bound widgets keep literal HTML but adopt canonical classes.

**Tech Stack:** FastAPI + Jinja2 templates, Alpine.js, HTMX, plain CSS (`app.css`), pytest (template/route render tests; **no JS test harness** — JS correctness is verified by grep-guards + the spec's manual acceptance flows).

**Spec:** `docs/specs/2026-05-27-ui-design-language-consolidation-design.md`

---

## Conventions for every task

- Run the backend test suite with: `python -m pytest <path> -q` from repo root (use the project's 3.12/3.13 venv; **not** 3.14 — see CLAUDE.md).
- "Grep-guard" steps assert old code is gone; run from repo root.
- Manual acceptance is deferred to a single final pass against a running server (start/stop per CLAUDE.md seat discipline) — each task lists which numbered spec flow it satisfies.
- Commit after each task. Keep `backend/app/static/app.css` edits surgical (it is large and shared).

---

## Task 0: Re-sync base and confirm clean baseline

**Files:** none (git + test only)

- [ ] **Step 1: Rebase onto the merged review work**

The spec is based on `feat/draft-review-accept`, which was still in flux. Before implementing, re-sync onto its final/merged form (prefer `main` once that branch has merged):

```bash
git fetch origin
# If feat/draft-review-accept has merged to main:
git rebase origin/main
# else rebase onto its current tip:
git rebase feat/draft-review-accept
```

Expected: clean rebase (only the two spec commits replay). Resolve any spec-file conflict by keeping our version.

- [ ] **Step 2: Re-enumerate the live targets** (the base may have added files, e.g. a `/review` page)

Run and note the output — later grep-guards must cover any new files:

```bash
grep -rln 'ra-btn\|ca-btn\|pg-btn\|\btbtn\b\|bulk-btn\|anno-scope-btn\|btn-compare\|btn-diff-toggle\|btn-close-cmp\|pc-vchip-btn\|filter-reset\|model-picker-btn' backend/app/templates
grep -rn 'padStart(2' backend/app/static/*.js | grep -v vendor
grep -rn 'bytesHuman' backend/app/static/*.js backend/app/templates
```

- [ ] **Step 3: Confirm baseline tests pass**

Run: `python -m pytest tests/unit tests/integration -q`
Expected: all pass (record the count). If anything fails pre-change, report and stop.

- [ ] **Step 4: Commit (no-op marker)** — skip if rebase produced no changes.

---

## Task 1: `format.js` — shared timecode, bytes, autosize

**Files:**
- Create: `backend/app/static/format.js`
- Modify: `backend/app/templates/pages/layout.html` (add script, first in the defer block)
- Modify: `backend/app/static/player.js` (~line 103-105), `backend/app/static/studio.js` (~line 93-94), `backend/app/static/liveSession.js` (~line 225-227), `backend/app/static/row_select.js` (~line 20), `backend/app/templates/cache_page.html:109`
- Test: `tests/unit/test_layout_assets.py`

- [ ] **Step 1: Write the failing test (format.js is loaded before feature scripts)**

Add to `tests/unit/test_layout_assets.py` (mirror the existing assertions in that file for script ordering):

```python
def test_format_js_loaded_before_feature_scripts():
    html = (TEMPLATES_DIR / "pages" / "layout.html").read_text()
    assert "/static/format.js" in html
    # must appear before player.js so window.fmt* exist when Alpine inits
    assert html.index("/static/format.js") < html.index("/static/player.js")
```

(If `TEMPLATES_DIR` is not already defined in the file, reuse the path constant the existing tests there use.)

- [ ] **Step 2: Run it, verify it fails**

Run: `python -m pytest tests/unit/test_layout_assets.py -q`
Expected: FAIL (`/static/format.js` not found).

- [ ] **Step 3: Create `backend/app/static/format.js`**

```js
// Shared formatting + UI helpers. Loaded first so window.* exist before
// Alpine/feature scripts initialize. No build step (ADR 0001) — plain globals.
(function () {
  // seconds -> "M:SS" or "H:MM:SS"
  function fmtTimecode(seconds) {
    const s = Math.max(0, Math.floor(Number(seconds) || 0));
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    const sec = s % 60;
    const pad = (x) => String(x).padStart(2, "0");
    return h > 0 ? `${h}:${pad(m)}:${pad(sec)}` : `${m}:${pad(sec)}`;
  }

  // byte count -> "12.3 MB" (1024-based)
  function fmtBytes(n) {
    n = Number(n) || 0;
    if (!n) return "0 B";
    const u = ["B", "KB", "MB", "GB", "TB"];
    let i = 0;
    while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
    return (i === 0 ? n.toFixed(0) : n.toFixed(1)) + " " + u[i];
  }

  // grow a textarea to fit its content
  function autosize(el) {
    if (!el) return;
    el.style.height = "auto";
    el.style.height = el.scrollHeight + "px";
  }

  window.fmtTimecode = fmtTimecode;
  window.fmtBytes = fmtBytes;
  window.autosize = autosize;

  // auto-bind autosize to any .txt-area on input
  document.addEventListener("input", (e) => {
    if (e.target.classList && e.target.classList.contains("txt-area")) autosize(e.target);
  });
  document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll("textarea.txt-area").forEach(autosize);
  });
})();
```

- [ ] **Step 4: Wire it into `layout.html`** — add as the first deferred script:

In `backend/app/templates/pages/layout.html`, immediately before the `player.js` line (currently line 11) add:

```html
  <script defer src="/static/format.js"></script>
```

- [ ] **Step 5: Run the test, verify it passes**

Run: `python -m pytest tests/unit/test_layout_assets.py -q`
Expected: PASS.

- [ ] **Step 6: Replace the duplicated implementations with the globals**

`backend/app/static/row_select.js` — replace the `bytesHuman(n) { ... }` method body so the factory delegates:

```js
    bytesHuman(n) { return window.fmtBytes(n); },
```

`backend/app/static/player.js` (the block around 103-105 that builds `mm`/`pad`): replace its local mm:ss assembly with `return window.fmtTimecode(ts);` (keep the surrounding method signature; remove the now-unused `mm`/`pad` locals).

`backend/app/static/studio.js` (around 93-94) and `backend/app/static/liveSession.js` (around 225-227): replace the local `padStart` mm:ss assembly with `window.fmtTimecode(s)` (studio) / `window.fmtTimecode(this.elapsed)` (liveSession — use whatever the local seconds variable is named there). Read each call site first and preserve the variable being formatted.

- [ ] **Step 7: Grep-guard — no hand-rolled timecode/bytes remain**

Run:
```bash
grep -rn 'padStart(2' backend/app/static/*.js | grep -v vendor
grep -rn 'function bytesHuman\|bytesHuman(n) {$' backend/app/static/*.js
```
Expected: no mm:ss assembly outside `format.js`; `row_select.js` only delegates. (One `padStart` inside `format.js` is correct.)

- [ ] **Step 8: Run full suite + commit**

Run: `python -m pytest tests/unit tests/integration -q` → PASS.
```bash
git add backend/app/static/format.js backend/app/templates/pages/layout.html \
        backend/app/static/player.js backend/app/static/studio.js \
        backend/app/static/liveSession.js backend/app/static/row_select.js \
        tests/unit/test_layout_assets.py
git commit -m "refactor(ui): add format.js; dedupe timecode + bytesHuman across JS"
```

Satisfies spec flow #7.

---

## Task 2: Tokens + canonical `.btn` system + accent checkboxes + CSS dedupe

**Files:** Modify `backend/app/static/app.css`

- [ ] **Step 1: Add component tokens** to the `:root` block (after the existing `--r-*` radii):

```css
  --btn-h: 32px;
  --btn-h-sm: 26px;
  --field-h: 32px;
  --field-label-h: 24px;
```

- [ ] **Step 2: Replace BOTH `.btn` definitions with one canonical block.**

Delete the second `.btn`/`.btn:hover`/`.btn.ghost` block (currently ~lines 825-835) entirely. Replace the first `.btn` block (~lines 256-283, through `.btn.is-disabled`) with:

```css
.btn {
  display: inline-flex; align-items: center; justify-content: center; gap: 6px;
  height: var(--btn-h); padding: 0 14px;
  border-radius: var(--r-2);
  border: 1px solid var(--line-2);
  background: var(--surface);
  color: var(--text);
  font: 500 13px/1 var(--f-sans);
  cursor: pointer;
}
.btn:hover { background: var(--surface-2); }
.btn.primary {
  background: var(--accent); color: var(--accent-fg);
  border-color: color-mix(in oklab, var(--accent) 60%, var(--line-2));
  font-weight: 600;
}
.btn.primary:hover { background: color-mix(in oklab, var(--accent) 90%, white 10%); }
.btn.ghost { background: transparent; border-color: var(--line); color: var(--text-2); }
.btn.ghost:hover { background: var(--hover); color: var(--text); }
.btn.danger { color: var(--bad); border-color: color-mix(in oklab, var(--bad) 45%, var(--line-2)); background: transparent; }
.btn.danger:hover { background: color-mix(in oklab, var(--bad) 12%, transparent); }
.btn.sm { height: var(--btn-h-sm); padding: 0 10px; font-size: 12px; }
.btn.icon { width: var(--btn-h); padding: 0; }
.btn.icon.sm { width: var(--btn-h-sm); }
.btn.is-disabled, .btn:disabled { opacity: 0.4; pointer-events: none; }
```

- [ ] **Step 3: Delete the old modifier twins.** Remove `.btn-primary`, `.btn-primary:hover`, `.btn-ghost`, `.btn-ghost:hover` rules (the dash-named ones, ~lines 267-280 in the original first block — folded into Step 2). Grep to be sure none remain:

```bash
grep -n '\.btn-primary\|\.btn-ghost' backend/app/static/app.css
```
Expected: no matches.

- [ ] **Step 4: Accent checkboxes (#7).** Add (near the existing `.row-check`/`.review-item-toggle` rules):

```css
.row-check, .ri-accept, input[type="checkbox"].review-check {
  accent-color: var(--accent);
  width: 15px; height: 15px; cursor: pointer;
}
```
And ensure `.review-item-toggle input[type="checkbox"]` does not override `accent-color` (leave its `cursor` rule; it inherits the accent).

- [ ] **Step 5: Dedupe conflicting rules.**
  - `.modal-body` is defined twice (~1218 and ~1898): read both, merge into the single intended definition at the first location, delete the second.
  - `.anno-draft-chip` is defined twice (~1316 and ~1352) with conflicting props: keep the intended one (the dashed-bottom chip at ~1352 is the live "draft chip"; the ~1316 left-border variant is stale — confirm by grepping templates for which visual is used), delete the other.
  - `.anno-status` (~1338): replace light-theme literals with tokens — `background: var(--info-bg, #eef4ff)` → `background: color-mix(in oklab, var(--accent) 12%, transparent)`; `border-left: 3px solid var(--accent, #4a8fff)` → `border-left: 3px solid var(--accent)`; the `.anno-status.error` colors → `var(--bad)`.

- [ ] **Step 6: Grep-guards**

```bash
grep -c '^\.btn \|^\.btn{' backend/app/static/app.css   # canonical base count
grep -n '#eef4ff\|#fdecec\|#4a8fff' backend/app/static/app.css   # expect: none
awk '/^\.modal-body[ {]/{c++} END{print "modal-body defs:", c}' backend/app/static/app.css  # expect 1
```

- [ ] **Step 7: Render smoke + commit**

Run: `python -m pytest tests/integration/test_routes_ui.py -q` → PASS (pages still render).
```bash
git add backend/app/static/app.css
git commit -m "refactor(css): single .btn system + tokens; accent checkboxes; dedupe modal-body/anno-draft-chip; retoken anno-status"
```

Satisfies part of spec flows #1, #10.

---

## Task 3: Form-field CSS (`.field`, `.field-label`, `.txt-area`)

**Files:** Modify `backend/app/static/app.css`

- [ ] **Step 1: Add the field component** (near the existing `.txt` / form rules):

```css
.field { display: flex; flex-direction: column; gap: 4px; }
.field-label {
  height: var(--field-label-h); display: flex; align-items: center;
  color: var(--text-2); font-size: 12px; font-weight: 500;
}
.field-help { color: var(--text-3); font-size: 11px; margin-top: 2px; }
.txt-area {
  width: 100%; min-height: 120px; resize: vertical;
  padding: 8px 10px; line-height: 1.5;
  background: var(--bg-2); color: var(--text);
  border: 1px solid var(--line); border-radius: var(--r-2);
  font-family: var(--f-mono); font-size: 13px; box-sizing: border-box;
}
.txt-area:focus { outline: none; border-color: var(--accent); }
.txt-area.json-editor { font-family: var(--f-mono); }
```

- [ ] **Step 2: Grep-guard + commit**

Run: `python -m pytest tests/integration/test_routes_ui.py -q` → PASS.
```bash
git add backend/app/static/app.css
git commit -m "feat(css): .field/.field-label/.txt-area form-field components"
```

---

## Task 4: Jinja macro library `components/_ui.html`

**Files:**
- Create: `backend/app/templates/components/_ui.html`
- Test: `tests/integration/test_ui_macros.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_ui_macros.py`:

```python
from jinja2 import Environment, FileSystemLoader
from pathlib import Path

TPL = Path(__file__).resolve().parents[2] / "backend" / "app" / "templates"

def _env():
    return Environment(loader=FileSystemLoader(str(TPL)), autoescape=True)

def test_button_renders_anchor_and_button():
    env = _env()
    t = env.from_string(
        "{% import 'components/_ui.html' as ui %}"
        "{{ ui.button('Save', variant='primary') }}|"
        "{{ ui.button('Go', href='/x', variant='ghost', size='sm') }}"
    )
    out = t.render()
    assert '<button' in out and 'class="btn primary"' in out and 'Save' in out
    assert '<a ' in out and 'href="/x"' in out and 'class="btn ghost sm"' in out

def test_textarea_field_passes_input_attrs():
    env = _env()
    t = env.from_string(
        "{% import 'components/_ui.html' as ui %}"
        "{{ ui.textarea_field('Body', 'body', value='hi', input_attrs='x-model=\"d.body\"') }}"
    )
    out = t.render()
    assert 'class="field-label"' in out and 'Body' in out
    assert 'class="txt-area' in out and 'x-model="d.body"' in out and '>hi</textarea>' in out
```

- [ ] **Step 2: Run it, verify it fails**

Run: `python -m pytest tests/integration/test_ui_macros.py -q`
Expected: FAIL (template `components/_ui.html` not found).

- [ ] **Step 3: Create `backend/app/templates/components/_ui.html`**

```jinja
{# Reusable UI components. See docs/design-language.md. #}

{% macro button(label, href=None, variant='', size='', type='button', attrs='') -%}
  {%- set cls = ('btn ' ~ variant ~ ' ' ~ size).split() | join(' ') -%}
  {%- if href -%}
    <a class="{{ cls }}" href="{{ href }}" {{ attrs|safe }}>{{ label }}</a>
  {%- else -%}
    <button type="{{ type }}" class="{{ cls }}" {{ attrs|safe }}>{{ label }}</button>
  {%- endif -%}
{%- endmacro %}

{% macro field(label, name, value='', type='text', help='', input_attrs='') -%}
  <label class="field">
    <span class="field-label">{{ label }}</span>
    <input class="txt" type="{{ type }}" name="{{ name }}" value="{{ value }}" {{ input_attrs|safe }}>
    {%- if help %}<span class="field-help">{{ help }}</span>{% endif -%}
  </label>
{%- endmacro %}

{% macro textarea_field(label, name, value='', help='', min_height='120px', input_attrs='') -%}
  <label class="field">
    <span class="field-label">{{ label }}</span>
    <textarea class="txt-area" name="{{ name }}" style="min-height: {{ min_height }};" {{ input_attrs|safe }}>{{ value }}</textarea>
    {%- if help %}<span class="field-help">{{ help|safe }}</span>{% endif -%}
  </label>
{%- endmacro %}

{% macro page_header(title, meta='') -%}
  <div class="page-hdr">
    <h1>{{ title }}</h1>
    {%- if meta %}<span class="meta">{{ meta }}</span>{% endif -%}
    <div class="grow"></div>
    {{ caller() if caller is defined else '' }}
  </div>
{%- endmacro %}

{% macro breadcrumb(segments) -%}
  <span class="crumb">
    {%- for seg in segments -%}
      {%- if not loop.first %}<span class="sep"> / </span>{% endif -%}
      {%- if seg[1] %}<a href="{{ seg[1] }}">{{ seg[0] }}</a>
      {%- else %}<span class="strong">{{ seg[0] }}</span>{% endif -%}
    {%- endfor -%}
  </span>
{%- endmacro %}

{% macro status_pill(label, state='') -%}
  <span class="pill {{ state }}"><span class="led"></span>{{ label }}</span>
{%- endmacro %}
```

- [ ] **Step 4: Add `.pill` alias** in `app.css` so the macro's class works (the existing styled element is `.env-pill`; alias the pill component):

```css
.pill { display: inline-flex; align-items: center; gap: 5px; height: 22px; padding: 0 8px;
  border-radius: 11px; border: 1px solid var(--line-2); font-family: var(--f-mono);
  font-size: 10.5px; letter-spacing: 0.04em; color: var(--text-2); text-transform: uppercase; }
.pill .led { width: 6px; height: 6px; border-radius: 50%; background: var(--text-3); }
.pill.ok { color: var(--good); border-color: color-mix(in oklab, var(--good) 35%, transparent); }
.pill.ok .led { background: var(--good); box-shadow: 0 0 6px var(--good); }
```

- [ ] **Step 5: Run tests, verify pass**

Run: `python -m pytest tests/integration/test_ui_macros.py -q` → PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/templates/components/_ui.html backend/app/static/app.css tests/integration/test_ui_macros.py
git commit -m "feat(ui): Jinja macro library (button/field/page_header/breadcrumb/status_pill)"
```

---

## Task 5: Migrate bespoke buttons onto `.btn`

**Files (from Task 0 enumeration — re-verify):**
- `templates/pages/_cache_row_cells.html`, `_cache_queue_table.html` (`ra-btn`→`btn sm`, `ra-btn-danger`→`btn sm danger`)
- `templates/pages/clip_detail.html` (`ca-btn`→`btn`, `ca-btn-primary`→`btn primary`, `ca-btn-danger`→`btn danger`; `tbtn`→`btn icon ghost`; `anno-scope-btn`→`btn sm`)
- `templates/pages/_annotate_dropdown.html` (`ca-btn`→`btn`)
- `templates/pages/_pager.html` (`pg-btn`→`btn sm`; keep its `.disabled` state class)
- `templates/cache_page.html`, `templates/pages/clips.html` (`bulk-btn`→`btn sm`, `bulk-btn-danger`→`btn sm danger`; `filter-reset`→`btn sm ghost`)
- `templates/pages/_studio_prompt_card.html` (`btn-compare`→`btn sm`, `btn-diff-toggle`→`btn sm`, `btn-close-cmp`→`btn sm icon`)
- `templates/pages/_studio_version_picker.html` (`pc-vchip-btn`→`btn sm ghost`)
- `templates/pages/_prompt_detail.html`, `_studio_prompt_card.html` (`model-picker-btn` on `.tag info` → `btn sm ghost`; keep the inner `.dot` LED)

- [ ] **Step 1: Apply the class-token substitutions** above, file by file. For each, read the file, replace only the class token(s) in the `class="..."` attributes (leave Alpine bindings/positioning intact). Where a feature class carried layout (e.g. pager arrows), keep a layout-only class alongside `.btn` (e.g. `class="btn sm pg-prev"`), but do **not** re-declare button look in CSS.

- [ ] **Step 2: Remove the now-dead CSS rules** in `app.css` for every migrated class: `.ra-btn*`, `.ca-btn*`, `.pg-btn*`, `.tbtn*`, `.bulk-btn*` (keep `.bulkbar` layout), `.anno-scope-btn*`, `.btn-compare/.btn-diff-toggle/.btn-close-cmp`, `.pc-vchip-btn`, `.filter-reset` (keep size via `.btn.sm`), `.model-picker-btn`. Keep any rule that is pure layout (positioning) and rename it to a layout-only selector if still referenced.

- [ ] **Step 3: Grep-guard — no bespoke button classes remain in templates**

```bash
grep -rn 'ra-btn\|ca-btn\|pg-btn\|\btbtn\b\|bulk-btn\|anno-scope-btn\|btn-compare\|btn-diff-toggle\|btn-close-cmp\|pc-vchip-btn\|filter-reset\|model-picker-btn' backend/app/templates
```
Expected: no matches. Then confirm `app.css` has no orphaned definitions of the removed look:
```bash
grep -n '\.ra-btn\|\.ca-btn\|\.pg-btn\|\.tbtn\|\.bulk-btn\b\|\.anno-scope-btn\|\.btn-compare\|\.btn-diff-toggle\|\.btn-close-cmp\|\.pc-vchip-btn\|\.filter-reset\|\.model-picker-btn' backend/app/static/app.css
```
Expected: only layout-only survivors you intentionally kept.

- [ ] **Step 4: Render smoke across pages**

Run: `python -m pytest tests/integration/test_routes_ui.py tests/integration/test_studio_page.py tests/integration/test_clip_detail_draft.py -q` → PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/templates backend/app/static/app.css
git commit -m "refactor(ui): migrate bespoke button classes onto canonical .btn"
```

Satisfies spec flows #1, #2 (bad button).

---

## Task 6: Prompt forms — shared field macros + autosize (#1 bad button, #2 fields)

**Files:** Modify `templates/pages/_prompt_new.html`, `templates/pages/_prompt_detail.html`
- Test: `tests/integration/test_routes_pages_prompt_create.py` (extend)

- [ ] **Step 1: Failing test — new-prompt form uses field components, not the inline label hack**

Add to `tests/integration/test_routes_pages_prompt_create.py`:

```python
def test_new_prompt_form_uses_field_components(client):
    r = client.get("/prompts/new")
    assert r.status_code == 200
    assert 'class="panel-h" style="padding: 0; background: transparent' not in r.text
    assert 'class="txt-area' in r.text and 'class="field-label"' in r.text
```

(Use the test client fixture already used in this file.)

- [ ] **Step 2: Run it, verify it fails**

Run: `python -m pytest tests/integration/test_routes_pages_prompt_create.py -q`
Expected: FAIL (inline hack still present / `txt-area` absent).

- [ ] **Step 3: Rewrite `_prompt_new.html` fields using the macros**

At top: `{% import "components/_ui.html" as ui %}`. Replace each `<label>…panel-h…<input/textarea>…</label>` block:
- Name → `{{ ui.field('Name', 'name', value=form.name, input_attrs='required') }}`
- Description → `{{ ui.field('Description', 'description', value=form.description) }}`
- Prompt body → `{{ ui.textarea_field('Prompt', 'body', value=form.body, min_height='130px') }}`
- target_map → `{{ ui.textarea_field('target_map (JSON)', 'target_map', value=form.target_map_text, min_height='100px', input_attrs='class=\"txt-area json-editor\"') }}`
- output_schema → `{{ ui.textarea_field('output_schema (JSON)', 'output_schema', value=form.output_schema_text, min_height='140px', input_attrs='class=\"txt-area json-editor\"') }}`
- Replace the form's `max-width: 800px` inline style with full width (drop the cap; let it fill the page-body).
- Model / Applies-to selects: keep `<select class="txt">` but wrap each in `<label class="field"><span class="field-label">…</span>…</label>`.
- Submit/Cancel buttons → `{{ ui.button('Create prompt', variant='primary', type='submit') }}` and `{{ ui.button('Cancel', href='/prompts', variant='ghost') }}`.

- [ ] **Step 4: Update `_prompt_detail.html` editor fields** (Alpine editor) to the same components, passing bindings via `input_attrs`:
- Prompt body → `{{ ui.textarea_field('Prompt', 'body', input_attrs='x-model=\"draft.body\" :readonly=\"!canEdit\"') }}`
- target_map → `{{ ui.textarea_field('target_map', 'target_map', input_attrs='class=\"txt-area json-editor\" x-model=\"draft.target_map_text\" :readonly=\"!canEdit\"') }}`
- output_schema → likewise with `x-model="draft.output_schema_text"`.
- Remove the per-field inline `style="width:100%; min-height…"` and the `panel-h` label hack (now `.field-label`).
- The model-picker button already migrated in Task 5; verify it renders inside the header row.

- [ ] **Step 5: Run tests, verify pass + guard**

Run: `python -m pytest tests/integration/test_routes_pages_prompt_create.py -q` → PASS.
```bash
grep -rn 'class="panel-h" style="padding: 0; background: transparent' backend/app/templates
```
Expected: no matches.

- [ ] **Step 6: Commit**

```bash
git add backend/app/templates/pages/_prompt_new.html backend/app/templates/pages/_prompt_detail.html tests/integration/test_routes_pages_prompt_create.py
git commit -m "refactor(prompts): render forms via shared field macros; autosizing full-width textareas"
```

Satisfies spec flow #3 (prompt editing).

---

## Task 7: Consistent header + breadcrumb (#3)

**Files:** Modify `templates/pages/layout.html` (no structural change — slot already exists), `templates/pages/prompts.html`, `templates/pages/_prompt_new.html`, `templates/studio.html`, `templates/cache_page.html`, and confirm `clips.html`/`clip_detail.html` crumbs.
- Test: `tests/integration/test_routes_ui.py` (extend)

- [ ] **Step 1: Failing test — every top-level page fills the crumb; cache shows its title once**

Add to `tests/integration/test_routes_ui.py`:

```python
def test_pages_have_breadcrumb_and_single_title(client):
    for path, crumb in [("/prompts", "Prompts"), ("/cache", "Cache")]:
        r = client.get(path)
        assert r.status_code == 200
        assert 'class="crumb"' in r.text  # top-bar context present
    cache = client.get("/cache").text
    # "Cache" appears as the breadcrumb leaf, not duplicated as a body <h1>
    assert cache.count(">Cache<") <= 1
```

- [ ] **Step 2: Run it, verify it fails**

Run: `python -m pytest tests/integration/test_routes_ui.py -q`
Expected: FAIL (prompts has no crumb; cache duplicates title).

- [ ] **Step 3: Add `{% block crumb %}` to pages that lack it**, using the macro. In each page template add at top `{% import "components/_ui.html" as ui %}` and a crumb block:
- `prompts.html`: `{% block crumb %}{{ ui.breadcrumb([('Prompts', None)]) }}{% endblock %}`
- `_prompt_new.html`: `{{ ui.breadcrumb([('Prompts', '/prompts'), ('New', None)]) }}`
- `studio.html`: `{{ ui.breadcrumb([('Studio', None)]) }}`
- `clips.html`/`clip_detail.html`: keep existing crumbs but route them through `ui.breadcrumb(...)` for consistency (catalog name as leaf).

- [ ] **Step 4: Fix the cache double-title.** In `cache_page.html`, change the crumb to a path (`{{ ui.breadcrumb([('System', None), ('Cache', None)]) }}`) and **remove the body `<h1>Cache</h1>`** from the `page-hdr` (keep the `.meta` + Refresh action), OR keep the body `page_header` and reduce the crumb to `('System', None)`. Choose the former (breadcrumb carries the name; body header keeps actions/meta only). Ensure exactly one visible "Cache".

- [ ] **Step 5: Run tests + commit**

Run: `python -m pytest tests/integration/test_routes_ui.py -q` → PASS.
```bash
git add backend/app/templates
git commit -m "feat(ui): consistent top-bar breadcrumb via macro; fix cache double title"
```

Satisfies spec flow #4.

---

## Task 8: Cache page tweaks (#4 relabel, #5 remove raw bytes)

**Files:** Modify `templates/cache_page.html`
- Test: `tests/integration/test_routes_ui.py` (extend)

- [ ] **Step 1: Failing test**

```python
def test_cache_metric_labels(client):
    r = client.get("/cache").text
    assert "GB cap" in r            # cap is labelled
    assert "| comma }} B" not in r  # template guard (no raw-byte expression)
```

(The second assert is a literal guard; better: assert the rendered AI-store metric has no trailing raw byte count — see Step 3.)

- [ ] **Step 2: Run it, verify it fails**

Run: `python -m pytest tests/integration/test_routes_ui.py -q`
Expected: FAIL.

- [ ] **Step 3: Edit `cache_page.html`:**
- Line ~30: `<div class="m-sub">of {{ summary.media_cache_cap_bytes | bytes_human }}</div>` → `<div class="m-sub">of {{ summary.media_cache_cap_bytes | bytes_human }} cap</div>`.
- Lines ~49-51 (AI store `m-foot`): delete the `<span class="muted-2">{{ summary.total_ai_bytes | comma }} B</span>` and its leading `·` so only `<b>{{ ai_total_count }}</b> objects` remains.

- [ ] **Step 4: Run tests + commit**

Run: `python -m pytest tests/integration/test_routes_ui.py -q` → PASS.
```bash
git add backend/app/templates/cache_page.html tests/integration/test_routes_ui.py
git commit -m "fix(cache): label media cap; drop redundant raw-byte line"
```

Satisfies spec flows #5, #6.

---

## Task 9: Review-mode editor — read-only rows + inline-expand Edit (#6)

**Files:** Modify `templates/pages/_anno_panels.html`; Alpine state in `backend/app/static/clipAnnotate.js` (the `reviewQueue`/panel component) and/or `player.js` root (`editingItemId`).
- Test: `tests/integration/test_anno_panels_flag.py` (extend) or `tests/integration/test_clip_detail_draft.py`

- [ ] **Step 1: Failing test — review items render read-only by default (no always-on inputs)**

Add to `tests/integration/test_anno_panels_flag.py` (it already renders `_anno_panels.html` with a review flag — reuse its fixture):

```python
def test_review_items_are_readonly_until_edit():
    html = render_anno_panels(review_mode=True)  # use the helper this test file already defines
    # editor inputs are NOT rendered inline by default
    assert 'class="ri-mfield"' not in html
    # each item exposes an Edit toggle and the accept checkbox keeps the accent class
    assert 'data-edit-item' in html
    assert 'class="ri-accept' in html
```

(If the file lacks a render helper, add one that loads the template with a minimal `panels` dict containing a marker with `item_id`.)

- [ ] **Step 2: Run it, verify it fails**

Run: `python -m pytest tests/integration/test_anno_panels_flag.py -q`
Expected: FAIL (`ri-mfield` present; no `data-edit-item`).

- [ ] **Step 3: Rewrite the `.ri-marker` / field / note review blocks** in `_anno_panels.html` to the Option-A pattern. For a marker (replace lines ~53-72):

```jinja
{% if m.item_id is defined and m.item_id is not none %}
  <div class="ri-row" data-item-id="{{ m.item_id }}" onclick="event.stopPropagation()"
       :class="{ editing: editingItemId === {{ m.item_id }} }">
    <div class="ri-readonly">
      <label class="review-item-toggle">
        <input type="checkbox" class="ri-accept" data-item-id="{{ m.item_id }}"
               {% if m.decision != 'rejected' %}checked{% endif %}>
      </label>
      <span class="ri-text"><b>{{ m.name }}</b>{% if m.category %} · {{ m.category }}{% endif %}</span>
      <span class="ri-tc mono">{{ smpte(m.in_secs, panels.fps or clip.fps) }}{% if m.out_secs is not none %} – {{ smpte(m.out_secs, panels.fps or clip.fps) }}{% endif %}</span>
      <button type="button" class="btn sm ghost" data-edit-item="{{ m.item_id }}"
              @click="editingItemId = (editingItemId === {{ m.item_id }} ? null : {{ m.item_id }})"
              x-text="editingItemId === {{ m.item_id }} ? 'Done' : '✎ Edit'"></button>
    </div>
    <div class="ri-editor" x-show="editingItemId === {{ m.item_id }}" x-cloak>
      <label class="field"><span class="field-label">Name</span>
        <input class="txt ri-mfield" data-item-id="{{ m.item_id }}" data-k="name" value="{{ m.name }}"></label>
      <label class="field"><span class="field-label">Category</span>
        <input class="txt ri-mfield" data-item-id="{{ m.item_id }}" data-k="category" value="{{ m.category or '' }}"></label>
      <label class="field"><span class="field-label">Description</span>
        <textarea class="txt-area ri-mfield" data-item-id="{{ m.item_id }}" data-k="description">{{ m.description or '' }}</textarea></label>
      <div class="ri-time mono">
        in <span x-text="riReadout({{ m.item_id }}, 'in', {{ m.in_secs }})">{{ smpte(m.in_secs, panels.fps or clip.fps) }}</span>
        out <span x-text="riReadout({{ m.item_id }}, 'out', {{ m.out_secs if m.out_secs is not none else 'null' }})"></span>
        <span class="muted">drag on timeline · ←/→ nudge</span>
      </div>
    </div>
  </div>
{% else %}
  <h3 class="m-name">{{ m.name }}</h3>
  {% if m.description %}<p class="m-desc">{{ m.description }}</p>{% endif %}
{% endif %}
```

Apply the same read-only-row → inline-editor pattern to the **fields** block (replace the always-on `.ri-edit` input: read-only `ident → value` + Edit button revealing a `field` editor) and the **notes** block (read-only text + Edit revealing a `.txt-area`). The accept checkbox stays in the read-only row.

- [ ] **Step 4: Add the Alpine state.** In the player root component (`player.js` factory return object) add `editingItemId: null,` and a `riReadout(id, edge, secs)` helper that returns the SMPTE string for the current (possibly dragged) value. The panel (child `reviewQueue` scope) reads/writes `editingItemId` on the ancestor — verify by reading `clipAnnotate.js` / wherever `reviewQueue` is defined; if `editingItemId` must live on the child, hoist it to the root so the timeline (Task 10) can read it.

- [ ] **Step 5: Add editor CSS** in `app.css`:

```css
.ri-readonly { display: flex; align-items: center; gap: 8px; }
.ri-readonly .ri-text { flex: 1; min-width: 0; }
.ri-readonly .ri-tc { color: var(--text-3); font-size: 11px; }
.ri-editor { display: flex; flex-direction: column; gap: 8px; margin-top: 8px;
  padding: 10px; border: 1px solid var(--accent); border-radius: var(--r-2); }
.ri-row.editing { outline: 1px solid color-mix(in oklab, var(--accent) 40%, transparent); border-radius: var(--r-2); }
```
Remove the old `.ri-marker`/`.ri-mfield` width hacks superseded by `.field`/`.txt`/`.txt-area` (keep `.ri-mfield` only as a JS hook selector — strip its visual CSS).

- [ ] **Step 6: Run tests, verify pass + commit**

Run: `python -m pytest tests/integration/test_anno_panels_flag.py tests/integration/test_clip_detail_draft.py -q` → PASS.
```bash
git add backend/app/templates/pages/_anno_panels.html backend/app/static/app.css backend/app/static/player.js backend/app/static/clipAnnotate.js tests/integration/test_anno_panels_flag.py
git commit -m "feat(review): edit-gated inline editor (read-only rows + expand) with field components"
```

Satisfies spec flows #9, #10.

---

## Task 10: Timeline drag-to-adjust markers (#8)

**Files:** Modify `templates/pages/_player_overlay.html` (tag draft ranges with `item_id`, add handles + pointer bindings), `backend/app/static/player.js` (drag controller + nudge + `riReadout`), `backend/app/static/app.css` (handle styles), `templates/pages/_anno_panels.html` (spinners already removed in Task 9).

- [ ] **Step 1: Tag draft ranges with their item id.** The overlay builds ranges from `row.ranges`. Ensure the caller (`clip_detail.html`) includes `item_id` on each draft marker dict, then in `_player_overlay.html` add to the draft `.range` div:

```jinja
{% if 'range-draft' in row.cls and m.item_id is defined %}
  data-item-id="{{ m.item_id }}"
  :class="{ editing: editingItemId === {{ m.item_id }} }"
  @pointerdown="startMarkerDrag($event, {{ m.item_id }}, 'move')"
{% endif %}
```

and inside that draft range, conditionally render edge handles shown only when editing:

```jinja
{% if 'range-draft' in row.cls and m.item_id is defined %}
  <span class="range-handle in"  x-show="editingItemId === {{ m.item_id }}" @pointerdown.stop="startMarkerDrag($event, {{ m.item_id }}, 'in')"></span>
  <span class="range-handle out" x-show="editingItemId === {{ m.item_id }}" @pointerdown.stop="startMarkerDrag($event, {{ m.item_id }}, 'out')"></span>
{% endif %}
```

- [ ] **Step 2: Add the drag controller** to the `player(...)` factory in `player.js`:

```js
    editingItemId: null,
    _drag: null,

    _timelineEl() { return this.$root.querySelector('.timeline'); },
    _xToSecs(clientX) {
      const r = this._timelineEl().getBoundingClientRect();
      const frac = Math.max(0, Math.min(1, (clientX - r.left) / r.width));
      return frac * this.duration;
    },
    _draftItem(id) { return this.draftMarkers.find(m => m.item_id === id); },

    startMarkerDrag(e, id, mode) {
      e.preventDefault(); e.stopPropagation();
      this.editingItemId = id;
      const m = this._draftItem(id);
      if (!m) return;
      this._drag = { id, mode, startX: e.clientX, t0: this._xToSecs(e.clientX),
                     in0: m.in_secs, out0: m.out_secs };
      e.target.setPointerCapture?.(e.pointerId);
      const move = (ev) => this._onMarkerDrag(ev);
      const up = (ev) => { this._endMarkerDrag(ev); window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up); };
      window.addEventListener('pointermove', move);
      window.addEventListener('pointerup', up);
    },
    _onMarkerDrag(e) {
      const d = this._drag; if (!d) return;
      const m = this._draftItem(d.id); if (!m) return;
      const dt = this._xToSecs(e.clientX) - d.t0;
      const dur = this.duration;
      if (d.mode === 'move') {
        const len = (d.out0 ?? d.in0) - d.in0;
        m.in_secs = Math.max(0, Math.min(d.in0 + dt, dur - len));
        if (d.out0 != null) m.out_secs = m.in_secs + len;
      } else if (d.mode === 'in') {
        m.in_secs = Math.max(0, Math.min(d.in0 + dt, (m.out_secs ?? dur)));
      } else if (d.mode === 'out') {
        m.out_secs = Math.min(dur, Math.max(d.out0 + dt, m.in_secs));
      }
    },
    _endMarkerDrag() {
      const d = this._drag; this._drag = null;
      if (d) this._persistMarker(d.id);
    },
    nudgeMarker(dir, fine) {
      if (this.editingItemId == null) return;
      const m = this._draftItem(this.editingItemId); if (!m) return;
      const step = fine ? (1 / (this.fps || 25)) : 1.0;  // Shift = 1 frame, else 1s
      m.in_secs = Math.max(0, m.in_secs + dir * step);
      if (m.out_secs != null) m.out_secs = Math.max(m.in_secs, m.out_secs + dir * step);
      this._persistMarker(this.editingItemId);
    },
    riReadout(id, edge, fallback) {
      const m = this._draftItem(id);
      const v = m ? (edge === 'in' ? m.in_secs : m.out_secs) : fallback;
      return v == null ? '—' : this.smpteSecs(v);
    },
    _persistMarker(id) {
      const m = this._draftItem(id); if (!m) return;
      fetch(`/api/review/items/${id}/decision`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ decision: 'accepted',
          edited_value: { in_secs: m.in_secs, out_secs: m.out_secs } }),
      });
    },
```

If a SMPTE-from-seconds helper does not already exist on the component, add `smpteSecs(secs)` using `fmtTimecode` plus frames, or reuse the existing `tc(secs)` method (check player.js — it has `tc`). Use whichever the component already exposes; do not duplicate.

- [ ] **Step 3: Keyboard nudge.** In the existing keydown handler in `player.js` (it already handles arrows for seek at lines ~159-161), branch: if `this.editingItemId != null`, ArrowLeft/Right call `this.nudgeMarker(-1/ +1, e.shiftKey)` and `preventDefault()` (do not also seek). Otherwise keep current seek behavior.

- [ ] **Step 4: Handle + editing CSS** in `app.css`:

```css
.range-handle { position: absolute; top: 0; bottom: 0; width: 6px; background: var(--accent);
  border-radius: 2px; cursor: ew-resize; }
.range-handle.in { left: -3px; } .range-handle.out { right: -3px; }
.ranges .range.draft-range.editing { background: color-mix(in oklab, var(--accent) 45%, transparent);
  border: 1px solid var(--accent); box-shadow: 0 0 8px color-mix(in oklab, var(--accent) 50%, transparent); cursor: grab; }
```

- [ ] **Step 5: Grep-guard — no number spinners remain**

```bash
grep -rn 'type="number"' backend/app/templates/pages/_anno_panels.html
```
Expected: no matches.

- [ ] **Step 6: Render smoke + commit**

Run: `python -m pytest tests/integration/test_player_overlay_partial.py tests/integration/test_clip_detail_draft.py -q` → PASS.
```bash
git add backend/app/templates/pages/_player_overlay.html backend/app/templates/pages/clip_detail.html backend/app/static/player.js backend/app/static/app.css
git commit -m "feat(review): drag-to-adjust markers on timeline (edit-activated) + keyboard nudge"
```

Satisfies spec flow #11. **Note:** verify the `decision` endpoint accepts an `edited_value` object with `in_secs`/`out_secs` for markers; if it expects a different shape, adapt `_persistMarker` to match `routes/review.py` (read it first).

---

## Task 11: Agent-facing docs + CLAUDE.md pointer

**Files:** Create `docs/design-language.md`; Modify `CLAUDE.md`

- [ ] **Step 1: Write `docs/design-language.md`** — the catalog. Sections, each with a copy-paste example:
  1. **Tokens** — list the `:root` tokens (colors, radii, `--btn-h*`, `--field-*`) and "use tokens, never hex literals."
  2. **Buttons** — `.btn` + `.primary/.ghost/.danger/.sm/.icon`; show `{{ ui.button(...) }}`; "do not create `*-btn` classes."
  3. **Form fields** — `.field/.field-label/.txt/.txt-area`; `{{ ui.field(...) }}` / `{{ ui.textarea_field(...) }}`; "no inline label hacks."
  4. **Page header / breadcrumb** — `{{ ui.page_header(...) }}` + `{{ ui.breadcrumb([...]) }}`; the top-bar = path, body = title rule.
  5. **Status pill** — `{{ ui.status_pill(...) }}`; "pills are status, not actions."
  6. **JS helpers** — `window.fmtTimecode/fmtBytes/autosize`; "never re-implement timecode/byte formatting."
  7. **Red flags** (mirror CLAUDE.md tone): writing a `<button class="x-btn">`, a `style="..."` on a form field, a `mm:ss` `padStart`, a raw `1024` byte loop → stop and reuse.

- [ ] **Step 2: Extend `CLAUDE.md`** "Frontend: explore before implementing" — add a paragraph pointing to `docs/design-language.md` and `backend/app/templates/components/_ui.html`, naming the canonical components and the "reuse the macro/class, don't hand-roll" rule.

- [ ] **Step 3: Commit**

```bash
git add docs/design-language.md CLAUDE.md
git commit -m "docs: design-language catalog + CLAUDE.md frontend pointer"
```

Satisfies spec flow #8.

---

## Final pass: manual acceptance

Start the dev server once (CLAUDE.md seat discipline; check 8765 first, `kill -TERM` after), then walk **all 11 manual acceptance flows** in the spec's "Manual acceptance flows" section. Record pass/fail per flow. Stop the server gracefully and confirm the seat-release log lines.

Then request code review (superpowers:requesting-code-review) before merge.
