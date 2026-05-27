# UI Design-Language Consolidation

**Date:** 2026-05-27
**Status:** Approved (design)

## Problem

The frontend has accreted duplicated and parallel-evolved UI code. The
same visual element is implemented many different ways, so a change to
"how a button looks" means editing ~10 places, and new features tend to
hand-roll yet another variant. Concretely, what an audit of
`backend/app/static/app.css` + `backend/app/templates/` found:

- **Buttons**: the base `.btn` rule is defined **twice** (`app.css:256`
  and `:825`) with conflicting size rules, and there are two naming
  conventions for the same look (`.btn-primary`/`.btn.primary`,
  `.btn-ghost`/`.btn.ghost`). On top of that, ~10 bespoke per-feature
  button classes exist (`ra-btn`, `ca-btn`, `pg-btn`, `tbtn`,
  `bulk-btn`, `anno-scope-btn`, `btn-compare`, `pc-vchip-btn`,
  `filter-reset`, plus `model-picker-btn` built awkwardly on `.tag`).
- **Form fields**: `_prompt_new.html` and `_prompt_detail.html` are
  parallel renderings of the *same* prompt fields. The label hack
  `class="panel-h" style="padding:0;background:transparent;border:0;height:24px"`
  is repeated **10×**, and textarea styling
  (`width:100%;min-height;resize:vertical;font-family:mono`) is
  copy-pasted per field. The prompt editor textareas are also too
  narrow / fixed-height and hard to edit.
- **Page header / breadcrumb**: the top-bar slot next to the brand
  (`{% block crumb %}`) is filled inconsistently — clips shows the
  catalog name, cache shows `System / Cache management` **and** repeats
  the title in the body, prompts shows nothing.
- **Cache page**: the "of 50 GB" figure is unexplained (it is a
  configured cap, not measured disk), and a redundant raw-byte line is
  printed under the already-humanized value.
- **Duplicated CSS rules**: `.modal-body` (`:1218`/`:1898`) and
  `.anno-draft-chip` (`:1316`/`:1352`, conflicting) are each defined
  twice. `.anno-status` (`:1338`) carries stray light-theme fallback
  colors (`#eef4ff`, `#fdecec`, `--accent:#4a8fff`) that do not match
  this app's dark theme — a copy-paste from elsewhere.
- **Duplicated JS**: `mm:ss` timecode formatting is reimplemented three
  times (`player.js:103`, `studio.js:93`, `liveSession.js:225`), and
  `cache_page.html:147` reimplements the server-side `bytes_human`
  Jinja filter as a JS `bytesHuman`.

## Goals

Establish a **small, reusable UI library** and migrate existing code
onto it, so that (a) the duplicated code is deleted, (b) the visuals are
consistent, and (c) **future agents reuse the library instead of
re-creating components**. The library has four thin layers, all within
the project's no-Node / no-build constraint (see ADR 0001):

1. **Design tokens** — extend `app.css :root` with the few missing
   component tokens (`--btn-h`, `--btn-h-sm`, field heights) so sizes
   stop being magic numbers. No new color system; existing color/radii/
   font tokens are kept.
2. **Canonical CSS component classes** — one definition each, replacing
   the duplicates and bespoke variants.
3. **Jinja macros** (`templates/components/_ui.html`) — the reuse
   primitive; call sites collapse to one line.
4. **Shared JS helpers** (`static/format.js`) — one implementation of
   timecode, byte-humanization, and textarea autosize.

This is **Phase 1**. A future "Phase 2" formal design-token expansion
(documented earlier as Option C) is explicitly out of scope here.

## Non-goals

- No JS framework, bundler, SCSS, or any Node toolchain (ADR 0001).
- No new color palette or visual redesign — same look, consolidated
  implementation.
- No backend/API changes beyond the cache-page template and label
  tweaks for issues #4/#5.
- No UI editor for the cache cap (see Decisions).
- The status-pill family (`.env-pill`, connection chip, shutdown
  button) stays a **separate, intentional** component — it is a status
  indicator, not an action button.

## Design

### Layer 1 — Tokens (`app.css :root`)

Add component-size tokens alongside the existing ones:

```css
--btn-h: 32px;          /* default action button height */
--btn-h-sm: 26px;       /* compact (row actions, pagers, toolbars) */
--field-h: 32px;        /* single-line inputs/selects */
--field-label-h: 24px;  /* the former panel-h label height */
```

### Layer 2 — Canonical CSS classes

**`.btn` (single definition) + modifiers.** Delete the second `.btn`
block and the `.btn-primary`/`.btn-ghost` twins. One base plus:

- `.btn.primary` — accent fill (the production/CTA button)
- `.btn.ghost` — transparent/secondary
- `.btn.danger` — destructive (purge, archive)
- `.btn.sm` — compact size (uses `--btn-h-sm`)
- `.btn.icon` — square icon-only (kebab, close)

