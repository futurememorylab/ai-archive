# Player Redesign — Spec

**Date:** 2026-05-20
**Scope:** Clip-detail video player only. Visual + interaction parity with the Claude Design mockup (`CatDV Annotator.html`), implemented on the existing Alpine.js + Jinja + vanilla-CSS stack.

## Background

`backend/app/templates/pages/clip_detail.html` currently renders a minimal player:
- `<video controls>` with native browser chrome.
- Timeline strip with marker ranges (Jinja-rendered) and an Alpine-driven playhead.
- TC readout in the page header.

The Claude Design mockup (extracted to `/tmp/design_bundle/catdv-annotator/project/`) reimagines this player with: HUD overlays on the viewer, a dedicated transport row, prev/next-marker navigation, frame-step controls, a 5-quintile TC label strip, and a keyboard-shortcuts hint bar at the bottom.

This spec ports the **player area only**. Explicitly out of scope: risk markers, the right-rail "Proposed changes" tabs, accept/reject decisions, "Apply N to CatDV", Set IN/OUT mutation, ethics banner.

## Goals

1. Visual parity with the mockup's `.player-wrap` (viewer + transport + transport-row + kbdbar).
2. Keyboard-driven transport: Space, `,`/`.` (frame step), `↑`/`↓` (prev/next marker), `J`/`K`/`L`, `Home`/`End`.
3. Marker click jumps the playhead and pauses focus on that range. Hover tooltips show marker name.
4. No new frontend frameworks. Alpine.js stays the only client-side runtime.

## Non-Goals

- Marker editing (rename, set IN/OUT, delete).
- Decision state (accepted / rejected / pending colors on ranges).
- Risk markers (red `range risk` styling and behavior).
- Right-rail item cards, tabs, bulk actions, ethics banner.
- "Apply to CatDV" header buttons or prev/next-clip navigation.
- Replacing Alpine with a different framework.

## UI structure

```
.detail
├── .detail-hdr              (unchanged: title, format, cache badges, TC readout)
└── .player-wrap             (rebuilt)
    ├── .viewer
    │   ├── <video> (no controls, double-click → fullscreen)
    │   └── .hud .hud-tl     (timecode + frame counts)
    ├── .transport
    │   ├── .timeline
    │   │   ├── .ticks       (decorative tick strip)
    │   │   ├── .ranges      (marker ranges, click → seek)
    │   │   ├── .playhead    (Alpine `:style="left: pct%"`)
    │   │   └── .tc-labels   (5 quintile TCs)
    │   └── .transport-row
    │       ├── .transport-btns (prev-marker, step-back, play/pause, step-fwd, next-marker)
    │       ├── .tc-readout  (current / duration)
    │       ├── .grow
    │       └── shuttle/audio meta strip (static decoration)
    └── .kbdbar              (keyboard shortcuts hint, static config)
└── .anno-col                (unchanged: markers, fields, notes)
```

## Alpine component (`backend/app/static/player.js`)

Extend the existing `Alpine.data("player", ...)` factory. New shape:

```js
Alpine.data("player", (fps, duration, markers) => ({
  fps: fps || 25,
  duration: duration || 0,
  current: 0,
  playing: false,
  markers: markers || [],   // [{in_secs, out_secs, name}, ...] sorted by in_secs

  init() { /* wire timeupdate, play, pause, loadedmetadata; install keydown */ },
  destroy() { /* remove keydown */ },

  // transport
  togglePlay()      { ... },
  stepFrame(delta)  { /* pause(), then currentTime += delta/fps */ },
  prevMarker()      { /* nearest marker with in_secs < current */ },
  nextMarker()      { /* nearest marker with in_secs > current */ },
  seek(secs)        { /* clamp + assign + play */ },

  // formatting
  tc(secs)          { /* HH:MM:SS:FF */ },
  frame(secs)       { return Math.round(secs * this.fps); },
  pct(secs)         { return this.duration ? (secs / this.duration) * 100 : 0; },
  quintileTc(i)     { return this.tc((i / 4) * this.duration); },  // i ∈ 0..4

  // markers
  hasMarkers()      { return this.markers.length > 0; },
}));
```

**Keyboard handler** (installed in `init`, scoped to `document`, ignores when focus is in an input/textarea):

| Key      | Action               |
|----------|----------------------|
| Space    | togglePlay           |
| `,`      | stepFrame(-1)        |
| `.`      | stepFrame(+1)        |
| `↑`      | prevMarker           |
| `↓`      | nextMarker           |
| `J`      | play backwards (set playbackRate = -1; fallback: stepFrame(-1) repeated) |
| `K`      | pause                |
| `L`      | play (rate = 1)      |
| `Home`   | seek(0)              |
| `End`    | seek(duration)       |

`J` reverse-play is best-effort — most browsers don't support negative `playbackRate` on `<video>`. Fallback: treat `J` as alias for `stepFrame(-1)` if `playbackRate = -1` is rejected (catch the silent no-op via testing `playbackRate` round-trip).

## Template changes (`clip_detail.html`)

1. Pass markers into the Alpine factory as JSON, sorted by `in_secs`:
   ```jinja
   x-data='player({{ clip.fps }}, {{ clip.duration_secs }}, {{ markers_json|safe }})'
   ```
   Build `markers_json` in the view: `[{"in_secs": ..., "out_secs": ..., "name": ...}]`.

