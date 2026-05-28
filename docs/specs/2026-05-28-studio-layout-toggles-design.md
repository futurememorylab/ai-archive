# Studio — layout toggles (list / player / prompt-output position)

**Date:** 2026-05-28
**Status:** Approved (design)
**Extends:** `docs/specs/2026-05-26-prompt-studio-design.md`
**Predecessors:**
- post-PR2 — `docs/adr/0037-studio-shared-player-chrome-and-focused-clip-url.md`
- PR3 polish — `docs/adr/0039-prompt-studio-pr3-polish.md`

## Problem

The Studio page packs four regions into one screen — the video list
(left), the video player, the prompt/output card, and an optional
compare card. On smaller screens or when the user is focused on one
task (reading a long prompt, scrubbing a clip, comparing output), there
is no way to reclaim space. The only space control today is an
ad-hoc player **minimise** (`−`) button baked into the player's
top-right corner, paired with a **restore** (`▭`) icon in the header —
two single-purpose affordances for one region.

We want a small, VS-Code-style cluster of layout toggles, placed to the
left of the Run button, that manage screen real estate coherently:

1. Show / hide the **video list**.
2. Show / hide the **video player** (this replaces the player's corner
   `−` button and the header `▭` restore icon).
3. Switch the **prompt/output position** between *under* the player
   (stacked — the current layout) and *right of* the player
   (side-by-side).

## Goals

1. **Three header toggles, left of Run.** A `.studio-layout-toggles`
   group of three small icon buttons using the canonical `.btn`
   icon-button pattern, with an `active`/pressed state, VS-Code-style
   monochrome glyphs (24×24, `currentColor`, matching the existing
   stroke icons in `templates/icons/`).
2. **List toggle.** Hides/shows the `.studio-videos` column. When
   hidden the right pane takes the full width.
3. **Player toggle.** Hides/shows the player slot. Removes the player's
   corner `−` minimise button and the header `▭` restore icon — the new
   toggle is the single control. The player is only ever shown when a
   clip is focused (no focused clip ⇒ nothing to show, as today).
4. **Layout toggle.** Switches the prompt/output card between
   `under` (video on top, prompt/output below — current) and `right`
   (video on the left, prompt/output on the right). The video list
   stays the far-left column in both modes.
5. **Compare gating.** The `+ Compare` button in the prompt-card header
   is available **only** in `under` layout. Switching to `right`
   auto-closes any open compare card.
6. **Persistence.** The three toggle states persist to
   `localStorage` (`studio.layoutPrefs`) per browser, restored on every
   Studio visit, applied before first paint to avoid flicker.

## Non-goals

- No change to the Prompt/Output **tabs** inside the card — they stay
  tabbed (`mode` state); the card itself is always visible.
- No resizable/draggable splitters. The split ratios are fixed in CSS.
- No per-prompt or server-side persistence — `localStorage` only.
- No new keyboard shortcuts.

## Approach

**CSS-grid-class-driven, no DOM moves.** The current DOM already nests
correctly:

```
.studio-body (grid-cols: 320px | 1fr)
 ├ .studio-videos        ← video list
 └ .studio-right         ← player-slot + compare are SIBLINGS here
    ├ .studio-player-slot
    └ .studio-compare (prompt card + cmp-slot)
```

`.studio-right` is `grid-template-rows: auto 1fr` today (player stacked
over prompt/output). Re-declaring it as `grid-template-columns` places
the **same two siblings side-by-side** — player left, prompt/output
right — which is exactly `under → right`. So every toggle is a pure CSS
modifier class on `.studio-body` / `.studio-right`; **no DOM nodes
move**, so the Alpine + HTMX wiring on the player, prompt card, and
compare slot is untouched.

Rejected alternatives:
- **JS relocating DOM nodes** between containers per layout — fragile
  with Alpine's `initTree` / HTMX re-scan dance that the studio already
  fights (see `studio.js` comments).