The bespoke classes migrate to these:

| Old | New |
|---|---|
| `ra-btn`, `ra-btn-danger` | `.btn.sm`, `.btn.sm.danger` |
| `ca-btn`, `ca-btn-primary`, `ca-btn-danger` | `.btn`, `.btn.primary`, `.btn.danger` |
| `pg-btn` | `.btn.sm` (pager keeps `.disabled` state class) |
| `tbtn` | `.btn.icon.ghost` |
| `bulk-btn`, `bulk-btn-danger` | `.btn.sm`, `.btn.sm.danger` |
| `anno-scope-btn` | `.btn.sm` |
| `btn-compare`, `btn-diff-toggle`, `btn-close-cmp` | `.btn.sm` (+ `.icon` for close) |
| `pc-vchip-btn` | `.btn.sm.ghost` |
| `filter-reset` | `.btn.sm.ghost` |
| `model-picker-btn` (on `.tag info`) | `.btn.sm.ghost` with inline LED `.dot` |

Where a migrated button has feature-specific positioning, keep a thin
feature class for *layout only* (e.g. `.pager .btn`), never re-declaring
the button's look.

**Form field classes.** `.field` (wrapper), `.field-label` (replaces the
inline `panel-h` hack), `.txt` (single-line input/select, already
exists — keep), `.txt-area` (multiline: `width:100%`, sane
`min-height`, `resize:vertical`, autosized). These replace all per-field
inline styles in the prompt forms.

**`.page-hdr`** is formalized as *the* page title row: title + optional
`.meta` + right-aligned actions. Already exists; this spec makes it the
only pattern and removes the cache page's duplicate title.

**`.pill`** documents the existing status-indicator pill (the LED
variant) as a first-class, separate component.

**Dedupe/bug fixes:** merge the two `.modal-body` rules into one;
resolve the two conflicting `.anno-draft-chip` rules into a single
intended definition; replace `.anno-status`'s light-theme literals with
the dark-theme tokens (`--accent`, `--bad`, `--surface`).

### Layer 3 — Jinja macros (`templates/components/_ui.html`)

```jinja
{% macro button(label, href=None, variant='', size='', type='button', attrs='') %}
{% macro field(label, name, value='', type='text', help='', input_attrs='') %}
{% macro textarea_field(label, name, value='', help='', min_height='130px', input_attrs='') %}
{% macro page_header(title, meta='') %}{# actions via {% call %} body #}{% endmacro %}
{% macro breadcrumb(segments) %}{# segments = list of (label, href|None) #}
{% macro status_pill(label, state='') %}
```

- `button()` renders `<a>` when `href` is given, else `<button>`;
  `variant`/`size` map to the `.btn` modifiers; `attrs` passes raw
  attributes through (so Alpine `@click`, `:disabled`, `x-show` work).
- `field()`/`textarea_field()` accept `input_attrs` so Alpine bindings
  (`x-model`, `:readonly`) pass straight through — this lets the
  Alpine-driven `_prompt_detail.html` editor and the plain-POST
  `_prompt_new.html` form share the **same** field markup.
- `page_header()` uses the `{% call %}` pattern for the actions slot so
  action buttons (with their Alpine bindings) stay in the page.
- Deeply Alpine-bound bespoke widgets (model-picker menu, version
  picker) keep literal HTML but adopt the canonical `.btn`/`.tag`
  classes — macros are used where they remove duplication, not forced
  where they would fight Alpine.

### Layer 4 — Shared JS (`static/format.js`, loaded first in `layout.html`)

```js
window.fmtTimecode = (seconds) => …;   // replaces player.js/studio.js/liveSession.js copies
window.fmtBytes    = (n) => …;          // replaces cache_page.html bytesHuman
window.autosize    = (textarea) => …;   // grows .txt-area to content (issue #2)
```

`format.js` is added to the `<script defer>` block in `layout.html`
before the feature scripts, and the three timecode reimplementations +
the cache `bytesHuman` are replaced with calls to it.

### Issue resolution map

- **#1 Buttons** → Layer 2 `.btn` system; the "badly created" prompts
  button (`model-picker-btn` on `.tag info`) is normalized to
  `.btn.sm.ghost` + `.dot`.
- **#2 Text boxes** → `.txt-area` (full width) + `autosize()`; the
  prompt editor pane is given room. Same change de-dupes the two prompt
  forms via the shared `field`/`textarea_field` macros.
- **#3 Header** → Option A: `breadcrumb()` in the top-bar slot on every
  page (consistent `Section / Context` shape), `page_header()` in the
  body; cache no longer prints its title twice.
- **#4 Cache "50 GB"** → relabel the metric so it reads as a cap (e.g.
  "of 50 GB cap") and document in `design-language.md` /
  inline that it is `settings.media_cache_cap_gb`. No UI editor.
