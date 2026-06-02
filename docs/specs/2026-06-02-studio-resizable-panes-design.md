# Studio resizable panes — design

**Date:** 2026-06-02
**Status:** Approved (pending spec review)

## Goal

Let the user drag the borders between the Studio panes to resize them, and
remember the sizes. Concretely:

1. The border between the two prompt versions (cur ↔ cmp compare cards) is
   draggable; each card resizes.
2. The border between the player and the prompt area is draggable; the player
   and the prompt area resize.
3. In the **right** layout the user can place Player, Prompt (cur) and Compare
   (cmp) next to each other as three columns and resize them by dragging the
   borders between them.
4. A draggable border shows a resize cursor + a grip "icon" on mouse-over so
   it's discoverable.

Out of scope (YAGNI): video-list width resize, min/max chrome, double-click to
reset, a separate compare button (the existing `+ Compare` already opens the
cmp column).

## Current layout (what we build on)

- `.studio-body` — grid `[320px video-list | 1fr studio-right]`; list + player
  have header show/hide toggles; `no-list` / `no-player` modifier classes.
- `.studio-right` — grid with two children: `.studio-player-slot` and
  `.studio-compare`. Header layout toggle flips **under** (rows: player on top,
  prompt below) ↔ **right** (columns: player left, prompt right) by swapping
  grid rows↔columns — no DOM moves.
- `.studio-compare` → `.studio-compare-row` (flex) → cur `.studio-prompt-card`
  + `.cmp-slot` (`display:contents`, the JS innerHTML target) → cmp card.
- Player height is fixed (`.studio-player .viewer { height: 320px }`); cards are
  `flex: 1 1 0`.
- Layout prefs (`showList`, `showPlayer`, `layout`) persist in
  `localStorage['studio.layoutPrefs']`; a first-paint inline script in
  `studio.html` stamps modifier classes before Alpine boots (no flash).

## Approach

Hand-rolled splitters (no Node dependency — ADR 0001). A thin divider element
sits between two panes; dragging it updates a CSS custom property that drives
the container's grid/flex track sizes. Two **nested** resizers cover both
layouts:

### Resizer 1 — Player ↔ Prompt (in `.studio-right`)

Add a `.studio-resizer.is-player` element as a real grid track between
`.studio-player-slot` and `.studio-compare`, so `.studio-right` is a 3-track
grid `[player] [divider] [compare]`:

- **under**: `grid-template-rows: var(--studio-player-h, 320px) 6px minmax(0,1fr); grid-template-columns: 1fr;`
  → divider is **row-resize** (drag vertical, changes `--studio-player-h`).
- **right**: `grid-template-columns: var(--studio-player-w, 1fr) 6px 1fr; grid-template-rows: 1fr;`
  → divider is **col-resize** (drag horizontal, changes `--studio-player-w`).

The drag handler reads `layout` (`Alpine.store('studio').layout`) to pick the
axis + var. `--studio-player-w` is stored in px and applied to the player
track; `1fr` on the compare track lets compare absorb the remainder.

### Resizer 2 — Prompt (cur) ↔ Compare (cmp) (in `.studio-compare-row`)

Add a `.studio-resizer.is-cmp` element between the cur card and `.cmp-slot`.
It is **always rendered** in the template; CSS hides it and the cur flex-basis
only applies when a cmp card is present, gated with `:has()`:

```css
.studio-compare-row:not(:has(.cmp-card)) .studio-resizer.is-cmp { display: none; }
.studio-compare-row:has(.cmp-card) > .studio-prompt-card[data-side="cur"] {
  flex: 0 0 var(--studio-cmp-cur, 50%);
}
```

So when comparing, cur is `var(--studio-cmp-cur, 50%)` wide, the divider shows,
and cmp fills the rest; when **not** comparing the divider is hidden and the cur
card keeps its default `flex: 1 1 0` (full width) — unchanged from today.
(`:has()` matches through the `display:contents` `.cmp-slot` because it's a DOM,
not render-tree, query.) Always **col-resize**. This means `openCompare` /
`closeCompare` need **no** changes — they already add/remove the `.cmp-card`,
which flips the CSS.

### Net effect per layout

- **under**: player on top with draggable height; below it the cur|cmp row with
  a draggable width split.
- **right**: three columns — player | cur | cmp — with two dividers. The left
  divider trades player vs the (cur+cmp) block; the middle divider trades cur
  vs cmp. (Nested behavior, per approved design — not a flat 3-track grid.)

## Components / files

- **`backend/app/static/studioResize.js`** (new) — the resizer module:
  - `clampSize(start, delta, min, max)` — pure helper, returns the new size
    clamped to `[min, max]`. Mirrored by a Python test for parity.
  - Plain pointer-event handling via **delegation** on `document` (one
    `pointerdown` listener matching `.studio-resizer`), so dividers that are
    HTMX-injected or CSS-toggled need no re-wiring. On `pointerdown` →
    `setPointerCapture` on the divider + set `dragging` (adds `body.studio-resizing`
    that disables text selection and forces the resize cursor); `pointermove`
    → compute delta from the pointer, `clampSize`, write the CSS var on the
    container; `pointerup` → release capture, clear `dragging`, persist via the
    store.
  - Reads/writes sizes through `Alpine.store('studio')` (new `playerH`,
    `playerW`, `cmpCur` fields + a `saveLayoutPrefs()` extension).
