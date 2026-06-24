# 0108. Annotation column follows playback: one active-predicate drives timeline + column, comfort-band nearest-edge auto-scroll

**Date:** 2026-06-22
**Status:** Accepted
**Lifespan:** Feature

## Context

On the clip-detail page the timeline already highlights the marker segment
under the playhead (the orange `.range.active` band, driven by
`player.js::isMarkerActive(m)`). The annotation column to the side lists the
same markers as cards, but nothing connected playback to that list: as a clip
played the active card was neither highlighted nor scrolled into view, so on a
clip with enough markers to overflow the column the operator had to hunt for
"which one is playing now" by hand (#80).

The ask: when a clip plays, highlight the active marker **in the column too**,
and auto-scroll the column to keep it visible — but without the list fighting
the user or jumping around distractingly.

The column is shared chrome: `_anno_panels.html` renders the published list on
the clip page (`review_mode=True`) **and** in Prompt Studio
(`review_mode=False`), where there is no `player` Alpine scope. Draft markers
render from a separate partial (`_anno_draft.html`), clip-page-only.

## Alternatives

- **A second "is active" predicate / new Alpine store for column state.**
  Rejected — duplicates the timeline's logic and risks the two surfaces
  disagreeing about which marker is active. Reuse `isMarkerActive` so there is
  exactly one source of truth, and highlighting stays purely reactive (no new
  state to keep in sync).
- **Center the active card (scrollIntoView 'center').** Rejected — centers on
  every marker change, so a card already comfortably on screen still yanks to
  the middle. Too much movement; reads as jitter during normal playback.
- **Always smooth-scroll.** Rejected — a far timeline seek would slowly glide
  across many viewports. Jumps longer than one viewport snap instantly
  (`behavior:'auto'`); small corrections glide (`'smooth'`).
- **No manual-scroll handling.** Rejected — if the operator scrolls the list to
  read ahead, auto-follow would immediately yank it back. Unusable.

## Decision

- **Reuse `isMarkerActive(m)` as the single active-predicate** for both the
  timeline and the column. The column card binds `:class="{ active: ... }"` to
  the same call; highlighting needs no new state.
- **Comfort-band, nearest-edge, minimal-movement scroll.** The viewport is inset
  by a 20% margin top and bottom; if the active card is already inside that band
  the list does not move at all. Otherwise it scrolls the *minimum* needed to
  bring the card to the nearest band edge — not to center. This is why an
  already-visible marker (e.g. the first one) never scrolls. Tuning constants
  (verbatim from the spec): **20%** band margin, **4000 ms** manual-scroll
  resume, **1 viewport** smooth-vs-instant threshold.
- **Anchor = first active card in `in_secs` order.** Markers arrive ascending by
  `in_secs` (clip-detail view-model guarantee), so when segments overlap the
  earliest-starting active marker wins — stable, predictable behaviour. The pure
  helper `annoActiveAnchorIndex` returns `-1` in a gap (playhead between
  markers), and the driver then leaves the list untouched.
- **Manual-scroll pause.** A user scroll of the column suspends auto-follow for
  4 s, then resumes. Our own programmatic scroll is flagged
  (`_selfScrolling`) so it isn't mistaken for the user. An intentional `seek()`
  (timeline click or card click) cancels the pause immediately so the list snaps
  to the target.
- **Scope-gated to the clip page.** The published follow hooks (`data-anno-marker`,
  `data-in`/`data-out`, the `isMarkerActive` binding) render only under
  `{% if review_mode %}`, so Studio — which shares the partial but has no
  `player` scope — stays clean (an `isMarkerActive` reference there would break
  `Alpine.initTree`). The driver reads only *visible* cards (`offsetParent !==
  null`), so the hidden scope/tab (published vs draft) is naturally excluded.
- **Highlight via inset box-shadow + token tint, not a border.** `.marker.active`
  / `.ri-card.ri-marker.active` use `box-shadow: inset 3px 0 0 0 var(--accent)`
  plus a `color-mix` tint so the highlight never reflows the card, and uses the
  design token (no raw hex — design-language guard).
- **Pure helpers + Python text-scan guards instead of a JS test runner.** The
  comfort-band arithmetic and anchor selection live in two `this`-free, DOM-free
  module functions (`annoComputeScroll`, `annoActiveAnchorIndex`). This repo has
  no node tooling (ADR 0001), so `tests/unit/test_anno_follow_playback.py` pins
  the wiring contract (helper presence, DOM attrs/bindings, CSS rule, Studio
  exclusion, driver wiring) by text scan; behaviour is covered by the spec's
  manual acceptance flows.

## Consequences

- During playback the active marker lights up in both the timeline and the
  column, and the column keeps it visible with the least movement needed —
  no jitter when it's already on screen, instant catch-up on far seeks.
- A new "is active?" surface must reuse `isMarkerActive`; adding a parallel
  predicate would reintroduce the drift this avoided.
- Studio's read-only list is unaffected (guarded by
  `test_anno_panels_review_mode.py` + the new Studio-exclusion test).
- The feature is pure client-side view behaviour: no new Alpine store, no
  `location.reload`, no network calls.
