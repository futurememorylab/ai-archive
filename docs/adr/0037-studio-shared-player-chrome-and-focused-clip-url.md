# 0037. Studio: shared player chrome + focused clip in URL

- **Date:** 2026-05-27
- **Status:** Accepted
- **Lifespan:** Feature

## Context

Two related issues surfaced on the Studio page after PR2 shipped:

1. **The Studio player looked different from the clip-detail player.**
   `_studio_player.html` had been written as a stripped-down `<video
   controls>` plus the shared `_player_overlay.html` timeline, while
   `clip_detail.html` had the full chrome inlined directly into the page
   (HUD, custom transport buttons, kbdbar). Same Alpine `player(...)`
   component, two parallel renderers — the exact anti-pattern called out
   in `CLAUDE.md` ("Frontend: explore before implementing — do not
   parallel-evolve a second renderer").
2. **Focus on a clip was lost when the prompt was switched.** Picking a
   prompt from the header dropdown does a full-page navigation
   (`/studio?prompt_id=N`), and `focusedClipId` lived only in Alpine
   state — so the player slot emptied, the Run button disabled, and the
   sidebar reverted to no-selection on every prompt change.

This ADR records the calls made to fix both, since several were
non-obvious and a reader of the diff would reasonably ask *why*.

## Alternatives

- **Keep two player templates, just sync their styling.** Rejected:
  every time the clip-page chrome evolves the studio drifts again. The
  Alpine `player(...)` component was already shared; extracting the
  chrome into a partial finishes the job.
- **Persist `focusedClipId` in `localStorage`** instead of the URL.
  Rejected: URLs are shareable, survive cross-browser navigation, and
  the studio page already round-trips `prompt_id`, `version_id`, and
  `compare_version_id` through the query string — `clip_id` belongs in
  the same set.
- **Switch prompts via HTMX swap instead of full reload.** Rejected:
  much larger refactor (the entire page body re-renders — folder list,
  prompt cards, compare slot). Adding `clip_id` to the URL solves the
  focus-loss problem with a one-line route addition.
- **Use Alpine `:href` on the prompt-picker links** to inject
  `clip_id` from `$root.focusedClipId`. Rejected: in Alpine v3 `$root`
  returns the *closest* `x-data` ancestor, not the topmost component.
  The picker is wrapped in `<div x-data="{ open: false }">` for its
  dropdown state, which shadows `studioPage` — `$root.focusedClipId`
  resolves to the inner div and evaluates to `undefined`. Lifting
  `open` into the page-level root would have worked but pollutes the
  shared component with picker UI state.
- **Render `.selected` on the focused clip card purely via
  `htmx:afterSwap` JS.** Rejected as the *sole* mechanism: HTMX swap
  timing relative to first paint is unreliable, and the user
  experiences a flash of "no selection" before the JS runs. The JS
  handler stays as a reconciliation pass for cross-folder cases (see
  Decision below).

## Decision

- **Extract a shared `_player.html` chrome partial.** It wraps the
  `<video>`/`<img>`, the HUD overlay, the `_player_overlay.html`
  timeline, the custom transport buttons (prev/next marker, step,
  play), the TC readout, and an optional SHUTTLE/AUDIO meta line.
  Caller-controlled toggles: `show_kbdbar`, `show_meta`, `show_legend`.
  Both `clip_detail.html` and `_studio_player.html` now include it; the
  studio variant hides the kbdbar (it doesn't own keyboard focus) and
  the meta line. A scoped CSS rule `.studio-player .viewer {
  height: 320px; flex: none; }` caps the studio slot — the chrome's
  `.viewer` is `flex:1` by default, which is correct for clip-detail
  but would dominate the studio panel.

- **Persist `focusedClipId` in the URL as `clip_id=…`.** The
  `/studio` route accepts `clip_id`, passes it as `focused_clip_id` to
  the template, which seeds `studioPage()` and conditionally drops the
  `no-player` CSS class so the player slot is visible from first paint.
  `studioPage._writeUrl()` and `focusClip()` keep the URL in sync as
  the user re-focuses without navigating.

- **Auto-expand the folder holding the focused clip on page load.**
  The route looks up `focused_folder_id` via a new
  `StudioFoldersRepo.folder_id_for_clip(clip_id)` and seeds
  `studioFolders(initialExpandedId)` so the matching folder is open
  from the start. Otherwise after a prompt switch the player restores
  but the clip's card is buried inside a collapsed folder — visually
  identical to "focus was lost".

- **Vanilla click interceptor on `a[data-prompt-switch]` rewrites the
  href at click time** to append `clip_id=…` from live `studioPage`
  state. Avoids Alpine `$root` scope traps and covers
  modifier-click-into-new-tab (setting `href` before default action
  means cmd-click also carries the clip).

- **Render `.selected` on clip cards server-side.** The folder-kids'
  `hx-get` URL carries `&clip_id={{ focused_clip_id }}`; the
  `_studio_folder` route receives it and `_studio_clip_card.html`
  renders the matching card with `class="studio-clip-card selected"`
  from the start. The `htmx:afterSwap` handler remains as a
  reconciliation pass for the case where the user has re-focused via
  JS *after* page load — the `hx-get` URLs are baked at page render
  time and the server's guess for newly-expanded folders would
  otherwise be stale. The handler now clears any existing
  `.selected` in the swapped subtree before re-applying, so the stale
  and current cards don't both end up marked.

## Consequences

- The studio and clip-detail players cannot drift again: one chrome,
  one Alpine component, two thin wrappers that differ only in CSS
  class and a few toggle flags.
- Studio URLs now look like
  `/studio?prompt_id=1&version_id=14&clip_id=42` — bookmarkable and
  shareable. Anyone landing on that URL gets the same view as the
  user who created it.
- Auto-expanding folders adds one extra DB query
  (`folder_id_for_clip`) per studio page load when `clip_id` is set
  — single indexed lookup, negligible cost.
- The `htmx:afterSwap` handler doing reconciliation is non-obvious
  and worth keeping the comment that explains why server-side
  selection isn't sufficient on its own. If a future change makes the
  folder-kids `hx-get` URLs reactive to `focusedClipId` (e.g. via an
  Alpine `:hx-get` binding from a scope that can see studioPage), the
  handler can go.
- `clip_kind` in `_studio_player.html` is hardcoded to `"video"`. If
  image clips become studio-eligible (currently they aren't — the
  studio is video-only), the shared `_player.html` already supports
  the `clip_kind == "image"` branch and the wrapper just needs to
  pass through `clip.kind`.
