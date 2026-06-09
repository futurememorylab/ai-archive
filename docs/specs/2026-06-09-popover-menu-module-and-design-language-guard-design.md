# Popover Menu Module + Design-Language Enforcement Guard

**Date:** 2026-06-09
**Status:** Approved (design)

## Problem

The 2026-05-27 UI consolidation ("Phase 1") deepened the *primitives* —
the `.btn` system, form fields, `fmtTimecode`/`fmtBytes`. It explicitly
deferred a "Phase 2". In the two weeks since, every feature that needed a
*composite* pattern re-hand-rolled it, because no primitive covered it
and **nothing failed CI when a coder bypassed the library**.

The clearest instance is the **popover menu** — a trigger button that
opens a floating panel of items, closes on click-outside / escape. It is
implemented **seven times** under seven class vocabularies, each
re-writing the same open/close wiring and the same absolutely-positioned
panel CSS:

| Menu | Files | Classes |
|---|---|---|
| Prompt version picker | `_prompt_version_picker.html` | `version-picker` / `version-menu` / `version-menu-item` |
| Annotate dropdown (async) | `_annotate_dropdown.html` | `annotate-wrap` / `annotate-menu` / `annotate-item` |
| Prompt "⋯" actions | `_prompt_menu.html` | `tmpl-menu` / `tmpl-menu-item` / `tmpl-menu-sep` |
| Studio header prompt switch | `_studio_header.html` | `hdr-tpicker` / `hdr-tmenu` / `hdr-tmenu-{item,h,lbl,name,desc,v}` |
| Studio version chip | `_studio_version_picker.html` | `pc-vchip` / `pc-vmenu` / `pc-vmenu-{item,h,lbl}` |
| Model picker (selection) | `_studio_prompt_card.html`, `_prompt_detail.html` | `model-picker` / `model-menu` / `model-menu-{item,lbl,dot}` |
| Clips row actions | `clips.html` | `actions-menu` / `actions-btn` |

Concretely, what the audit found:

- **The chrome is identical and duplicated.** Every panel is
  `position:absolute; top:calc(100% + 4px); …; z-index; box-shadow`
  (`app.css` lines 365, 1079, 1121, 1195, 1279, 1992, 2091). Every menu
  re-declares `x-show="open"` + `@click.outside` + a caret glyph
  (`▾` / `&#9662;`). Several panel rules are even **page-scoped** —
  `.prompts-page .version-menu`, `.studio-hdr .hdr-tmenu` — a generic
  concept wearing a page prefix.

- **The open-state lives in two incompatible places, and one trips a
  landmine.** Three menus keep the `open` flag inline on their own
  wrapper (`x-data="{ open: false }"`): version picker, `pc-vmenu`,
  `hdr-tpicker`. Three bind a flag on a surrounding component
  (`annoOpen` in `bulkAnnotate.js:7`, `modelOpen` in `studio.js:280` /
  `promptEditor.js:21`, `open` in `clipAnnotate.js:3`). `_studio_header.html`
  carries a ~10-line comment documenting that putting an `x-data` scope
  on the menu wrapper **shadows** the `studioPage` scope, so
  `focusedClipId` reads as `undefined` — the nested-scope hazard is real
  and already cost someone an afternoon.

- **One menu is genuinely different.** `annotate-menu` fetches its items
  over the network on first open (`clipAnnotate.js:23` `toggleOpen()` →
  `loadPrompts()`), with loading / error / empty states. It is a popover
  *hosting an async list*, not a static menu.

- **There is no guard.** Every other "removed pattern" in this repo has
  an automated tripwire — `test_no_x_data_stack.py`,
  `test_templates_shared.py`, `test_htmx_alpine_single_lifecycle.py`,
  the structural-erosion gate (ADR 0060). Phase 1 shipped as
  documentation + discipline only. That is precisely why bespoke shapes
  re-grew after it: `studio-run-btn`, `hdr-title-btn`, `mp-fail-btn`,
  `actions-btn`, and a hand-rolled timecode at `player.js:181` all
  post-date the cleanup.

