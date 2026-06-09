# 0063. Clip card consolidation (Candidate D) explored and declined

**Date:** 2026-06-09
**Status:** Accepted

## Context

The 2026-06-09 architecture review listed "Candidate D — unify the clip media
card" (rated *Worth exploring*, not *Strong*). On close exploration it does not
hold up. Recorded here so future architecture reviews don't re-suggest it.

## What we found

- **The table-row clip rendering is already consolidated.** `_video_list.html`
  is the shared scaffold (a thumb+name cell + injected head/row cells), used by
  the clips list, the cache list, the batch picker, AND the archive picker —
  the picker's `_batch_picker_cells.html` is just trailing `<td>`s. Cross-ref
  ADR 0056.
- **`.thumb` is not duplicated** — three context-scoped rules, each defined
  once: `.vlist .thumb` (table row), `.studio-clip-card .thumb` (grid card with
  `.tc`/`.yr` overlays), `.nb-bchip .thumb` (42×24 basket chip).
- **The two "card-ish" renderings are single-use and genuinely different
  shapes**: `studio-clip-card` (one renderer — a grid card with a
  `background-image` thumb + overlays + checkbox + remove-x + run-dots) and
  `nb-bchip` (one renderer — a tiny `<img>` chip with name + kind + remove).

## Decision

Do not build a shared `ui.clip_card`. A component spanning a grid card, a table
row, and a chip (div-vs-img, overlay-vs-none, server-Jinja-vs-client-Alpine
data) would fail the deletion test — it concentrates nothing and adds a
parameterized wrapper over three distinct shapes ("don't introduce a seam
unless something actually varies across it"). The real consolidation
(`_video_list.html`) is already done.

## Consequences

- The clip media card is the one architecture-review candidate intentionally
  not pursued; this ADR records why, so it isn't re-litigated.
- A close-out cleanup landed alongside: the design-language guard's menu/btn
  burn-down list is now empty except the permanent `shutdown-btn` / `rail-btn`.
  `studio-run-btn` was a dead class (no CSS) and was removed; `mp-fail-btn`
  became `.btn.link.danger` (a new bare-text-button modifier on the `.btn`
  system, per the design-language rule against `*-btn` classes); the studio
  header's `hdr-title-btn` was renamed `.hdr-title-trigger` (an intentional
  bespoke trigger, like `.pc-vchip`, not an action button); and the dead
  `.actions-kinds` CSS was swept.
