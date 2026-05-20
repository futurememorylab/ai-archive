# UI MVP — CatDV Catalog Browser

**Date:** 2026-05-20
**Status:** Approved, ready for plan
**Author:** Peter Hora (with Claude)
**Supersedes:** Nothing. First UI deliverable for the project (backend-only until now).

---

## 1. Goal

Ship the smallest end-to-end UI that lets the user browse the CatDV `AI katalog` (881507) and view a clip's existing annotations side-by-side with its proxy video — a read-only minimal mirror of the CatDV web client, restricted to two screens.

This UI **does not** participate in the AI annotation workflow yet. No prompt management, no job queue, no review/approve flow. Those land in later plans.

## 2. Scope

**In:**
- Two HTML pages, server-rendered by FastAPI + Jinja2.
- Clips list with search and pagination over `GET /api/catdv/clips`.
- Clip detail: native HTML5 video player streaming from `GET /api/media/{id}`, with a sidebar listing existing CatDV markers, custom field values, and notes.
- Visual timeline showing existing markers as click-to-seek ranges.
- SMPTE timecode readout (`HH:MM:SS:FF` at 25 fps).
- Dark theme only.

**Out (deferred):**
- AI proposed annotations (accept/reject voting).
- Templates / Jobs / Archive / Settings screens.
- Light theme, density/typography tweaks.
- Custom transport buttons (J/K/L, ±1 frame, set in/out). Native `<video controls>` covers MVP.
- Keyboard shortcuts.
- Write/mutation operations against CatDV.
- Tailwind CLI (revisit when we have ≥4 screens).
- Pixel-perfect parity with the mockup's polish (HUD scope, film-grain overlay, ethics banner). The mockup is the visual reference; we recreate layout, palette, and typography, not the prototype DOM.

## 3. Project-readiness check

| Need | Status |
|---|---|
| List clips API | ✓ `GET /api/catdv/clips?q&offset&limit` returns paged `provider_data` |
| Clip detail API | ✓ `GET /api/catdv/clips/{id}` returns full CatDV JSON (`markers[]`, `fields{}`, `notes`, `bigNotes`, `media{}`) |
| Proxy video stream | ✓ `GET /api/media/{id}` with HTTP Range support — drops into `<video>` |
| Jinja2 | ✓ in `pyproject.toml`, already used by HTMX partials in `backend/app/templates/` |
| Static file mount | ✗ — add `StaticFiles` mount in `main.py` |
| Page routes | ✗ — add `backend/app/routes/pages.py` |

No DB migration, no service change, no provider work. Reuses the same `ctx.archive` adapter the API routes use (in-process call, no HTTP hop, no duplicated code).

## 4. Tech stack

Honors ADR 2026-05-18 ("Python-only stack, no Node frontend"):

- **Server-rendered Jinja2** templates.
- **HTMX** via CDN (already used by `cache_page.html`) for the clips-list search debounce.
- **Alpine.js** via CDN, only on the detail page, only for two jobs: live timecode readout and marker click-to-seek (~30 lines).
- **Plain CSS** — one file (`backend/app/static/app.css`), ~250 lines. Dark palette, density, and typography tokens taken from the mockup's `styles.css`. **Tailwind deferred** — the build-step cost isn't justified by two screens.

No build step. No npm.

## 5. Architecture

```
backend/app/
  static/                       ← new; mounted at /static
    app.css                     ← dark theme, mockup tokens
    player.js                   ← Alpine component: TC + seek
  templates/
    layout.html                 ← shared topbar + CDN tags + <main>
    clips.html                  ← clips list page
    clips_tbody.html            ← HTMX partial for search re-renders
    clip_detail.html            ← player + annotations sidebar
  routes/
    pages.py                    ← GET / and GET /clips/{id}
  main.py                       ← wire StaticFiles + pages_router
```

### Routes

| Route | Renders |
|---|---|
| `GET /` | `clips.html` — full page. Calls `ctx.archive.list_clips(catalog_id, ClipQuery(text=q, offset, limit))`. |
| `GET /?q=…&offset=…` with `HX-Request: true` | `clips_tbody.html` partial — for HTMX search swap. |
| `GET /clips/{clip_id}` | `clip_detail.html`. Calls `ctx.archive.get_clip(str(clip_id))`. Returns 404 if absent. |

Both routes call the adapter directly (not over HTTP). The existing `/api/catdv/*` routes stay as the public JSON surface for future consumers; the HTML pages share their data source, not their code path beyond the adapter.

### View model (what the templates receive)

**Clips list:**
```python
{
  "q": str | "",
  "offset": int,
  "limit": int,               # default 50
  "total": int,
  "catalog": {"id": int, "name": str},
  "clips": [
    {
      "id": int,
      "name": str,
      "duration_secs": float,
      "fps": float,
      "year": str | None,       # from fields["pragafilm.rok.natočení"]
      "decade": str | None,     # from fields["pragafilm.dekáda.natočení"]
      "marker_count": int,
    },
    ...
  ],
}
```