- **#5 Bytes** → delete the redundant raw-byte line at
  `cache_page.html:50`.

### Agent guidance (the durable artifact)

- **New `docs/design-language.md`** — the catalog: tokens, CSS
  component classes, Jinja macros, and JS helpers, each with a
  copy-paste example, a "**use these — do not hand-roll buttons,
  fields, headers, or timecode/byte formatting**" rule, and a red-flags
  list mirroring the existing CLAUDE.md style.
- **Extend `CLAUDE.md`** "Frontend: explore before implementing" with a
  pointer to `docs/design-language.md` and the `components/_ui.html`
  macro library, so the reuse-first instruction names the concrete
  components.

## Alternatives

- **CSS-only vocabulary** (consolidate classes, keep raw HTML): rejected
  — leaves the template duplication (10× label hack, parallel prompt
  forms) in place; relies on discipline only.
- **Macro-heavy library** (everything a macro): rejected — Jinja macros
  fight Alpine where components need live `x-model`/`:class` bindings.
- **Hybrid (chosen)**: canonical CSS classes for the visual layer +
  macros for the genuinely-duplicated structural pieces + shared JS
  helpers. Maximum dedup, no macro/Alpine friction, fits no-build
  constraint.

## Decisions

- **#4 cache cap is not UI-editable.** It stays configured via
  `settings.media_cache_cap_gb`; we only relabel + document. Revisit if
  an in-app editor is later wanted.
- **Status pills are not buttons.** The LED pill family is documented as
  a separate component; the answer to "is the lit button the same
  element as a button?" is *no, by design*.
- **Phase 2 (formal token expansion / Option C) deferred.**

## Risks & sequencing

This rewrites `app.css` and many templates — high collision risk with
concurrent work on `feat/draft-review-accept`. **All implementation
happens in an isolated git worktree on a dedicated branch**; nothing
touches in-flight files until a deliberate merge. The CatDV license seat
is shared — UI work needs the dev server only for manual acceptance, and
must follow the single-instance + graceful-shutdown discipline in
CLAUDE.md.

Suggested implementation order (kept reviewable):

1. `format.js` + replace the 3× timecode and cache `bytesHuman` (pure,
   isolated, testable).
2. Tokens + canonical `.btn` and form-field CSS; delete dup/conflicting
   rules (`.btn`, `.modal-body`, `.anno-draft-chip`); retoken
   `.anno-status`.
3. `components/_ui.html` macros (`button`, `field`, `textarea_field`,
   `page_header`, `breadcrumb`, `status_pill`).
4. Migrate templates onto the macros/classes, starting with the prompt
   forms (#1 bad button, #2 fields) and the header/breadcrumb (#3).
5. Cache page tweaks (#4 relabel, #5 remove raw bytes).
6. `docs/design-language.md` + `CLAUDE.md` pointer.

## Manual acceptance flows

A colleague who didn't write the code can run these on the app and tick
them off.

1. **Buttons are one element.** Open `/prompts`, `/cache`, `/`
   (clips), and `/studio`. Every action button (New prompt, Refresh,
   pager arrows, row Re-fetch/Purge, bulk Purge, studio Run/Compare)
   shares the same shape/radius/hover. Grep confirms: only `.btn` (+
   modifiers) in templates; `ra-btn`/`ca-btn`/`pg-btn`/`tbtn`/etc. no
   longer appear. `app.css` has exactly one `.btn` definition.
2. **Prompts page bad button fixed.** On `/prompts`, select a prompt;
   the model-picker button in the detail header matches the other
   buttons (no longer a mis-styled tag) and still opens the model menu.
3. **Prompt editing is comfortable (#2).** Open a prompt and a new
   prompt (`/prompts/new`). The Name/Description inputs and the Prompt /
   target_map / output_schema textareas are full-width within their
   pane and grow as you type past the initial height. Both pages render
   their fields from the shared `field`/`textarea_field` macros (no
   inline `panel-h` label hack remains).
4. **Consistent header (#3).** On every page the top-bar slot next to
   "CatDV Annotator" shows a `Section / Context` breadcrumb; the body
   shows the page title once. The cache page no longer shows "Cache" in
   both the top bar and the body.
5. **Cache cap explained (#4).** On `/cache` the Local-cache metric
   reads as a cap (e.g. "X of 50 GB cap"); the meaning is documented.
6. **No raw bytes (#5).** On `/cache` the AI-store metric shows only the
   humanized size (e.g. "11.8 MB") with no trailing raw-byte count.
7. **Timecode/bytes still correct.** Player and studio timestamps and
   the cache live-selection byte total still display correctly — now via
   `format.js` (`fmtTimecode`, `fmtBytes`); the per-file copies are
   gone.
8. **Agent guidance exists.** `docs/design-language.md` lists tokens,
   classes, macros, and JS helpers with examples; `CLAUDE.md`'s frontend
   section points to it.
