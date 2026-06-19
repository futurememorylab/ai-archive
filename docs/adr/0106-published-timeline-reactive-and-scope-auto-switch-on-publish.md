# 0106. Published (and draft) timeline bands are reactive; scope auto-switches to Published after a confirmed publish

**Date:** 2026-06-18
**Status:** Accepted

## Context

Operator report: after applying/publishing a draft, the timeline markers stay
**blue (draft)** instead of turning **orange (published)** — even once the
write-back lands on CatDV. (Originally filed as the last comment on issue #43.)

Root-cause walk of the render path (`clip_detail.html` rows → `_player_overlay.html`
→ `review.js` publish/poll/refresh):

- **R1 — bands are mutually exclusive by scope.** `clip_detail.html` gates the
  published band with `x_show="scope === 'published'"` and the draft band with
  `x_show="scope === 'draft'"` (the gate was added 2026-06-02, commit `144e1a57`,
  superseding the earlier split-band design that showed both at once). After
  Apply the view stays in `draft` scope, so only the blue band is ever visible.
- **R2 — the draft band never drains.** `_build_draft_for_clip` /
  `build_draft_view` return every non-rejected item (carrying `applied_at` /
  `synced_at` but never excluding synced ones), so `draftMarkers` keeps the
  just-published markers and the blue band keeps showing them.
- **R3 — the published band was static server HTML.** The published `.range`
  bars were positioned with server-rendered Jinja (`style="left: {{ … }}"`), so
  `review.js _refreshPublished()` (which updates the Alpine `markers` array)
  could not move them. They only refreshed on a full page reload — and
  `location.reload()` after a CRUD action is banned (frontend discipline).

Net effect: markers stuck in the only visible (blue) band, with no no-reload
path to the orange band.

## Alternatives

- **Restore the split band (both visible in draft mode).** Rejected — busier
  timeline, and still needs R3's reactivity work; the operator's mental model is
  "after publish it's published," i.e. one band.
- **Recolor synced draft markers orange in place.** Rejected — semantically
  muddy (the "draft" band rendering published colors) and leaves R1/R3 latent.
- **Full reload after sync.** Rejected — `location.reload()` after CRUD is
  forbidden; loses player position/state.

## Decision

- **Auto-switch to Published after a confirmed sync.** `review.js _pollSync()`
  sets `scope = "published"` in its settled-OK branch, right after
  `_refreshPublished()`, so the user lands on the orange band once the write-back
  is actually on CatDV (not merely enqueued).
- **Make the published band reactive — opt-in per row.** `_player_overlay.html`
  renders a row's ranges via an Alpine `<template x-for="(m, idx) in {row.x_for}">`
  with a reactive `:style` **only when the row sets `x_for`**; otherwise it keeps
  the static server loop. `clip_detail.html`'s published row opts in with
  `"x_for": "markers"`. Studio (which inits `player(…, [], [])` and renders its
  bands from server `rows`) does **not** set `x_for`, so it is untouched — this
  avoids blanking studio's markers and avoids parallel-evolving a second renderer.

The draft band intentionally still lists applied/synced items (the aside shows
their "applied ✓" status); auto-switching to Published hides that band, so R2 is
not visible and is left as-is rather than filtering the shared `draftMarkers`
array (which also feeds the aside).

## Consequences

- Publishing a draft now visibly moves the markers to the orange Published band
  without a reload, matching the operator's expectation.
- The published timeline band is reactive to the Alpine `markers` array on the
  one surface that refreshes it (clip detail); studio stays static and unchanged.
- Guard: `tests/integration/test_player_overlay_partial.py` pins both contracts —
  `x_for` rows render the reactive Alpine loop (no static `left:` for that band),
  and rows without `x_for` keep the static server style. Full suite green (1753).
- Not addressed here: synced items still appear in the draft band if the user
  manually switches back to `draft` scope (R2). Acceptable while Published is the
  post-publish landing band; revisit if the split-band view returns.

## Addendum (2026-06-19): the draft band had the inverse bug

Operator report: after an **annotation run finishes**, the new draft markers
don't appear on the timeline until a page reload.

Same root-cause family as R3, on the other band. The draft band was a
server-rendered `{% for m in row.ranges %}` loop, so each `.range` div was baked
in for the draft markers that existed **at page load**. Its `x-show` / `:style`
reactively reflected *edits* to those existing markers (via `_draftItem(id)`),
but a marker with no page-load div had nowhere to render. `clipAnnotate.swapDraft`
→ `reviewMixin.refreshDraft()` repopulates the live `draftMarkers` array, yet the
band — keyed to page-load membership — could not show the additions. Worse, the
common path opens a clip with **no** draft (`ranges = []`), so a first annotation
run rendered zero bars until reload.

**Decision:** extend the same opt-in. `_player_overlay.html` renders a reactive
`<template x-for="m in {row.x_for}" :key="m.item_id">` for the draft band when a
row sets **both** `x_for` and `draft`, carrying the full draggable/editable markup
(item_id, editing class, in/out handles) bound to the live loop item rather than a
baked-in literal id. `clip_detail.html`'s draft row opts in with
`"x_for": "draftMarkers", "draft": True`. Membership *and* position are now
Alpine-driven, so freshly-annotated markers appear immediately and deletes/restores
track live. The static draft sub-branch stays as the no-`x_for` fallback (parity
with the static published loop); studio is unaffected.

Guard: `test_player_overlay_partial.py::test_draft_range_reactive_when_x_for_set`
pins the reactive draft contract (x-for over `draftMarkers`, drag bound to
`m.item_id`, no page-load-frozen `_draftItem(42)` literal). Full suite green (1754).
