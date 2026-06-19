# 0107. Draft scope shows the published band above the draft band (gated split), and timecode labels stay legible on any band

**Date:** 2026-06-19
**Status:** Accepted

## Context

Two operator reports on the clip-detail timeline while in **draft** scope:

1. **Timecode labels are unreadable.** The quintile `.tc-labels` sit at the
   bottom of the track in `var(--text-4)` with no backdrop. Over the blue draft
   band (and the orange published band) the dark text disappears.
2. **"I want to see the published markers above the draft markers, like in
   prompt studio — there's only free space for it."** ADR 0106 (R1) made the two
   bands mutually exclusive by scope (`x_show="scope === 'published'"` on the
   published row, `…'draft'"` on the draft row). The split-band CSS
   (`.detail.is-draft … :not(.draft-ranges){bottom:50%}` + `.draft-ranges{top:50%}`)
   still existed, so in draft scope the top half was reserved but **empty** — the
   published band was `x-show`-hidden.

Compounding nuance found while reproducing on `/clips/889070` (an unpublished
draft) vs `/clips/888894` (the one live clip): **most clips under review are
unpublished**, so naively un-hiding the published band would still leave the top
half empty for them — the very "free space" complaint — just no longer hidden.

## Alternatives

- **Just remove the scope gate (always split in draft).** Rejected — leaves the
  empty top strip on every unpublished clip, which is the common review case.
- **Keep bands mutually exclusive; add a separate "show published too" toggle.**
  Rejected — extra control for what the operator wants as the default.
- **Recolour in place / single band.** Rejected — the operator explicitly wants
  the two-band stack, and a two-colour split reads more clearly.

## Decision

- **Published band shows in both scopes.** Drop the `x_show` gate on the
  published row in `clip_detail.html`; the draft row stays gated to draft scope.
- **Split only when published markers exist.** `.detail` gains a reactive
  `has-published-markers` class (`markers.length > 0`). "Published" here means
  the clip's **current state in CatDV** (`clip.markers` from `archive.get_clip`,
  whatever its origin — authored directly in CatDV or written by our publish
  flow), NOT the stricter `publish_status` "Live vN" notion (a version *we*
  snapshotted via `clip_versions`). The split's purpose is to show the live
  CatDV content above the draft for comparison, so any CatDV markers qualify;
  the class is named `-markers` to avoid implying our version flow. The split
  rules are now
  `.detail.is-draft.has-published-markers …` and the draft band defaults to full height,
  dropping to the bottom half only under that combined class. So: published clip
  in draft → split (published top, draft bottom); unpublished clip in draft →
  draft uses the full height, no empty strip; either clip in published scope →
  published full height, draft hidden (0106's auto-switch still lands here).
- **Two-colour split.** Published = orange (`--accent`, the live colourway),
  draft = blue (`--info`), scoped to `.detail` so Studio's `range-cur` band keeps
  its own colour. Matches the app's draft=blue / live=orange language and the
  prompt-studio reference.
- **Legible timecodes on any band, any theme.** `.tc-labels span` gets a backdrop
  pill in the timeline's own base colour
  (`color-mix(in oklab, var(--bg-2) 85%, transparent)`) plus `var(--text-3)` —
  both token-derived, so contrast holds over blue/orange bands and across themes
  rather than assuming a fixed light/dark background.

This revises ADR 0106 R1 (bands are no longer mutually exclusive *in draft
scope*) while keeping 0106's reactive bands and auto-switch-to-Published-after-
sync behaviour intact.

## Consequences

- In draft scope the operator sees published markers (orange) stacked above the
  draft markers (blue) when a published version exists; unpublished clips show a
  clean full-height draft band with no empty reserved space.
- Timecode labels are readable over any band in every theme.
- Guard: `tests/integration/test_clip_detail_draft.py::
  test_published_timeline_band_visible_in_both_scopes` pins that the published
  band carries no scope `x-show`, that the split is keyed on `has-published-markers`, and
  that the draft band stays draft-scoped. Studio overlay + design-language guards
  unchanged.
- Verified live: `/clips/888894` (live) shows orange-top + (injected) blue-bottom
  split with legible labels; `/clips/889070` (unpublished) shows a full-height
  blue draft band with legible labels and no empty top strip.
