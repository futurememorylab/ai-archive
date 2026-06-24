# 0034. Prompt Studio PR2 — version compare

- **Date:** 2026-05-27
- **Status:** Accepted
- **Lifespan:** Feature

## Context

PR2 of Prompt Studio adds side-by-side version comparison: a version
picker chip on each prompt-card, a `+ Compare` button that materializes
a second card, line-diff views for prompt body and structured output,
and a second range row on the player overlay. The spec at
`docs/specs/2026-05-26-prompt-studio-pr2-design.md` covers the design;
this ADR records the implementation calls worth knowing about.

## Alternatives

- **Alpine-side state with all versions preloaded** for version
  switching. Rejected: bigger initial payload, more JS, and the
  editor↔readonly DOM toggle would need to be replicated client-side.
  HTMX swap of the card partial keeps server-side ownership of the
  rendering and matches the existing `/studio/_run` swap pattern.
- **Separate `/api` endpoint for the output JSON** consumed by the
  diff. Rejected: every diff toggle would incur two fetches. Embedding
  the raw JSON in a `<script type="application/json" data-run-json>`
  block inside the existing output partial is zero-cost since the
  partial is already loaded.
- **Custom transport replacing native `<video controls>`.** Rejected:
  the umbrella spec says "no new player behavior". Layering an
  SVG/HTML strip below the native controls and reusing
  `Alpine.data("player", ...)` from clip_detail is the smaller change.
- **Bespoke renderer for the Studio Output tab.** Rejected actively.
  `_anno_panels.html` already renders markers/fields/notes on
  `clip_detail` and `_anno_draft.html`; the studio's PR1 renderer was
  one day old. Codified the reuse rule in `CLAUDE.md` ("Frontend:
  explore before implementing").

## Decision

- **Version switch is HTMX-driven.** A new partial route
  `GET /studio/_prompt_card?side=cur|cmp&prompt_version_id=N&clip_id=M`
  renders one card. The chip's dropdown rows are HTMX buttons with
  `hx-target="closest .studio-prompt-card"` and `hx-swap="outerHTML"`.
  A page-level `htmx:afterSwap` listener reads `data-side` /
  `data-version-id` / `data-version-num` off the swapped root and
  reconciles `studioPage` state (active/compare ids, URL params,
  player refresh).
- **Cur picker is the active-version picker.** Picking on cur bumps
  `studioPage.activeVersionId/Num`, the Run-button label (already
  bound), and the URL `?version_id=`. Picking on cmp updates only
  `compareVersionId` and `?compare_version_id=`. cmp picker is local.
- **Both selectors are deep-linkable.** The page route accepts both
  query params and server-renders the right initial state. URL is
  written back via `history.replaceState` on every picker change.
- **Output diff reads from sibling DOM** — no extra fetch. Each card's
  Output partial includes a `<script type="application/json"
  data-run-json>` block; the cmp card's `cmpDiff` Alpine component
  parses both blocks, `JSON.stringify(..., null, 2)` pretty-prints,
  then `lineDiff` produces a row alignment that renders as a
  two-column table.
- **`lineDiff` is dual-ported** — Python (in
  `tests/unit/test_studio_line_diff.py`) and JS (in
  `backend/app/static/studio-diff.js`). The Python version is
  authoritative; the JS is a character-for-character port. Shared
  fixtures cover both. Node cross-check confirmed byte-identical
  output for the interleaved fixture.
- **`_player_overlay.html`** is extracted from `clip_detail.html` and
  shared with the studio player. The studio uses
  `Alpine.data("player", ...)` unchanged. The partial does NOT wrap
  in `.transport` — callers wrap, so `clip_detail` can keep
  `.transport-row` as a child of `.transport` (preserving the
  `.transport .tc-readout` CSS descendant chain).
- **`_anno_panels.html`** is reused for the studio Output tab. A new
  `show_history` flag (default `true`) elides the History tab in
  studio context; a server-side `panels_from_studio_run` adapter
  converts `(output_json, target_map)` into the partial's `panels`
  shape. Marker clicks reach the studio player via a
  `seekFocusedClip` proxy on the page Alpine root.
- **Tab sync** lifts `mode` from per-card to the page Alpine. Both
  cards' tab buttons bind to `$root.mode`.
- **Compare default selection** prefers next-most-recent non-cur
  draft, else production, else any. Selection is in
  `studioPage.openCompare()`.
- **Cmp card semantics:** never editable, no model picker, no Run
  button binding (Run always operates on cur).

## Consequences

- One renderer for markers/fields/notes across the app. New
  marker/field features added to `_anno_panels.html` show up in
  studio for free. Conversely, regressions there affect studio — the
  clip-detail player-overlay regression test guards the seam.
- The cmp-card diff view has a known limitation: it only diffs what
  is currently rendered. If the Prompt tab is active when toggled,
  the diff is over prompt bodies; if Output is active, over JSON.
  Switching modes re-runs `cmpDiff.refresh` automatically via
  `x-effect`.
- Deep-link URLs (`?prompt_id=…&version_id=…&compare_version_id=…`)
  are reload-safe and shareable. Tab/mode and diff-toggle state are
  not in the URL (intentional — see open questions in the spec).
- The `seek` proxy assumes the studio player is the focused-clip
  player on the same page. If we ever support multiple players on
  the studio page (no plan to), this assumption needs revisiting.
- The `/studio/_player` route grew an offline `duration_secs`
  fallback (derives from max scene `out_secs`) so the overlay
  renders in test mode where the archive can't supply duration.
  Guard requires `ctx.archive is None`, so production behavior is
  unchanged.
- The compare-card materialization injects a server-rendered partial
  into `[data-cmp-slot]` and calls `Alpine.initTree(slot)` to wire
  the new subtree. This relies on Alpine v3's public `initTree`
  surface; a future Alpine upgrade should re-verify.
