# 0083. HTMX-only wiring for fetch-injected subtrees that own an x-data root

**Date:** 2026-06-15
**Status:** Accepted (refines [0048](./0048-alpine-store-not-x-data-stack-for-shared-state.md))
**Lifespan:** Invariant

## Context

ADR 0048 made `htmxAlpine.js` the single owner of HTMX↔Alpine lifecycle
calls, exposing `reinit(el)` = `Alpine.initTree(el)` + `htmx.process(el)` for
subtrees injected after page load.

`reinit` is correct for two of the three injection shapes we use, but wrong
for the third:

1. **HTMX `hx-swap` (e.g. version-picker `outerHTML`)** — Alpine's
   MutationObserver does not reliably re-init the swapped x-data subtree, so
   the explicit `initTree` is needed (the `htmx:afterSwap` handler).
2. **`fetch()`+innerHTML of content with NO x-data root** (hosted-mode
   directives bound to an ancestor scope) — needs `process` for HTMX; the
   observer handles the Alpine side.
3. **`fetch()`+innerHTML of content that HAS its own x-data root** (the
   compare card injected by `openCompare`, `x-data="studioPromptCard('cmp',…)"`).
   Alpine's MutationObserver **reliably** initializes a freshly-inserted
   x-data root on its own. Calling `initTree` here too binds every directive
   on that subtree **twice**.

Shape 3 was being wired with `reinit`. The double-bound `@click` on the
compare **Diff** toggle flipped `$store.studio.compareDiff` twice per click —
straight back to its original value — so the button looked dead. It worked
after a page reload, where the card is server-rendered and Alpine initializes
it exactly once at startup. Measured directly in-browser: the diff button had
two `@click` cleanups and one click produced two writes to `compareDiff`.

## Alternatives

- **Make `reinit` skip already-initialized roots:** Alpine v3's `initTree`
  has no public "init once" guard; reimplementing one is fragile.
- **Stop injecting via innerHTML, swap via HTMX instead:** larger change to
  `openCompare`, and shape 1 shows HTMX-swapped x-data roots have their own
  observer-unreliability caveat.
- **Add an HTMX-only wiring helper (chosen):** a second `htmxAlpine` method,
  `wireHtmx(el)` = `htmx.process(el)` only, for shape 3. Lets Alpine's
  observer do the single Alpine init.

## Decision

Add `htmxAlpine.wireHtmx(el)` (HTMX `process` only, no `initTree`) and use it
in `openCompare` for the injected compare card. Picking the helper:

- HTMX-swapped subtree, or injected content with **no** x-data root → `reinit`.
- Injected content that **owns** an x-data root → `wireHtmx`.

`htmxAlpine.js` remains the single file allowed to call `initTree(` /
`.process(` (guard: `test_htmx_alpine_single_lifecycle.py`). A new guard,
`test_studio_compare_card_single_init.py`, pins `openCompare` to `wireHtmx`.

## Consequences

+ The compare Diff toggle (and every other directive on the injected cmp
  card) is bound once and works whether compare is reached by clicking or by
  reload.
- Two helpers now exist with a rule for choosing between them; the rule is
  documented in `htmxAlpine.js` and enforced by the new guard test.
- Other `reinit` call sites that inject x-data roots via innerHTML
  (`studioSets.createSet`, possibly `studioNav.switchSource`) may carry the
  same latent double-init; not changed here (out of scope, unreported). They
  should migrate to `wireHtmx` if a duplicate-binding symptom surfaces.