- **`backend/app/templates/pages/studio.html`** — add the player↔compare
  divider element inside `.studio-right`; extend the first-paint inline script
  to read `playerH`/`playerW`/`cmpCur` from prefs and stamp them as CSS vars on
  `.studio-right` / `.studio-compare-row`; load `studioResize.js`.
- **`backend/app/templates/pages/_studio_compare.html`** — add the cur↔cmp
  divider element between the cur card and `.cmp-slot`, **always rendered**
  (CSS hides it when not comparing, via `:has(.cmp-card)`).
- **`backend/app/static/studioStore.js`** — add `playerH`, `playerW`, `cmpCur`
  to state + hydrate; extend `_saveLayoutPrefs()` to include them.
- **`backend/app/static/studio.js`** — no change needed for the cmp divider
  (CSS-gated). `setLayout()` unchanged (the player divider's axis follows
  `layout` via CSS + the handler).
- **`backend/app/static/app.css`** — `.studio-resizer` base (size, cursor per
  layout, hover grip + accent highlight, hidden when neighbor hidden); grid/flex
  track rules using the new vars; `body.studio-resizing` (no-select + cursor).

## Data flow

1. First paint: inline script reads `studio.layoutPrefs` → stamps
   `--studio-player-h/-w` on `.studio-right` and `--studio-cmp-cur` on the
   compare row (if present). No flash.
2. Alpine boots; store hydrates the same size fields.
3. User drags a divider → handler updates the CSS var live → on release,
   `saveLayoutPrefs()` writes the new sizes to localStorage.
4. Layout toggle (`under`↔`right`) changes which var the player divider drives;
   each layout keeps its own size (`playerH` for under, `playerW` for right).

## Error handling / edge cases

- Pointer capture ensures the drag continues if the pointer leaves the divider.
- `clampSize` keeps panes within `[min, max]` (e.g. player ≥ 160px and ≤ 70% of
  the container; cmp split within 20%–80%) so a pane can't collapse to 0 or
  push another offscreen.
- Player hidden (`no-player` / `showPlayer` false) → player divider hidden via
  CSS; compare fills.
- Not comparing → cmp divider absent; cur full width.
- Corrupt/missing prefs → fall back to defaults (320px / 1fr / 50%).

## Testing

- **Unit (parity):** `tests/unit/test_studio_resize_clamp.py` mirrors
  `clampSize`; verified against the JS by running both over shared fixtures
  (same pattern as the word-diff mirror).
- **Markup guards** (`tests/integration/test_studio_*`):
  - The player↔compare divider element is present in `studio.html` with its
    `data-studio-resizer="player"` hook.
  - The cur↔cmp divider (`data-studio-resizer="cmp"`) is present in the compare
    partial (always rendered; CSS-gated by `:has(.cmp-card)`).
- **CSS guard** (`tests/unit/test_studio_resizable_css.py`): `.studio-right`
  tracks use `--studio-player-h/-w`; the `:has(.cmp-card)` cur rule uses
  `--studio-cmp-cur`; `.studio-resizer` declares a resize cursor; the cmp
  divider is hidden via `:not(:has(.cmp-card))`.
- **JS guards:** `studioResize.js` passes `node --check`; the single
  HTMX↔Alpine lifecycle and `_x_dataStack`-free guards still pass.
- **Manual acceptance** (below), driven in Chrome.

## Manual acceptance flows

1. **Resize cur ↔ cmp (under layout).** Open `/studio?prompt_id=<P>&version_id=<draft>&compare_version_id=<other>` (default `under` layout). Expect cur and cmp side by side with a divider between them. Hover the divider → cursor becomes `col-resize` and a grip highlights. Drag left → cmp widens, cur narrows; drag right → reverse. Neither card collapses to 0.
2. **Resize player ↔ prompt (under layout).** Focus a clip so the player shows. Hover the horizontal border between the player and the prompt area → cursor becomes `row-resize` + grip. Drag down → player taller, prompt shorter; drag up → reverse. The prompt area still scrolls (cmp pane scroll from the previous fix still works).
3. **Three-column right layout.** Click the layout toggle to `right`. Expect Player | Prompt (cur) | Compare (cmp) as three columns with two dividers. The player↔prompt divider now shows `col-resize`. Drag it → player vs the cur+cmp block resizes. Drag the cur↔cmp divider → cur vs cmp resizes. All three remain usable.
4. **Persistence across reload.** After dragging in flows 1–3, reload the page. Expect the dragged sizes to be restored on first paint (no visible jump to defaults then to saved).
5. **Hidden-neighbor cleanup.** Toggle the player off (header player toggle). Expect the player↔prompt divider to disappear and the prompt area to fill. Toggle it back on → divider returns. Close compare (× on the cmp card) → the cur↔cmp divider disappears and cur fills the width.