`_video_list.html` is the counter-example worth copying: a genuinely
deep module — documented params, `head_cells` / `row_cells` injection
points — that callers reuse instead of re-rendering. The menu work
should reach that shape.

## Goals

1. **One popover module** that owns the open/close/escape/click-outside
   behavior and the floating-panel chrome, usable both by simple
   standalone menus and by menus nested inside a larger component
   (without re-introducing the scope-shadowing hazard).
2. **One menu look** (`.menu` / `.menu-item` family) plus a thin macro
   for ordinary rows, so the common menu is one line per row.
3. **Migrate all seven** menus onto the module; delete the seven
   bespoke vocabularies and the duplicated panel CSS.
4. **An enforcement guard** (the durable artifact): a test that fails CI
   when a *new* bespoke `*-btn` / `*-menu` class or a hand-rolled
   timecode/byte formatter appears, with today's names grandfathered and
   burned down as the menus migrate.

All within the no-Node / no-build constraint (ADR 0001): plain CSS
classes, a Jinja macro, and one `Alpine.data(...)` registration (ADR
0048 — `Alpine.data`, never `_x_dataStack`).

## Non-goals

- **Modals (Candidate B) and the clip media card (Candidate D) are out
  of scope.** They are real duplication but separate deepenings; this
  spec is Candidate A + C only. The guard is *designed to extend* to
  them later (a `-modal` / card rule added to the same allow-list) but
  ships covering menus + buttons + formatters.
- No JS framework, bundler, or new Alpine directive. The two-mode
  popover is a plain `Alpine.data` factory + macro, not an `x-popover`
  directive (see Alternatives).
- No new color palette or visual redesign — same look, consolidated
  implementation. The migrated menus must be pixel-equivalent.
- The intentional status-indicator family stays separate (`.env-pill`,
  the connection chip) — unchanged from Phase 1.

## Design

The module is **two layers** because the seven call sites cross two
different seams: all seven share the *chrome* (open/panel), but only
five-to-six share the *item look*, and the async/selection menus need to
keep their own bodies.

### Layer 1 — Popover behavior (`static/popover.js`, two modes)

A new `static/popover.js` registers a single Alpine factory, loaded in
`layout.html` alongside `format.js`:

```js
// Standalone mode: the menu owns its own open flag.
Alpine.data("popover", () => ({
  open: false,
  toggle() { this.open = !this.open; },
  close() { this.open = false; },
}));
```

**Mode 1 — standalone** (version picker, `pc-vmenu`): the wrapper is
`x-data="popover()"`; the trigger calls `toggle()`; the panel binds
`x-show="open"`, `@click.outside="close()"`,
`@keydown.escape.window="close()"`.

**Mode 2 — hosted** (annotate, model picker, Studio header): the menu
lives inside an existing component that already owns a flag
(`annoOpen`, `modelOpen`, …). The wrapper gets **no `x-data`** — that is
what shadows the parent scope. Instead the macro wires the same four
bindings against the caller's flag *expression*:

```jinja
{# hosted: no new scope; binds to the parent's flag #}
@click="annoOpen = !annoOpen"
x-show="annoOpen"  @click.outside="annoOpen = false"
@keydown.escape.window="annoOpen = false"
```

Because hosted mode introduces no scope, `hdr-tmenu` can keep reading
`focusedClipId` off `studioPage` — the landmine is structurally
removed, not commented around.

The macro picks the mode by whether a `state=` expression is passed
(see Layer 3). Click-outside and escape attach to the panel element,
which sits inside whichever scope owns the flag — correct in both modes.

### Layer 2 — Menu look (`app.css`)

One canonical, **un-page-scoped** vocabulary replacing the seven:

