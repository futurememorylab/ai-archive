# 0055. Studio output compare is an aligned scene table + linked timeline

**Date:** 2026-06-02
**Status:** Accepted
**Lifespan:** Feature

## Context

Comparing two prompt versions' **output** in Studio showed two side-by-side
Output panes plus a flowing word-diff. A brief earlier iteration on this branch
word-diffed the *rendered text* of the output (superseding the raw-JSON diff),
but the user pivoted to a richer design: a single **aligned scene table** where
the two versions' structured output is aligned row-by-row into scenes — each row
showing a diff status (UNCHANGED / CHANGED / ADDED / REMOVED) and word-level
inline highlights (red strike for removed words on the left, green for added on
the right) — and a **linked comparison timeline** where the existing two-track
marker overlay gains scene-name labels, status-colored borders, and bidirectional
hover highlighting with the table.

Both views are driven by the same question: how do the two versions' markers line
up, and what changed?

## Alternatives

1. **Client-side alignment + diff** — compute the alignment and word-diff in JS
   from two embedded JSON blobs. Rejected: the alignment is non-trivial and the
   codebase prefers Python-authoritative, unit-tested logic; server-render keeps
   the table a plain HTMX partial.
2. **A new third "Compare" tab** rather than replacing Output. Rejected by the
   user — the table replaces the Output panes when comparing.
3. **Markers-only.** Rejected — fields and notes are included.
4. **Keep the rendered-text flowing diff.** Superseded by the structured table.
5. **An "● evidence M:SS" CatDV-marker cross-reference** (in the original mock).
   Dropped: the annotation model has no field linking a scene to a CatDV marker
   and no source for one (markers carry only `in_secs/out_secs/name/category/
   description`). Out of scope until such data exists.

## Decision

- **One pure model** — `services/output_compare.build_output_compare(cur_panels,
  cmp_panels)` consumes the `panels` dicts `build_draft_view` already produces and
  returns aligned `scenes` / `fields` / `notes` rows with a per-row status and a
  single `word_diff`. Scenes align by a **greedy two-pointer time-overlap merge**
  over the time-ordered marker lists (overlap → pair, else advance the
  earlier-ending side as removed/added). Verified to reproduce the mock's exact
  five aligned scenes. Documented limitation: a 1→N split pairs with the first
  and marks the rest added.
- **`word_diff` promoted** from a test-only mirror to a real service
  (`services/word_diff.py`) with a `diff_html(segs, side)` Jinja global
  (`left`=eq+del, `right`=eq+ins, `both`=all). The client `wordDiff` in
  `studio-diff.js` stays for the live Prompt-tab diff (it reads textareas).
- **Table partial + route** — `_studio_compare_table.html` + `GET
  /studio/_compare`. When comparing **and** on the Output tab, a full-width table
  replaces the two per-card Output panes (gated by `compareVersionId === null` on
  the panes and `!== null` on the table region). Prompt mode and single-version
  Output are unchanged.
- **Timeline** — `_studio_player`'s row builder derives both rows from the same
  `build_output_compare` model when comparing, so every range shares the table's
  `data-scene-key` + status; `_player_overlay.html` renders a `range-label` and a
  `range-st-<status>` border, both guarded so the clip-detail caller (which does
  not pass `show_range_labels`/`status`/`scene_key`) is unaffected.
- **Linkage** — a plain vanilla bridge (`studioSceneLink.js`) toggles `.is-linked`
  on every `[data-scene-key="<key>"]` element on hover (table row ↔ timeline
  block), mirroring the key into `Alpine.store('studio').selectedSceneKey`. Vanilla
  (not Alpine directives) because both surfaces are HTMX-injected innerHTML where
  directive-less subtrees don't reliably wire up — the same rationale as the
  existing `window.studio` shim.
- **Colors** — `--changed` (purple) drives both the CHANGED pill and the changed
  timeline-range border so the two views agree; added=green (`--good`),
  removed=red (`--bad`), unchanged=blue (`--info`).

## Consequences

- The Output compare reads as one aligned, track-changes table instead of two raw
  panes; the timeline visually echoes the same statuses and cross-highlights on
  hover.
- `build_output_compare` couples to the `panels` shape and the `.marker`/`.field`
  identity keys; it is pure and unit-tested (alignment edge cases pinned), and the
  table↔timeline key match is pinned by a cross-endpoint integration test.
- `word_diff` now has a single authoritative Python home; the JS mirror remains
  only for the live Prompt diff.
- No new frontend dependency; the no-Node-frontend stack (ADR 0001) holds. Builds
  on ADR 0050 (word-level diff) — same engine, new structured consumer; the
  raw-JSON / rendered-text output diffs are removed, don't reintroduce them.
- "evidence" cross-reference is deferred pending a real scene→CatDV-marker link.
- **`+ Compare` is no longer gated to the under-player layout** (reverses the
  ADR 0040 gating). ADR 0051 made the `right` layout a three-column `Player |
  cur | cmp` arrangement and removed the auto-close-on-layout-switch, so compare
  is fully supported there; the button gate was the last vestige of the old
  restriction and is dropped so a comparison can be initiated from either
  layout. (`test_studio_layout_toggles_markup.py` updated accordingly.)