2. Replace the `<section class="player-wrap">` body with the structure above. Drop `controls` from `<video>`.

3. Remove the duplicate timeline that's currently inside `.player-wrap` — there's now exactly one timeline, inside `.transport`.

4. Bind transport buttons:
   ```html
   <button class="tbtn" :disabled="!hasMarkers()" @click="prevMarker()" title="prev marker (↑)">…</button>
   <button class="tbtn" @click="stepFrame(-1)" title="step back 1 frame (,)">…</button>
   <button class="tbtn play" @click="togglePlay()" title="play/pause (Space)">…</button>
   <button class="tbtn" @click="stepFrame(1)" title="step fwd 1 frame (.)">…</button>
   <button class="tbtn" :disabled="!hasMarkers()" @click="nextMarker()" title="next marker (↓)">…</button>
   ```
   Play icon swaps via `x-show` on two SVGs.

5. Marker ranges remain Jinja-rendered for SSR-friendly hover/click; `@click` uses `seek(secs)` exactly as today.

6. Add static keyboard-shortcuts strip after the transport (matches mockup's `.kbdbar`).

## Icons

Inline SVG. The mockup's icons live in `icons.jsx` — port the 7 we need (`IconStepBack`, `IconStepFwd`, `IconFrameBack`, `IconFrameFwd`, `IconPlay`, `IconPause`, plus optional `IconIn`/`IconOut` if we decide to show them as disabled — we won't).

Store them as Jinja macros in a new partial `backend/app/templates/pages/_player_icons.html` and `{% include %}` where needed, or inline since count is small. **Decision:** inline — 6 small SVGs, no reuse outside the player.

## CSS (`backend/app/static/app.css`)

Replace the existing `.player-wrap`, `.timeline`, `.timeline .ranges`, `.timeline .range`, `.timeline .playhead` block with a port of these rules from the design bundle's `styles.css`:

- `.player-wrap` — flex column, gap, dark background
- `.viewer` — relative, 16:9 (or `aspect-ratio: 16/9`), holds `<video>` and `.hud-*`
- `.hud`, `.hud-tl`, `.hud-tc`, `.hud-lbl`, `.hud-val` — overlay typography
- `.transport`, `.timeline`, `.ticks`, `.ranges`, `.range` (+ `:hover`, `.active`), `.playhead`, `.tc-labels`
- `.transport-row`, `.transport-btns`, `.tbtn`, `.tbtn.play`
- `.transport .tc-readout`, `.transport .tc-readout .slash`, `.transport .tc-readout .end`
- `.kbdbar`, `.kbdbar .group`, `.kbdbar .sep`, `.kbd`

Rewrite color/radius/font references to use existing app tokens (`var(--text)`, `var(--text-2)`, `var(--line)`, `var(--accent)`, `var(--accent-fg)`, `var(--surface-2)`, `var(--r-2)`, `var(--f-mono)`). Drop the design's `.range.risk` rules and `:hover` rules that depend on the decision-color palette we're not using.

## Server-side

A small change in the view that renders `clip_detail.html`: build `markers_json` (list of `{in_secs, out_secs, name}` with `out_secs` defaulting to `in_secs + 0.04` for point markers so `nextMarker()` can sort), JSON-encoded, sorted ascending by `in_secs`. Pass as template var. No new endpoints.

## Behavior details

- **Point markers** (no `out_secs`): render as 4px-wide pill at `left = in_secs/duration%`.
- **Active marker highlight:** when `current` is between a marker's in/out, that range gets a brighter outline. Alpine `:class="{ active: current >= m.in_secs && current <= m.out_secs }"` per range — but with N markers this is N reactive checks; cheap at typical counts (<50). If a clip has hundreds of markers, batch via a computed `activeMarkerUid`.
- **No native controls:** add `:has(.video:fullscreen)` style so fullscreen still hides everything except the video, or simply rely on the browser's fullscreen chrome.
- **Mute/volume:** out of scope for this redesign. The shuttle/audio strip is a static label (matches mockup's intent — visual flavor, no functional control yet).

## Testing

Manual checklist (no Playwright in this repo):
1. Load a clip with ≥3 markers — verify all ranges render, click each jumps and pauses-then-plays.
2. Space toggles play. `,`/`.` step exactly 1 frame at 25 fps (`current` increments by 0.04 ± rounding).
3. `↑`/`↓` move to prev/next marker; at boundaries the buttons are disabled (`:disabled` styling visible).
4. TC readout updates live; HUD timecode + frame counts match.
5. Quintile TC labels show 5 strictly-increasing timecodes spanning 0 → duration.
6. Load a clip with 0 markers — timeline shows just ticks + playhead; prev/next buttons disabled; player still works.
7. Focus an `<input>` (e.g. cache search) and press Space — must NOT toggle play.
8. Resize narrow — transport row wraps cleanly; kbdbar horizontally scrolls (matches mockup).

## Out of scope (record for follow-up specs)

- Risk markers (red ranges + ethics banner).
- Right-rail tabbed item review (markers / description / risks tabs).
- Accept/reject decisions and "Apply N to CatDV" flow.
- Marker editing (Set IN/OUT, rename, delete) — needs backend write path beyond what's wired.
- Per-clip prev/next navigation in the page header.
- Confidence bars on items.

These are part of the broader mockup but require additional backend support (write queue extensions, AI proposal storage, etc.) and should be brainstormed separately.
