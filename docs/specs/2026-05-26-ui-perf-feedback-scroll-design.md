# UI responsiveness: click feedback, local assets, cache scroll

**Date:** 2026-05-26
**Status:** Accepted

## Problem

Two user-reported UI problems:

1. **Clicking anything feels "super slow" — no sign the click registered.** The user
   often can't tell whether a click landed, because nothing on screen changes for
   seconds after clicking.
2. **The Cache page can't be scrolled** the way the Clips page can — the bottom of
   the page is unreachable.

## Investigation findings

### Slowness is mostly *missing feedback* + *remote asset loading*, not the CatDV metadata fetch

- `CatdvAdapter.get_clip` reads the local metadata cache first with a **7-day TTL**
  (`backend/app/archive/providers/catdv/adapter.py`). After a clip is first opened,
  the detail page renders from SQLite — the CatDV call is skipped. Draft and
  cache-status lookups on that page are also local DB queries. So the server
  response is usually fast.
- Clip rows navigate with a **full-page reload** (`location.href`,
  `_video_list.html`). Tabs/filters reload the whole document too. Each reload
  re-bootstraps the page from scratch even when data is cached locally.
- **htmx, Alpine, and Google Fonts load from the public internet on every page
  load** (`pages/layout.html`): `htmx.org` and `alpinejs` from unpkg, two font
  families from `fonts.googleapis.com`. The htmx `<script>` is in `<head>` and is
  **render-blocking**. On a VPN'd / egress-restricted dev box these round-trips can
  stall for seconds before the page paints. Nothing is self-hosted.
- There is **no loading feedback anywhere** in the frontend — no progress bar, no
  spinner, no `hx-indicator`, no pressed/disabled state on click (only a single
  button uses `cursor: progress`).

### Cache scroll

The Cache page is the one page that scrolls *itself* (`.cache-page { overflow-y: auto }`,
`app.css`) instead of using a bounded inner scroller like Clips
(`.clips-region { min-height: 0 } > .tbl-scroll { overflow: auto }`). The app shell
is a CSS grid (`.app { grid-template-rows: 40px 1fr }`) with `.main { overflow: hidden }`
and **no `min-height: 0`**. A grid `1fr` track defaults to `min-height: auto`, so tall
cache content grows the `main` track past the viewport; `.cache-page`'s `height: 100%`
inherits that overgrown height, its `overflow-y: auto` has nothing to scroll, and
`.main { overflow: hidden }` clips the overflow. Result: unreachable bottom rows.

## Scope (explicitly bounded — "no big changes")

In scope:

1. **Self-host htmx, Alpine, and the web fonts** under `/static`. Pure asset move,
   no behavior change. Removes the render-blocking remote script and per-load
   CDN/font round-trips.
2. **Click/action feedback**: a global top progress bar plus a pressed/disabled
   state on the active control. Driven by htmx lifecycle events for htmx actions,
   and by a small click listener for full-page navigations (clip rows, rail links).
3. **Fix the Cache page scroll** so it behaves like the Clips page.

Explicitly **out of scope** (deferred — these are the "big changes"):

- `hx-boost` / converting full-page navigations to partial swaps.
- Changing the clip-list cache TTL or any CatDV data-path caching.
- Any change to the CatDV request path itself.

## Design

### 1. Local assets

- Vendor the exact pinned versions currently used (`htmx.org@1.9.10`,
  `alpinejs@3.14.1`) into `backend/app/static/vendor/` and serve them via the
  existing `/static` mount.
- Self-host the two font families (Inter, JetBrains Mono) as static `.woff2` files
  with a local `@font-face` block in `app.css`, or accept the system-font fallback
  if vendoring fonts proves heavy. Fonts are the lowest-priority item here; the
  render-blocking script is the important one.
- Update `pages/layout.html` to reference the local paths. Keep htmx before Alpine
  (the existing `alpine:init` ordering comment still applies). Keeping htmx in
  `<head>` is fine once it's local (no network wait); `defer` for Alpine stays.

### 2. Feedback layer

- **Progress bar**: a thin fixed-position bar at the top of the viewport. A tiny
  inline script (no new dependency) shows it on `htmx:beforeRequest` and on clicks
  of navigational elements (`a[href]` within the app, rows with an `onclick`
  navigation), and hides it on `htmx:afterRequest`. For full-page navigations the
  bar simply shows until the browser unloads the page and the fresh page renders
  without it — which is the desired "your click registered, loading…" signal.
- **Pressed/disabled state**: lean on htmx's built-in `.htmx-request` class for
  htmx actions (style it as a dimmed/spinner state in `app.css`). For navigational
  row/link clicks, add a transient `is-navigating` class on the clicked element so
  it visibly reacts immediately.
- No change to request semantics; this is presentation only.

### 3. Cache scroll

- Add `min-height: 0` to `.main` (and/or `grid-template-rows: 40px minmax(0, 1fr)`
  on `.app`) so the grid track is bounded by the viewport and `.cache-page`'s
  `overflow-y: auto` engages. Verify in a browser at multiple window heights, and
  confirm no regression on Clips / Clip detail / Prompts pages (which already
  scroll via their own inner containers).

## Testing

- **Manual / browser** is the primary verification for all three items — this is
  presentation and asset wiring, not logic. Confirm: (a) page paints without a
  network wait when offline from the CDNs (e.g. block unpkg) — proves local assets
  are used; (b) the progress bar/pressed state appears immediately on click of rows,
  tabs, and rail links; (c) the Cache page scrolls to its last row at a short window
  height, and Clips/Detail/Prompts still scroll correctly.
- **Automated**: a lightweight check that `layout.html` references local asset paths
  (no `unpkg.com` / `googleapis.com`), to prevent regressing back to the CDN. Respect
  the CatDV single-seat discipline in `CLAUDE.md` when running the dev server for
  manual checks (check for an existing instance; shut down with `SIGTERM`).

## Consequences

- Navigation still does full-page reloads (deferred `hx-boost`), but each reload is
  faster (local assets, no render-blocking remote script) and now gives immediate
  visual feedback, directly addressing "I'm not even sure I clicked."
- One new `/static/vendor/` directory and (optionally) local font files become assets
  the repo must carry. Versions are pinned to today's CDN versions.
- The grid scroll fix is global; it must be checked against every page, not just Cache.
