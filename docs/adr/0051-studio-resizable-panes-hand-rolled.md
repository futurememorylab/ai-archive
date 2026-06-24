# 0051. Studio resizable panes — hand-rolled splitters, nested 3-column right layout

**Date:** 2026-06-02
**Status:** Accepted
**Lifespan:** Feature

Full design: `docs/specs/2026-06-02-studio-resizable-panes-design.md`.

## Context

The Studio panes (player, prompt cur, compare cmp) had fixed sizes. Users
wanted to drag the borders to resize: cur ↔ cmp, player ↔ prompt, and a
right-layout arrangement placing player | prompt | compare as three resizable
columns, with the sizes remembered and a resize affordance on hover.

## Alternatives

1. Vendor a JS split-pane library (e.g. `html-diff.lix.dev`-style packages,
   Split.js, jsdiff-adjacent) — capable, but a Node/npm frontend dependency.
2. Hand-roll splitters: a divider element per boundary, dragging updates a CSS
   custom property that drives the container's grid/flex track sizes.
3. A true flat 3-track grid for the right layout (player, cur, cmp as three
   sibling tracks) so each divider resizes only its two immediate neighbours.

## Decision

**Alternative 2 (hand-rolled), with the nested arrangement of alternative 3's
intent.** The user explicitly ruled out a Node dependency (ADR 0001), so no
library. Implementation (see spec for detail):

- `studioResize.js` — a pure `clampSize(start, delta, min, max)` (Python-mirrored
  in `tests/unit/test_studio_resize_clamp.py`) plus **delegated** `pointerdown`
  handling on `document` (so HTMX-injected/CSS-toggled dividers need no
  re-wiring): `setPointerCapture` + `body.studio-resizing`, write a CSS var on
  the container during move, persist on up.
- Two dividers: **player ↔ prompt** lives in `.studio-right` as a real grid
  track — `under` → rows `var(--studio-player-h,320px) 6px minmax(0,1fr)`
  (row-resize); `right` → columns `var(--studio-player-w,…) 6px 1fr`
  (col-resize); the handler picks axis/var from `store.layout`. **cur ↔ cmp**
  lives in `.studio-compare-row`, cur gets `flex: 0 0 var(--studio-cmp-cur,50%)`,
  gated by `:has(.cmp-card)` so it only applies / shows when comparing.
- **Nested**, not a flat 3-track grid: the right layout reads as three columns
  (player | cur | cmp) but the left divider trades player vs the cur+cmp block
  and the middle divider trades cur vs cmp. This reuses the existing
  player-slot/compare and cur/cmp structure with zero DOM restructure.
- Sizes persist in `localStorage['studio.layoutPrefs']` (`playerH`/`playerW`/
  `cmpCur`); the existing first-paint inline script stamps them as CSS vars on
  `.studio-right`/`.studio-compare-row` before Alpine boots (no flash).
- Affordance: `.studio-resizer` shows row-/col-resize cursor + a grip pill that
  brightens to `--accent` on hover.

**`setLayout` change:** previously `setLayout('right')` force-closed compare
("compare needs the wide stacked layout"). That made the three-column right
layout impossible, so the close-on-right was removed; compare now survives
layout switches (guarded by `tests/unit/test_studio_setlayout_keeps_compare.py`).

## Consequences

- No new frontend dependency; the no-Node stack (ADR 0001) holds.
- Reuses `Alpine.store('studio')` (no `_x_dataStack`, ADR 0048) and the single
  HTMX↔Alpine lifecycle (the resizer is plain delegation, touching neither).
- The player slot moved from a content-sized `auto` row to a fixed/var grid
  track. Two follow-on fixes were required and are guarded:
  - `.studio-player` must be a flex column so `.viewer` flexes above the
    transport instead of `height:100%` overflowing it
    (`tests/unit/test_studio_player_layout.py`).
  - The `no-player` grid must collapse to a **single** `1fr` track: with the
    slot + divider `display:none`, a `0 0 1fr` template stranded the lone
    `.studio-compare` item in the first 0-size track and collapsed the prompts
    (`tests/unit/test_studio_no_player_grid.py`).
- `:has()` is used to gate the cmp divider/flex-basis — acceptable for the
  single-operator Chrome target.