- **Per-layout templates** — duplicates the player + card includes.

## State (`studio.js` `studioPage`)

Replace `playerMinimized` with:

```js
showList: true,
showPlayer: true,
layout: 'under',          // 'under' | 'right'
```

- `init()` reads `localStorage['studio.layoutPrefs']` (JSON
  `{showList, showPlayer, layout}`) and applies, falling back to
  defaults on parse error / absence.
- `toggleList()`, `togglePlayer()`, `setLayout(v)` flip state and call
  `_saveLayoutPrefs()`.
- `setLayout('right')` calls `closeCompare()` when `compareVersionId`
  is set.
- **Removed:** `playerMinimized`, `minimizePlayer()`, `restorePlayer()`,
  and the `window.studio.minimizePlayer` shim.
- Player is visible when `showPlayer && focusedClipId`.

Body/right classes are kept in sync by Alpine `:class` bindings on
`.studio-body` (`no-list`, `no-player`) and `.studio-right`
(`layout-right`), mirroring the existing `:class="{ 'no-player': … }"`
pattern.

### First paint (no flicker)

`studio.html` already stamps a static first-paint class
(`no-player` when no focused clip). Extend that with a tiny inline
`<script>` near the top of the page body that reads
`localStorage['studio.layoutPrefs']` and adds `no-list` / `no-player` /
`layout-right` to the static class set **before** Alpine boots. The
`:class` bindings then keep them in sync at runtime. Server-rendered
defaults remain `list on · player per focused-clip · under`.

## CSS (`app.css`, studio block)

- `.studio-body.no-list` → `grid-template-columns: 1fr;` and
  `.studio-videos { display: none; }`.
- `.studio-body.no-player .studio-player-slot { display: none; }`
  (existing rule — repurposed; no longer keyed off `playerMinimized`).
- `.studio-right.layout-right` →
  `grid-template-columns: minmax(360px, 1fr) 1fr; grid-template-rows: 1fr;`
  (player left, prompt/output right; player won't shrink below 360px;
  both columns full-height with their own scroll). In this mode the
  player slot's bottom border becomes a right border.
- `.studio-layout-toggles` → `display: flex; gap: 4px;` placed before
  the Run button (after the `grow` spacer) in `.studio-hdr`.
- Toggle buttons reuse `.btn` (icon variant); pressed state via an
  `active` class (same convention as `.pc-hdr .btn.active`).

## Templates

- `_studio_header.html`: remove the `.studio-show-player` (`▭`) button;
  add the `.studio-layout-toggles` group (3 buttons) before the Run
  button. Buttons bind `@click` to `toggleList()` / `togglePlayer()` /
  `setLayout(layout === 'under' ? 'right' : 'under')` and `:class` to
  their active state.
- `_studio_player.html`: remove the `.studio-player-min` (`−`) minimise
  button (it currently calls `window.studio.minimizePlayer()`). This
  button lives in the studio-only wrapper, **not** in the shared
  `_player.html` — the clip-detail page never had it, so the shared
  chrome is untouched.

- `studio.html`: extend the static first-paint class + add the inline
  prefs script; update `:class` bindings on `.studio-body` /
  `.studio-right`.
- `_studio_prompt_card.html`: gate `+ Compare` with
  `compareVersionId === null && layout === 'under'`; add a
  `get layout()` proxy to `studioPromptCard` (same pattern as the
  existing `get mode()` / `get compareVersionId()` proxies).

## Icons

Three new glyphs under `templates/icons/`, matching the existing stroke
style (`viewBox="0 0 24 24"`, `stroke-width="1.7"`, `currentColor`):

- `_panel_left.svg` — list toggle (rectangle, left column divided off).
- `_panel_top.svg` — player toggle (rectangle, top row divided off).
- `_layout_under.svg` / `_layout_right.svg` — the layout toggle shows
  the bottom-split glyph in `under` mode and the right-split glyph in
  `right` mode (`x-show` swaps the two, like the play/pause swap in
  `_player.html`).

