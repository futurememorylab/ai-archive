# 0089. Manual `innerHTML`/`insertAdjacentHTML` into a live Alpine tree uses `wireHtmx`, not `reinit`

**Date:** 2026-06-16
**Status:** Accepted

## Context

The studio navigator injects HTML into a live, Alpine-managed DOM tree in
several places that don't go through HTMX's own `hx-swap` pipeline:

- `studioNav.switchSource()` replaces the set-list body
  (`[data-studio-nav-body]`) with a freshly-fetched `_studio_set_list.html`.
- `studioSets.createSet()` appends a new `_studio_set_card.html` to the list.

After each insertion the code called `window.htmxAlpine.reinit(el)`, which runs
**both** `Alpine.initTree(el)` and `htmx.process(el)`.

The problem: Alpine v3's `MutationObserver` already initializes nodes inserted
into a tree it owns — including nodes with **no `x-data` of their own** whose
directives bind to an ancestor scope (e.g. a set card's
`@click="toggle(id)"` resolving against the parent `studioSets` component).
The extra `Alpine.initTree()` in `reinit` then binds every directive a
**second** time. A double-bound `@click` fires its handler twice, so a toggle
flips to its original value and looks dead:

- After switching source tabs, each set checkbox's `toggleSet` ran twice
  (select-all → deselect-all → net nothing), so selection looked broken on
  every tab except the first-painted one.
- A newly-created set's expand arrow ran `toggle` twice (expand → collapse),
  so the set couldn't be opened — and thus "+ Add from archive" couldn't be
  reached — until a full page reload re-rendered it single-bound.

Both were verified empirically in the browser (`toggleCalls === 2` per click).
This is the same failure mode the `htmxAlpine.wireHtmx` helper already
documented for the compare card (`openCompare`), but the two studio injection
sites still used `reinit`.

## Alternatives

- **Keep `reinit`, debounce/guard the directives** — fragile; fights the
  framework and leaves the double binding in place for any new directive.
- **Stop relying on the MutationObserver; bind only via `initTree`** — would
  require suppressing Alpine's observer for these subtrees, which Alpine v3
  does not cleanly support.
- **Route everything through HTMX `hx-swap`** instead of manual `innerHTML` —
  larger refactor; `switchSource`/`createSet` are deliberately JS-driven
  (fetch + insert + toast) and don't map cleanly onto a single swap target.

## Decision

For content inserted by manual `innerHTML` / `insertAdjacentHTML` into a tree
Alpine already owns, call **`window.htmxAlpine.wireHtmx(el)`** (HTMX wiring
only) and let Alpine's `MutationObserver` bind the directives exactly once.

Rule of thumb, encoded in the `htmxAlpine.js` doc comments:

- **`wireHtmx(el)`** — injected content whose directives are bound by Alpine's
  observer on insert: a subtree with its **own** `x-data` root (compare card),
  **or** a no-`x-data` subtree inserted into a live Alpine tree whose
  directives target an ancestor scope (set card, set list).
- **`reinit(el)`** — only when Alpine's observer will **not** have run, i.e.
  the Alpine state genuinely needs an explicit `initTree`. In practice the
  studio paths don't need this; prefer `wireHtmx` and reach for `reinit` only
  with evidence (a directive that is provably unbound after insert).

`htmx.process` stays in both helpers because HTMX's processing is idempotent
and manual insertion isn't guaranteed to be picked up by HTMX's own observer.

## Consequences

- `switchSource` and `createSet` now bind each directive once; tab-switch
  selection and immediate open-of-new-set both work without a reload.
- The single-lifecycle invariant is unchanged: `Alpine.initTree(` /
  `htmx.process(` still appear only in `htmxAlpine.js`
  (`tests/unit/test_htmx_alpine_single_lifecycle.py`).
- New manual-injection sites must choose `wireHtmx` by default. If a future
  contributor reaches for `reinit` on content inserted into a live Alpine
  tree, the symptom is a "dead" toggle/checkbox that only works after reload —
  the tell is a handler firing an even number of times per click.
