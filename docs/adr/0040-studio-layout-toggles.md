# 0040. Studio layout toggles (list / player / prompt-output position)

**Date:** 2026-05-28
**Status:** Accepted
**Lifespan:** Feature

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