## Testing

Following existing studio test patterns:

- **CSS guards** (`tests/unit/`, `test_studio_css_*` style): assert
  `app.css` defines `.studio-body.no-list` and
  `.studio-right.layout-right` rules.
- **Template guards** (`tests/unit/`): `_studio_player.html` /
  `_player.html` no longer contain the `studio-player-min` minimise
  button; `_studio_header.html` no longer contains
  `studio-show-player`; the rendered studio page contains the three
  toggle buttons (`data-studio-toggle="list|player|layout"` markers).
- **Integration render** (`tests/integration/`): GET `/studio?prompt_id=…`
  returns 200 and the header contains the `.studio-layout-toggles`
  group with all three toggles.
- Existing player/studio/css suite stays green
  (`pytest -k "player or studio or css or button"`).

## Implementation notes / commit boundaries

Suggested slices (the plan may re-slice under TDD):

1. **Icons** — add the four SVG partials.
2. **State** — `studioPage` prefs model + `localStorage` load/save +
   remove `playerMinimized`/minimise/restore/shim.
3. **CSS** — `no-list`, `layout-right`, `.studio-layout-toggles`,
   right-mode border flip; CSS guard tests.
4. **Templates** — header toggle group (remove `▭`), remove player `−`
   button, prompt-card compare gating + `get layout()`, studio.html
   first-paint script + `:class` bindings; template guard tests.
5. **Integration test + ADR + `docs/decisions.md`.**

The minimise button to remove lives in `_studio_player.html` (the
studio-only wrapper, routed through `window.studio.minimizePlayer`); the
shared `_player.html` never had it, so the clip-detail player is
unaffected.

## Open questions

None blocking.

- Player width in `right` mode is fixed at `minmax(360px, 1fr) 1fr`
  (≈50/50, player floor 360px). Revisit if it feels cramped in
  practice; a draggable splitter is a future ask.

## Manual acceptance flows

Setup for all flows: dev server running on `http://127.0.0.1:8765`, a
prompt with at least one version and a folder containing ≥1 clip.
Navigate to `/studio?prompt_id=<id>` and click a clip in a folder to
focus it (so the player is showing).

1. **List toggle.** Click the left-panel toggle icon (left of Run).
   *Expected:* the video-list column disappears and the right pane
   widens to fill it; the icon shows a pressed/inactive state. Click
   again → the list returns.

2. **Player toggle.** Click the player toggle icon. *Expected:* the
   video player hides and the prompt/output area takes its space; the
   icon reflects the off state. Click again → the player returns,
   playing the focused clip. Confirm there is **no** `−` button in the
   player's top-right corner and **no** `▭` icon in the header.

3. **Layout: under → right.** With the player showing, click the layout
   toggle. *Expected:* the player moves to the left and the
   prompt/output card sits to its right (side-by-side), both
   full-height. The layout icon changes to the right-split glyph. Click
   again → back to player-on-top, prompt/output below.

4. **Compare gating.** In `under` layout, confirm `+ Compare` is
   visible and opens a second card. With compare open, click the layout
   toggle to `right`. *Expected:* the compare card closes automatically
   and the single prompt/output card sits right of the player; while in
   `right` layout the `+ Compare` button is not shown. Switch back to
   `under` → `+ Compare` reappears.

5. **Persistence.** Set a non-default combination (e.g. list off,
   layout right). Reload the page. *Expected:* the page restores with
   list off and right layout with no visible flash of the default
   layout. Open the same Studio URL in a new tab → same restored state.

6. **No-clip state.** With no clip focused (fresh `/studio?prompt_id=<id>`
   with no `clip_id`), confirm the player toggle has nothing to show
   (player area empty / "click a clip" hint) and toggling list/layout
   still works without errors.