| Class | Replaces | Purpose |
|---|---|---|
| `.popover-panel` | the 7 copied `position:absolute; …; box-shadow` blocks | the floating panel chrome; `.align-right` modifier for right-anchored menus (`tmpl-menu`, `hdr-tmenu`) |
| `.menu` | `version-menu`, `tmpl-menu`, `pc-vmenu`, `hdr-tmenu`, `model-menu`, `actions-menu` | the item list container |
| `.menu-item` | `*-menu-item`, `*-item` | a row (`<a>`, `<button>`, or `<button type=submit>`) |
| `.menu-item.danger` | `tmpl-menu-item.danger` | destructive row (red) |
| `.menu-item.is-current` | `*-item.is-current` | selected/active row |
| `.menu-sep` | `tmpl-menu-sep` | hairline divider |
| `.menu-header` | `pc-vmenu-h`, `hdr-tmenu-h` | small uppercase section label |
| `.menu-item .menu-meta` | `hdr-tmenu-v`, trailing version/id text | right-aligned mono meta |
| `.menu-item .menu-desc` | `hdr-tmenu-desc` | sub-description line |
| `.menu-item .menu-dot` | `model-menu-dot` | leading LED dot (model picker) |

Panel/item tokens come from the existing palette (`--panel-2`, `--line`,
`--hover`, `--accent-2`, `--r-1`/`--r-2`) — no new tokens.

### Layer 3 — Macros (`templates/components/_ui.html`)

```jinja
{# Standard menu: renders a .btn trigger (label + caret) + the panel.
   Items go in the {% call %} body. `state=` selects hosted mode. #}
{% macro menu(label='', variant='ghost', size='sm', align='left',
              state=None, trigger_attrs='') %}…{% endmacro %}

{# One ordinary row. Exactly one of href / post / action is given. #}
{% macro menu_item(label, href=None, post=None, action=None,
                   icon=None, danger=False, current=False,
                   meta=None, desc=None) %}…{% endmacro %}

{% macro menu_sep() %}<div class="menu-sep"></div>{% endmacro %}
{% macro menu_header(label) %}<div class="menu-header mono-cell">{{ label }}</div>{% endmacro %}
```

- `menu()` with no `state=` → standalone (`x-data="popover()"`); with
  `state='annoOpen'` → hosted (binds to that flag, no `x-data`).
- `menu_item()` renders `<a>` for `href`, a `<form method=post>` +
  submit button for `post`, or a `<button @click>` for `action` (a raw
  Alpine expression). This is the only place the link/form/action
  branching lives.
- **Bespoke triggers keep custom markup.** The Studio header title
  button (`hdr-title`) and the version chip (`pc-vchip`) are not plain
  `.btn`s; they keep their trigger element but adopt the `.popover-panel`
  / `.menu` classes and the popover wiring (hosted or standalone). Per
  Phase 1's rule: macros where they remove duplication, raw markup +
  shared classes where a macro would fight the trigger's look.

### The two oddballs