**Clip detail:**
```python
{
  "clip": {
    "id": int,
    "name": str,
    "duration_secs": float,
    "fps": float,                  # PAL 25 in this archive
    "format": str,                 # e.g. "QuickTime · H.264 · 720×576 · 25p"
    "size_mb": float | None,
    "media_url": "/api/media/{id}",
    "markers": [
      {"name": str, "in_secs": float, "out_secs": float | None,
       "description": str | None, "category": str | None, "color": str | None},
      ...
    ],
    "fields": [
      {"identifier": str, "name": str, "value": str | list[str]},
      ...
    ],
    "notes": str | None,
    "big_notes": str | None,
  },
}
```

A small adapter function in `pages.py` (`_clip_view(canonical_clip)`) transforms `CanonicalClip` + raw `provider_data` into this shape so the templates stay logic-free.

### Client interactivity

**Clips list (HTMX only):**
- Search input: `hx-get="/" hx-trigger="input changed delay:300ms" hx-target="#clips-tbody" hx-swap="outerHTML" hx-include="this"`. The route returns the partial when `HX-Request` is set, the full page otherwise.
- Pagination: plain `<a href="/?offset=…&q=…">‹ Prev | Next ›</a>` links.

**Clip detail (Alpine):**
```js
// player.js — registers an Alpine component
Alpine.data('player', () => ({
  fps: 25,
  current: 0,
  duration: 0,
  init() {
    const v = this.$refs.video;
    v.addEventListener('timeupdate', () => this.current = v.currentTime);
    v.addEventListener('loadedmetadata', () => this.duration = v.duration);
  },
  seek(secs) { this.$refs.video.currentTime = secs; },
  tc(secs) {
    const f = Math.round(secs * this.fps);
    const h = Math.floor(f / (3600 * this.fps));
    const m = Math.floor((f % (3600 * this.fps)) / (60 * this.fps));
    const s = Math.floor((f % (60 * this.fps)) / this.fps);
    const ff = f % this.fps;
    const pad = (x) => String(x).padStart(2, '0');
    return `${pad(h)}:${pad(m)}:${pad(s)}:${pad(ff)}`;
  },
}));
```

Markers in the sidebar and on the timeline get `@click="seek({{ m.in_secs }})"`.

### CSS

One file. CSS variables for the dark palette (copied from `mockup/styles.css`):

```css
:root {
  --bg: #0b0d10; --panel: #14181d; --surface: #1d232a;
  --text: #e6e9ee; --text-2: #b6bcc6; --text-3: #7e8693;
  --accent: #f5a623; --good: #3ddc84; --bad: #ff5d5d;
  --line: rgba(255,255,255,0.07);
  --f-sans: "Inter", system-ui, sans-serif;
  --f-mono: "JetBrains Mono", ui-monospace, monospace;
}
```

Layout:
- Clips list: 1-column page, sticky top bar, sticky table header, fixed-height rows.
- Clip detail: 2-column grid (`1fr 400px`) — video left, sidebar right. Top bar across both columns. Timeline below the video.

Fonts via Google Fonts (Inter + JetBrains Mono), same as the mockup.

## 6. Acceptance

Manual end-to-end test against the live CatDV server (VPN required):

1. `./run.sh`; open `http://localhost:8765/`.
2. Clips list loads with all clips from catalog 881507. Page count matches `total / 50`.
3. Type "Anna" into the search box; list narrows to matching clips within ~300 ms after typing stops.
4. Click a clip row → detail page loads.
5. Video starts playing on click of the native play button; timecode readout updates while playing.
6. Existing markers appear on the timeline as colored ranges and as cards in the sidebar.
7. Clicking a marker card or a timeline range jumps the video to the marker's `in` time.
8. Custom `pragafilm.*` field values render in the sidebar with their Czech labels.
9. Refreshing the detail page restores the same view (no client-side state to lose).

No automated UI tests in this MVP — backend is already covered by pytest; the new code is templates + ~30 lines of JS where unit-testing earns nothing useful.

## 7. Open follow-ups (not in this MVP)

- Adopt Tailwind CLI when Templates + Jobs screens land (≥4 screens).
- Bring back the proposed-changes sidebar and ethics banner once the AI annotation pipeline is wired through to the UI.
- Wire keyboard shortcuts (J/K/L, ±1 frame, marker prev/next) when there's a real review use case.
- Light theme via the existing CSS variables — ~20 line addition.
- Poster thumbnails: MVP uses a pure-CSS gradient placeholder (mockup-style). Decide later whether to proxy `GET /catdv/api/9/clips/{id}/poster` — VPN throughput makes proxying ~50 thumbnails per page noticeable, so a dedicated thumbnail-cache route may be needed.
