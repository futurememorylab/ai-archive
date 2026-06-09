# 0061. Two-mode popover menu module + design-language enforcement guard

**Date:** 2026-06-09
**Status:** Accepted

Spec: `docs/specs/2026-06-09-popover-menu-module-and-design-language-guard-design.md`.

## Context

The 2026-05-27 UI consolidation (ADR-less Phase 1) deepened the *primitives*
(`.btn`, fields, `fmtTimecode`/`fmtBytes`) but shipped as documentation only.
Two weeks later the *composite* patterns had re-grown: the popover menu was
hand-rolled seven times under seven class vocabularies (`version-menu`,
`annotate-menu`, `tmpl-menu`, `hdr-tmenu`, `pc-vmenu`, `model-menu`,
`actions-menu`), each re-writing the same open/click-outside/escape wiring and
the same absolutely-positioned panel CSS. Every *other* removed pattern in this
repo has a CI guard (`test_no_x_data_stack`, `test_templates_shared`, the
erosion gate); the design language did not — which is precisely why it eroded.

Two design calls were load-bearing enough to record.

## Alternatives

**Popover open-state seam.**
- *Always self-contained* (`x-data="popover()"` on every menu): re-introduces
  the documented `_studio_header.html` hazard where a nested `x-data` shadows
  the `studioPage` scope so `focusedClipId` reads `undefined`.
- *Custom `x-popover` directive*: never shadows, but a hand-written Alpine
  directive is machinery we own and test forever — too much for the payoff.

**Guard rollout.**
- *Clean-slate*: migrate all seven menus and enable the guard with a clean
  allow-list in one PR — one sprawling, high-collision diff.

## Decision

**Two-mode popover.** One `Alpine.data("popover")` factory (`static/popover.js`)
exposing `open`/`toggle()`/`close()`, plus a `ui.menu` macro that selects the
mode by a `state=` argument: *standalone* emits `x-data="popover()"` for leaf
menus; *hosted* emits **no `x-data`** and binds the open/close/click-outside/
escape wiring to a caller-owned flag, so a menu nested in a larger component
never shadows the parent scope. Item look is one `.menu`/`.menu-item` vocabulary
with a `ui.menu_item` macro (link / form-POST / action variants); the async
annotate menu and the model selection picker reuse the chrome + classes but keep
their own bodies (hybrid, not fully declarative).

**Ratchet guard.** `tests/unit/test_design_language_guard.py` fails CI on any
class token ending in `btn`/`menu` outside the canonical bases and a
grandfathered allow-list, and on hand-rolled timecode/byte formatters in JS. The
allow-list ships full and is burned down one entry per migration PR until only
the intentional exceptions remain.

Scope is the popover module + guard only; modals and the clip media card are
separate deepenings the guard is built to cover later.

## Consequences

- The popover open/escape/click-outside logic has one place to test and fix;
  the `studioPage`-shadowing hazard is removed structurally, not commented
  around.
- New bespoke `*-btn`/`*-menu` classes and re-implemented formatters fail CI,
  so the library stays load-bearing instead of advisory.
- Delivered incrementally: PR 1 lands the module + guard + two pilot menus
  (prompt version picker, studio version chip) and deletes their vocabularies;
  PR 2 migrates the remaining five menus, sweeps the `player.js` timecode onto
  `fmtTimecode`, and shrinks `GRANDFATHERED` to the permanent exceptions.
- The `state=`-selected mode is a small interface knob callers must learn; the
  design-language doc (§8) carries the standalone / hosted / bespoke-trigger
  examples.