- **`annotate-menu` (async)** plugs into Layer 1 (hosted mode, reusing
  `clipAnnotate.js`'s existing `open`) and Layer 2 classes
  (`.popover-panel`, `.menu`, `.menu-item`), but keeps its
  `<template x-for>` + loading/error/empty body. We do **not** force its
  fetched rows into `menu_item()` — that body is genuinely
  feature-specific. Result: same chrome and look, no contrived
  declarative layer.
- **`model-menu` (selection)** is hosted mode over `modelOpen`, items
  rendered with `menu_item(..., current=…, icon=dot)` reusing the
  `.menu-dot` LED. Picking a model closes via the parent's existing
  handler.

### Candidate C — the enforcement guard

A new `tests/unit/test_design_language_guard.py`, same shape as
`test_no_x_data_stack.py`:

1. **Class allow-list.** Scan `templates/` for classes matching the
   bespoke suffixes `-btn` and `-menu` (regex on `class="…"` tokens).
   Any match **not** in a `GRANDFATHERED` frozenset fails the test with a
   message pointing at `docs/design-language.md` and the canonical
   `ui.menu` / `.btn`. The canonical names (`menu`, `menu-item`, …) carry
   no `-btn`/`-menu` *suffix* on a prefixed token, so they never trip it;
   layout-only helpers on the `.btn` system (`btn-row`, `btn-caret`) are
   *prefixes*, out of the suffix rule.
2. **Formatter check.** Scan `static/*.js` (excluding `format.js`) for
   hand-rolled timecode (`padStart` co-located with `% 60`) and byte
   loops (`>= 1024`); any hit fails, pointing at `fmtTimecode` /
   `fmtBytes`. This catches `player.js:181` today.

Initial `GRANDFATHERED` set:

```python
GRANDFATHERED = frozenset({
    # intentional, permanent (status / chrome, not action buttons)
    "shutdown-btn", "transport-btn", "rail-btn",
    # to migrate — deleted from this set as each menu lands
    "version-menu", "annotate-menu", "tmpl-menu", "hdr-tmenu",
    "pc-vmenu", "model-menu", "actions-menu", "actions-btn",
    "studio-run-btn", "hdr-title-btn", "mp-fail-btn",
})
```

The guard ships **first** (PR 1) with the full set. Each migration PR
deletes the entry it retired, so the set shrinks to the three permanent
exceptions. The guard is a pytest unit test (not the pre-commit erosion
gate) to match the existing `test_no_*` family.

### Agent guidance (the durable artifact)

- **`docs/design-language.md`** gains a "**Menus & popovers**" section:
  the `popover()` two modes, the `.menu` / `.menu-item` family, the
  `ui.menu` / `ui.menu_item` macros, each with a copy-paste example and a
  red-flag ("writing a new `*-menu` class / a second `x-data="{ open`
  toggle → use `ui.menu`").
- The guard's failure message names the doc, so a coder who trips it is
  routed straight to the reuse path.

## Alternatives

**State seam (Decision 1).**
- *Always self-contained* — every popover gets its own `x-data`.
  Simplest factory, but re-introduces the `hdr-tmenu` shadowing hazard
  and forces rewriting the three parent-owned menus. Rejected.
- *Custom `x-popover` directive* — adds behavior with no data scope, so
  shadowing is impossible. Most elegant, but a hand-written Alpine
  directive is machinery we own and test forever, for a payoff the
  two-mode macro already delivers. Rejected (revisit only if a third
  scope hazard appears).
- *Two-mode macro (chosen)* — standalone or hosted, selected by `state=`.
  Fits all seven sites, removes the landmine structurally, no new
  framework surface.

**Item depth (Decision 2).**
- *Fully declarative* (`ui.menu(items=[…])` draws everything) — great
  for the static menus, but `tmpl-menu` mixes form-POST + link + Alpine
  action rows in one menu, so the descriptor grows a knob per variation,
  and `annotate-menu`'s fetched rows can't be expressed at all. Rejected.
- *Thin chrome only* (shared socket, every caller writes raw rows) —
  leaves the row markup (icon + label + desc + meta + danger) duplicated.
  Rejected — solves half the problem.
- *Hybrid (chosen)* — chrome + `menu_item()` for ordinary rows; async /
  selection menus reuse chrome + classes but keep their own bodies.

**Guard rollout (Decision 3).**
- *Clean-slate in one PR* — migrate all seven + enable the guard with a
  clean allow-list at once. Best end-state, but one sprawling diff across
  7 templates + `app.css` + 4 JS files; high collision and review cost.
  Rejected as the *first* step (it is the destination).
- *Ratchet (chosen)* — guard first with everything grandfathered, then
  burn the list down one menu at a time. Stops new bleeding immediately;
  each PR small and green.

## Decisions

- **Two-mode popover.** One `Alpine.data("popover")` factory; the macro
  emits a self-contained scope for standalone menus and binds to a
  parent flag (no new scope) for hosted menus. This is the deferred
  Phase 2 of the 2026-05-27 spec, not a contradiction of it.
- **Hybrid item rendering.** `menu_item()` for ordinary rows; the async
  `annotate-menu` and the `model` selection picker keep bespoke bodies
  on shared chrome + classes.
- **Ratchet guard.** `test_design_language_guard.py` ships first with a
  grandfathered allow-list that shrinks per migration PR to the three
  permanent exceptions.
- **Scope is A + C only.** Modals (B) and the clip card (D) are not
  touched; the guard is built to extend to them later.

These four are ADR-worthy when the first PR lands — record them as an
ADR per the repo's end-of-session discipline (e.g. "Two-mode popover and
the design-language enforcement guard").

## Risks & sequencing

- **License seat discipline.** UI work needs the dev server only for the
  manual acceptance pass; follow the single-instance + graceful
  (`SIGTERM`) shutdown rules in `CLAUDE.md`.
- **Collision risk.** The migration touches `app.css` and several
  templates; land it promptly after PR 1 and rebase rather than letting
  it sit behind other UI branches.
- **Pixel-equivalence.** The migrated menus must look unchanged; the
  acceptance flow checks each menu still opens, closes on
  click-outside/escape, and renders its rows identically.

Suggested order (each PR green, each shrinks the grandfathered list):

1. **PR 1 — module + guard + two pilots.** `static/popover.js`;
   `.popover-panel` / `.menu` family in `app.css`; `menu` / `menu_item` /
   `menu_sep` / `menu_header` macros; `test_design_language_guard.py`
   (full allow-list); migrate the two simplest standalone menus
   (version picker, `pc-vmenu`) as proof; delete their entries.
   `docs/design-language.md` "Menus & popovers" section.
2. **PR 2 — the trickier menus.** `tmpl-menu` (mixed row types →
   `menu_item` variants), `hdr-tmenu` (hosted mode, proves the
   shadowing fix), `annotate-menu` (hosted, async body kept),
   `model-menu` (hosted, selection), `actions-menu`. Delete the remaining
   migrated entries; the menu/btn allow-list ends at the permanent
   exceptions. (Implementation note: `player.js`'s `tc()` is frame-accurate
   SMPTE, NOT an `m:ss` duplicate of `fmtTimecode` — it stays, permanently
   grandfathered for the formatter check.)

## Manual acceptance flows

A colleague who didn't write the code can run these on the app and tick
them off.

1. **Every menu opens and dismisses identically.** On `/prompts` open
   the version picker and the "⋯" actions menu; on `/studio` open the
   header prompt switch, the version chip, and the model picker; on `/`
   (clips) open the row actions and the Annotate dropdown. Each opens on
   click, closes on click-outside, and closes on `Esc`. All share one
   panel look (radius, shadow, hover).
2. **Studio header still reads the focused clip (#landmine).** In
   `/studio`, click a clip to focus it, open the header prompt-switch
   menu, switch prompts — the focused `clip_id` is still carried
   (the switch lands on the same clip). Proves hosted mode did not
   shadow `studioPage`.
3. **Mixed-row menu works (`tmpl-menu`).** On `/prompts` the "⋯" menu's
   "Promote" (form POST), "Export JSON" (link/download), and
   "Duplicate…" (opens the duplicate dialog) all work, the separator and
   the red "Archive" render correctly — all via `menu_item()`.
4. **Async menu still loads (`annotate-menu`).** On `/` open the
   Annotate dropdown on a clip; it shows "Loading…", then the production
   prompts; picking one starts a run. Same panel chrome as the static
   menus.
5. **Model picker selection (`model-menu`).** On `/studio` (or
   `/prompts`) open the model picker; the current model shows its LED
   dot / `is-current`, picking another updates and closes.
6. **Timecode still correct.** Player (SMPTE `hh:mm:ss:ff`) and Studio
   timestamps display correctly. `player.js`'s `tc()` stays (the unique
   frame-accurate SMPTE formatter); the guard's formatter check blocks *new*
   `m:ss`/byte re-implementations elsewhere.
7. **The guard fails on a fresh bespoke class.** Add a throwaway
   `class="foo-menu"` (or `foo-btn`) to any template and run
   `pytest tests/unit/test_design_language_guard.py` — it fails, naming
   `docs/design-language.md` and `ui.menu` / `.btn`. Remove it → green.
8. **The grandfathered list shrank.** After PR 2,
   `GRANDFATHERED` contains only `shutdown-btn`, `transport-btn`,
   `rail-btn`; no `*-menu` class remains in `templates/` except the
   canonical `.menu` family; the seven bespoke panel rules are gone from
   `app.css`.
9. **Agent guidance exists.** `docs/design-language.md` has a "Menus &
   popovers" section with the two `popover()` modes, the `.menu` family,
   and the `ui.menu` / `ui.menu_item` examples + red flags.
