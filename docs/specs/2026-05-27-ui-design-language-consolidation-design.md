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

The same pass also fixes three issues in the **review-mode (HITL)
editing UI** that the `feat/draft-review-accept` work introduced — they
are new instances of exactly the kind of cramped, hand-rolled markup
this library replaces, so they are folded in here rather than left to
diverge: #6 always-on cramped per-item edit inputs, #7 white
(unstyled) checkboxes, #8 marker time adjustment via number spinners.
See "Review-mode UI (HITL)" under Design.

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

### Review-mode UI (HITL) — issues #6–#8

These touch the HITL per-item editor rendered by `_anno_panels.html` in
review mode (gated by `item_id`) and the shared timeline overlay
(`_player_overlay.html`). They reuse the library above (`.field` /
`.txt-area`, accent tokens) — no new renderer.

**#6 — Edit is gated and roomy (Option A: read-only row → inline
expand).** Today every draft item renders all its edit inputs inline and
always-editable (`_anno_panels.html:53-71`, the `.ri-marker` / `.ri-mfield`
inputs), cramped in the narrow draft pane. New model:

- Each item renders as a **read-only row**: accept checkbox + display
  text + SMPTE timecode. No editable inputs by default.
- A per-item **Edit** (pencil) button toggles an inline expansion into a
  roomy editor built from the `field` / `textarea_field` macros
  (full-width, autosizing); **Done** collapses it back. The timeline and
  sibling items stay in view.
- Editing state is a single `editingItemId` on the **player root scope**
  (see drag wiring below); at most one item is expanded at a time.

**#7 — Accent checkboxes.** `.ri-accept` and `.row-check` have no
`accent-color`, so they render as the browser default (white) on the
dark theme. Add `accent-color: var(--accent)` + consistent sizing as
part of the canonical CSS; documented as the standard checkbox
treatment so future checkboxes inherit it.

**#8 — Drag-to-adjust markers on the timeline (replaces the spinners).**
Replace the `<input type="number">` in/out spinners
(`_anno_panels.html:66-70`) with direct manipulation on the timeline,
**activated by editing**:

- Clicking **Edit** on a marker sets `editingItemId`. The matching draft
  range in the timeline reacts via
  `:class="{ editing: editingItemId === item_id }"` — it changes color
  (accent) and reveals drag affordances. **Only the marker being edited
  is draggable** (no accidental drags).
- **Body drag** moves the marker (in+out together); **left/right edge
  handles** trim in/out independently. Pointer Events
  (`pointerdown`/`move`/`up` + `setPointerCapture`); `stopPropagation`
  so a drag does not also seek the player.
- **Keyboard nudge** while a marker is being edited: `←`/`→` = **±1
  second**, `Shift`+`←`/`→` = **±1 frame** (frame-accurate). The second
  step is tunable.
- The editor row shows the in/out as a **read-only SMPTE readout** that
  updates live as you drag/nudge; the underlying value persists via the
  existing `decision` endpoint (writing `edited_value`), unchanged.

**Feasibility (confirmed).** The pixel→time mapping already exists —
`player.js:71` `seekFromEvent` computes
`(clientX - rect.left) / rect.width × duration`; drag reuses it on
`pointermove`. Ranges are already `%`-positioned divs
(`_player_overlay.html:33`), so live-updating `left`/`width` is direct.
Scope is shared: the timeline is in the root `player(...)` scope
(`clip_detail.html:12`) and the panel is a nested `reviewQueue(...)`
child (`:220`→`:237`); an Alpine child can read/write ancestor state, so
`editingItemId` + drag logic live on the player root and the panel's
Edit button sets them. The one mechanical task is tagging each draft
range with its `item_id` so range↔row correlate. Caveat accepted: drag
is coarse on long clips — which is why the keyboard nudge stays.

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
- **Review editing = Option A** (read-only row → inline expand on Edit),
  not a drawer/modal (B) or whole-panel edit toggle (C). Keeps timeline
  + sibling items in context.
- **Marker editing is the drag trigger.** A marker is draggable on the
  timeline only while it is the item being edited; editing also recolors
  its range. Avoids accidental drags and ties the two surfaces together.
- **Nudge step = ±1 s (arrow), ±1 frame (Shift+arrow).** Chosen over
  ±0.5 s or ±10-frame schemes; the 1 s coarse step is tunable.

## Risks & sequencing

This rewrites `app.css` and many templates — high collision risk with
the `feat/draft-review-accept` work. To both isolate and *consume* that
work, this branch is **based on `feat/draft-review-accept`** (rebased
onto its tip), in a dedicated git worktree. That gives us their review
UI to consolidate (the #6–#8 fixes) and their `row_select.js` /
`cache_page.html` changes to dedupe against, in one pass — rather than
building competing helpers. Because the consolidation is a sweeping
refactor, it lands **after** the feature it builds on: ideally
`feat/draft-review-accept` merges first and this rebases onto the result
before merge. Note that branch was still moving during design (its tip
advanced and the `/review` page template was not yet present at
rebase); re-sync before implementation. The CatDV license seat is
shared — UI work needs the dev server only for manual acceptance, and
must follow the single-instance + graceful-shutdown discipline in
CLAUDE.md.

The consolidation absorbs their helpers rather than duplicating them:
the `bytesHuman` carried in `row_select.js` becomes a call to
`format.js`'s `fmtBytes`; the rail badge (`.rail-badge`) is documented
under the pill family; and the new review-page buttons/fields adopt the
canonical `.btn` / `.field` classes.

Suggested implementation order (kept reviewable):

1. `format.js` + replace the 3× timecode, cache `bytesHuman`, and the
   `row_select.js` `bytesHuman` (pure, isolated, testable).
2. Tokens + canonical `.btn`, form-field, and **accent checkbox** (#7)
   CSS; delete dup/conflicting rules (`.btn`, `.modal-body`,
   `.anno-draft-chip`); retoken `.anno-status`.
3. `components/_ui.html` macros (`button`, `field`, `textarea_field`,
   `page_header`, `breadcrumb`, `status_pill`).
4. Migrate templates onto the macros/classes: prompt forms (#1 bad
   button, #2 fields), header/breadcrumb (#3), and the review page /
   panels.
5. Cache page tweaks (#4 relabel, #5 remove raw bytes).
6. Review-mode editor (#6): read-only rows + inline-expand Edit using
   the field macros; `editingItemId` on the player root.
7. Timeline drag (#8): tag draft ranges with `item_id`; pointer
   drag + edge handles + keyboard nudge; replace the in/out spinners
   with a live SMPTE readout.
8. `docs/design-language.md` + `CLAUDE.md` pointer.

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
9. **Review editing is gated and roomy (#6).** Open a clip with a draft
   in review mode (`/clips/{id}?review=1`). Each draft item is a
   read-only row (checkbox + text + timecode) with no editable inputs.
   Click **Edit** on one item → it expands into a full-width, autosizing
   editor; **Done** collapses it. Only one item is expanded at a time.
10. **Checkboxes match the theme (#7).** The accept checkboxes on the
    review panel and the `/cache` row checkboxes render in the accent
    color (not white) and are legible on the dark background.
11. **Drag a marker on the timeline (#8).** Click **Edit** on a marker →
    its range on the timeline changes color and shows drag handles
    (other markers do not). Drag the body to move it and an edge handle
    to trim; the editor's SMPTE in/out readout updates live. With the
    marker active, `←`/`→` nudges by 1 s and `Shift`+`←`/`→` by 1 frame.
    Dragging never seeks the player. There are no number-spinner inputs.
    Apply → the adjusted in/out reach CatDV.
