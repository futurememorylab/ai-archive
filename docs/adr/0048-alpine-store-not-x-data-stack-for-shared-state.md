# 0048. Alpine.store (not _x_dataStack) for shared studio state; one HTMX↔Alpine lifecycle helper

**Date:** 2026-05-31
**Status:** Accepted

## Context

The studio page shared cross-component state (focused clip, active/compare
prompt version, model, run-button state machine, layout prefs) lived on the
`studioPage` Alpine component (`x-data` on `.studio-page`). Sibling
components — `modelPicker`, `studioPromptCard`, `archivePicker`, the
`cmpDiff` line-diff, the global `htmx:afterSwap` handler, and the
`window.studio` vanilla shim — reached that state via
`document.querySelector('.studio-page')._x_dataStack[0]`. `_x_dataStack`
is an **undocumented Alpine v3 internal**: it is not part of the public API
and can change or vanish on any Alpine upgrade. There were 8 such reach-ins
across `studio.js` + `studio-diff.js`.

Separately, the lifecycle wiring that re-initialises DOM injected after page
load (`Alpine.initTree(el)` to re-scan directives, `htmx.process(el)` to
re-wire `hx-*`) was scattered: one global `htmx:afterSwap` handler plus
four ad-hoc `initTree`/`process` call sites across `studio.js` and the
studio store. This made "why did this swapped node come alive (or not)?"
impossible to reason about in one place, and was the source of intermittent
"dead clicks" on HTMX-injected subtrees.

This is tier-3 tasks T3-B1 + T3-B2.

## Alternatives

- **Keep `_x_dataStack` reach-ins** — works until the next Alpine bump
  silently breaks every studio cross-component read. Rejected: depending on
  a private internal is the defect.
- **Event bus / `CustomEvent` for all shared state** — decouples but turns
  simple reads (`store.focusedClipId`) into subscribe/dispatch ceremony and
  scatters state across listeners. Reserved only for the one genuine
  cross-component *call* (page → player seek).
- **Props drilling / `$root`** — Alpine `$root` resolves to the nearest
  enclosing `x-data`, which for a nested component is itself, not the page;
  that shadowing is exactly why the reach-ins existed. Doesn't solve it.
- **Move every binding to `$store.studio` and delete `studioPage`** — the
  cleanest store-purist option, but rewrites every studio template binding.
  With no JS test runner in this repo, untested template churn is high-risk.
  Rejected in favour of minimal template churn.

## Decision

- **Shared studio state lives in `Alpine.store('studio')`** (new
  `static/studioStore.js`), the documented Alpine pattern for
  cross-component state. `studioPage` stays the `x-data` on `.studio-page`
  but becomes a **thin delegator**: getter/setter/method pass-throughs to
  `$store.studio`, so every existing template binding keeps resolving with
  near-zero template churn. All sibling readers use `Alpine.store('studio')`
  instead of `_x_dataStack[0]`.
- **The one page→player call** (`seekFocusedClip`) uses a public DOM
  `CustomEvent('studio-seek')` that the player root handles via
  `x-on:studio-seek="seek($event.detail)"` — no private-internal access.
- **One HTMX↔Alpine lifecycle owner** (`static/htmxAlpine.js`): a
  `window.htmxAlpine.reinit(el)` helper (`initTree` + `process`, the single
  place those are called) for JS-injected subtrees, plus the one global
  `htmx:afterSwap` listener for studio's `.selected`/version reconciliation.
  Callers that inject via `fetch()`+`innerHTML` call `reinit(el)`.

## Consequences

- Studio shared state survives Alpine upgrades; the private-internal
  dependency is gone. Enforced in CI by `tests/unit/test_no_x_data_stack.py`
  (source-grep over `static/` + `templates/`, excluding vendored Alpine).
- `Alpine.initTree(` / `htmx.process(` exist in exactly one file, enforced
  by `tests/unit/test_htmx_alpine_single_lifecycle.py`.
- Delegating getters keep reactivity: Alpine stores are reactive, and a
  getter that reads a store field inside a binding's effect tracks the
  dependency, so `x-text`/`:class` re-evaluate on store mutation (including
  the 1 Hz run-button ticker now living on the store).
- Cost: the `studioPage` delegator layer is boilerplate that can drift from
  the store's fields. Accepted as the minimal-template-churn trade-off given
  the absence of a JS test runner; live behaviour is verified by manual
  click-through (the spec's tier-3 acceptance flows).
- No JS unit-test coverage exists for the live reactivity; the grep guards +
  server-rendered HTML tests + manual acceptance are the safety net.
