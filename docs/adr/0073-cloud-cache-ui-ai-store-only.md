# 0073. Cloud cache UI: hide the unused local-media layer, act on the ai-store

**Date:** 2026-06-11
**Status:** Accepted
**Lifespan:** Feature

## Context

On Cloud Run the media backend is `media_cache="ai_store"` (ADR 0069): a
clip's bytes live in the GCS AI-store, and the local proxy cache
(`proxy_cache` / `media-local`) is not used. But the per-clip cache UI still
spoke the three-layer local-dev vocabulary everywhere:

- The cache **badge** rendered all three glyphs — `metadata ●`,
  `media-local ▣`, `media-ai ▲` — so cloud users saw a permanently-absent
  local-media indicator that means nothing in their deployment.
- The clip-detail **Cache / Evict buttons** gated on `media_local.present`.
  In cloud that is always false, so the "⬇ Cache video" button showed even
  when the clip was already cached in the ai-store, and there was no way to
  purge the ai-store copy from the clip page (only the popover's per-layer
  Evict).
- **"Cache video"** POSTed `/api/cache/prefetch` then immediately
  `location.reload()`. Prefetch is an async background job, so the reload
  happened before anything was cached: no start confirmation, no progress
  spinner, no finish confirmation. Users couldn't tell whether it worked.

`host_local_proxies` (`proxy_resolver.is_host_local`) was the only
deployment axis the templates had, and it is the wrong one: it is `False`
in *both* cloud and local-with-download, so it cannot distinguish them.

## Alternatives

1. **Thread a `cloud`/`media_cache` flag through every include site.** The
   badge is included from the clips list, clip detail, video list, and two
   HTMX fragment routes. Threading a bool through all of them is verbose and
   easy to miss one, leaving a stale local glyph somewhere.
2. **Drive the buttons purely with Alpine `x-show` off a seeded `cached`
   bool.** Avoids a fragment route, but makes the Cache↔Purge logic
   untestable server-side and still needs a place to re-derive the badge
   after a state change.
3. **Keep `location.reload()` and add a pre-reload toast.** The toast store
   is in-memory, so a reload drops the confirmation — the very thing users
   said was missing.

## Decision

- **One signal, exposed as a Jinja global.** `media_cache` (from
  `settings.media_cache`) is registered as a template global —
  default `"local"` in `routes/pages/templates.py`, overridden from settings
  in the app lifespan. Every full-page and HTMX-fragment render can branch
  on `media_cache == 'ai_store'` without threading a parameter. This is the
  cloud-vs-local axis; `host_local_proxies` stays as the orthogonal
  "host already has proxies" axis for local dev.
- **Hide the local-media layer in cloud.** The `media-local` glyph
  (both badge templates) and the `media-local` popover row are omitted when
  `media_cache == 'ai_store'`. `metadata` and `media-ai` stay.
- **Buttons act on the authoritative layer.** In cloud, Cache/Purge gate on
  `media_ai.present` (Purge evicts `media-ai`); local dev keeps the
  `media_local` / Evict-local path under `not host_local_proxies`.
- **A refreshable control + an Alpine component for async feedback.** The
  badge + buttons moved into `pages/_cache_actions.html`, served standalone
  by `GET /ui/cache-actions/{clip_id}`. The `cacheActions` Alpine component
  POSTs prefetch, **polls `/api/cache/prefetch/queue` to completion**, toasts
  start/finish (and errors), shows a `.ca-spinner` while busy, then swaps the
  control in place via the fragment route — no `location.reload()`
  (CLAUDE.md). Evict/Purge route through the same component.

## Consequences

- Cloud users see a coherent two-indicator badge (`metadata`, `ai`), a
  Cache button only when uncached, a Purge button only when cached, and
  real start→spinner→finish feedback.
- `media_cache` is now a template global; any new cache surface gets the
  cloud/local distinction for free. The default in `templates.py` keeps
  fragment renders safe if the lifespan override hasn't run (tests).
- The Cache↔Purge selection is server-rendered, so it is covered by
  `tests/integration/test_cache_cloud_ui.py` (badge hiding, popover row
  hiding, Purge-vs-Cache). The async polling/toast/spinner behaviour is
  client-side and verified in the browser.
- Local-dev behaviour is unchanged: `media_cache` defaults to `"local"`, so
  the three-glyph badge and the proxy Cache/Evict path render as before.
