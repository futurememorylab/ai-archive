# 0030. UI responsiveness: local assets, click feedback, cache scroll

**Date:** 2026-05-26
**Status:** Accepted
**Lifespan:** Feature

## Context

Users reported the UI felt slow — clicks gave no sign they registered ("not even
sure I clicked"), and the Cache page could not be scrolled to its last rows.
Investigation found three independent causes, not one.

Spec: `docs/specs/2026-05-26-ui-perf-feedback-scroll-design.md`.

## Alternatives

- **`hx-boost` / convert full-page navigations to partial swaps** to cut reload
  cost. Deferred: bigger behavioural change; the asset and feedback fixes
  addressed the felt problem without it.
- **Keep CDN assets, add only a spinner.** Rejected: the render-blocking htmx
  `<script>` from unpkg was a real per-load latency source on a VPN'd box.
- **Cache scroll: restructure to an inner scroller like the Clips page** (fixed
  header + scrolling table region). Held as a fallback; the simpler CSS fix
  worked, so the restructure was not needed.

## Decision

1. **Self-host assets.** Vendor htmx 1.9.10 + Alpine 3.14.1 and the Inter /
   JetBrains Mono variable fonts under `/static/vendor/`, removing the
   render-blocking unpkg script and the Google Fonts round-trip. No `hx-boost`.
2. **Feedback.** A fixed top progress bar (`#app-progress`) plus a window-wide
   `cursor: progress` busy state, driven by a dependency-free `nav-feedback.js`
   from htmx lifecycle events and capture-phase navigation clicks. The click
   handler defers to htmx for elements carrying `hx-*` attributes, so htmx links
   (e.g. cache tabs) don't `start()` without a matching `done()` and leave the
   bar/cursor stuck.
3. **Cache scroll.** Three layered CSS causes, all fixed: the `.app` grid row is
   `minmax(0, 1fr)` and `.main` gets `min-height: 0` (bound the track to the
   viewport); `.cache-page` gets `flex: 1; min-height: 0` (it is the scroll
   viewport, like `.tbl-scroll` on Clips); and `.cache-page > *` gets
   `flex-shrink: 0` (it is also a flex column, so its children — notably
   `.cache-listwrap`, which has `overflow: hidden` and thus a 0 flex
   min-height — were being compressed to fit instead of overflowing).
4. **Cache row navigation.** Non-orphan cache rows set `row_href` to
   `/clips/{id}` so they open the clip detail like the Clips list; orphans stay
   non-clickable (the detail page would 404).

## Consequences

- Navigations still do full-page reloads, but each is faster (local assets) and
  now gives immediate visual feedback. `hx-boost` remains a future option.
- The cache scroll fix is a general flex-column-scroll-container pattern; the
  `flex-shrink: 0` on children is the load-bearing, non-obvious piece.
- The repo now carries vendored JS + woff2 assets (pinned to the prior CDN
  versions) that must be refreshed manually on upgrade.
- A `tests/unit/test_layout_assets.py` guard fails if the CDN references return.
