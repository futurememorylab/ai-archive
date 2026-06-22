# Annotation follow-playback — design spec

**Date:** 2026-06-22
**Status:** Draft
**Surface:** Clip detail page (`clip_detail.html` + `player.js` + annotation list partials)

## Goal

Archive researchers want the annotation column to *follow* playback. When a
clip plays, the annotation whose segment contains the current playhead should
be visually highlighted — both on the timeline (already the case) and as a card
in the annotation column (new) — and the column should auto-scroll so the active
card stays visible, with **minimal movement**.

This applies to **published and draft markers** (whichever scope/tab is
currently shown). It does not touch scenes, fields, or notes.

## What already exists (reused, not rebuilt)

- `player.js::isMarkerActive(m)` — returns true when `current` is within a
  marker's `[in_secs, out_secs]` window (40ms fallback when no out). Already
  drives the orange `.range.active` highlight on the timeline.
- `this.current` — playhead in seconds, updated on `timeupdate`, frame-quantized
  (~4 updates/sec), so watching it is cheap.
- `seek(secs)` — used by both timeline clicks and annotation-card clicks; the
  existing "click a card → video jumps to the annotation's start" behaviour.
- `.anno-body` — the single `overflow-y: auto` scroll container for the column.
- Marker lists: published markers via Jinja `{% for %}` in `_anno_panels.html`;
  draft markers via Alpine `x-for="m in draftMarkers"` in `_anno_draft.html`.
  Both carry `in_secs` / `out_secs` (seconds) and an identity (`item_id` for
  draft, list index for published).

No backend changes. Times stay in **seconds** throughout (existing convention).

## Design

### 1. Highlighting — one source of truth

Extend the existing `isMarkerActive(m)` to drive the column cards, so "active" is
defined in exactly one place for both surfaces:

- **Draft cards** (`x-for`): add `:class="{ active: isMarkerActive(m) }"`.
- **Published cards** (Jinja `{% for %}`): bind
  `:class="{ active: isMarkerActive({in_secs: {{ m.in_secs }}, out_secs: {{ m.out_secs if m.out_secs is not none else 'null' }} }) }"`.
  Alpine processes server-rendered attributes, so this stays reactive to
  `current` with no extra state.

New CSS: an `.active` style on the marker card mirroring the timeline accent
(left border + subtle tint) using existing `:root` design tokens — no new class
vocabulary, no raw hex.

### 2. Scroll algorithm — comfort band, nearest edge

`.anno-body` has a visible viewport. Define a **comfort band**: a vertical safe
zone inside it (default top/bottom margin = ~20% of viewport height each, i.e.
the band spans ~20%–80%). On each playhead tick, for the **anchor** card
(see §3):

1. If the card is **fully inside** the band → **do nothing**. (This is why an
   already-visible annotation — e.g. the very first one — never moves.)
2. If it's **above** the band → scroll up just enough to bring its top to the
   band's top edge. If **below** → scroll down to bring its bottom to the band's
   bottom edge. Nearest edge only — never centered — so movement is the minimum
   needed to satisfy "visible".
3. **Distance-based behaviour:** small corrections scroll **smoothly**; large
   jumps (e.g. a timeline seek to the far end) scroll **instantly** (`'auto'`)
   so the list doesn't slowly glide for a second. Threshold: roughly one
   viewport height.

This is implemented as a **pure helper** so it can be unit-tested without a DOM:

```
computeScroll({ scrollTop, viewportHeight, cardTop, cardHeight, bandMargin }) 
  -> { scrollTo: number, behavior: 'smooth' | 'auto' } | null   // null = no move
```

`cardTop` is relative to the scroll container's content. The Alpine method
`followActiveAnno()` is the thin DOM wrapper: measure rects → call helper →
apply `scrollTo` (if non-null), guarded by the self-scroll flag (§4).

### 3. Anchor selection (overlap + gaps)

Markers can overlap, so several may be active at once.

- **Highlight all** active cards (consistent with the timeline highlighting all
  active ranges).
- The **scroll anchor** is the **first active card in list order**. Lists are
  `in_secs`-ordered, so this is the earliest-starting active marker — a stable
  anchor that does not hop between cards as the playhead crosses an overlap
  boundary.
- **Gap** (playhead between segments, nothing active): clear all highlights and
  **do not scroll** — the list stays exactly where it is, ready for the next
  annotation.

A pure helper makes this testable too:

```
activeAnchorIndex(markers, current) -> index | null
```

### 4. Manual-scroll pause

Chosen behaviour: manual scroll suspends auto-follow **briefly**, then resumes.

- A `selfScrolling` flag is set while `followActiveAnno()` performs a
  programmatic scroll, and cleared after it settles — so the app never mistakes
  its own scroll for the user's.
- A genuine user `wheel` / `touchmove` / `scroll` (when `selfScrolling` is
  false) sets `followSuspended = true` and (re)arms a ~4s timer.
- When the timer elapses with no further manual scrolling, `followSuspended`
  returns to false and following resumes on the next tick.
- **Intentional navigation resumes immediately:** `seek()` (timeline click or
  annotation-card click) clears `followSuspended` so the list snaps to the new
  position right away.

### 5. Driver & scope

- A single `$watch('current', () => this.followActiveAnno())` in the `.detail`
  Alpine scope drives everything. (Highlighting is already reactive via the
  `:class` bindings; the watch only handles scrolling.)
- `followActiveAnno()` targets **only the currently-visible markers list** inside
  `.anno-body`, and only when the markers tab is shown. Switching scope
  (published⇄draft) or tab re-evaluates against the now-visible list on the next
  tick. It reads the visible scope from existing `scope` / `tab` state.

### 6. Scenarios (acceptance of the goal)

| Scenario | Result |
|---|---|
| First annotation, already on screen | Highlighted, no scroll (inside band). |
| Active annotation scrolled off-screen | Scrolls to nearest band edge (minimal). |
| Playhead enters a gap | Highlights cleared, list unchanged. |
| User seeks far via timeline | One instant scroll to the new active card. |
| Click annotation card | Video seeks to its start (existing); follow resumes immediately and card is already in view → little/no scroll. |
| Long list, sequential play-through | One calm card-by-card scroll as each becomes active. |
| User manually scrolls away mid-play | Follow pauses ~4s, then resumes. |

## Out of scope

- Scenes, fields, notes following playback.
- Any backend / view-model / API change.
- Changing the existing click-to-seek or timeline-highlight behaviour (reused
  as-is).

## Components touched

| File | Change |
|---|---|
| `backend/app/static/player.js` | `computeScroll()` + `activeAnchorIndex()` pure helpers; `followActiveAnno()`; `$watch('current')`; `followSuspended` / `selfScrolling` state + manual-scroll listeners; `seek()` clears suspension. |
| `backend/app/templates/pages/_anno_panels.html` | `:class="{ active: isMarkerActive({...}) }"` on published marker cards; a `data-` anchor hook if needed for measurement. |
| `backend/app/templates/pages/_anno_draft.html` | `:class="{ active: isMarkerActive(m) }"` on draft marker cards. |
| `backend/app/static/app.css` | `.marker.active` / `.ri-card.active` style via design tokens. |
| `tests/unit/` (JS) | Tests for `computeScroll()` and `activeAnchorIndex()`. |

## Testing

**JS unit tests (pure helpers — no DOM):**

- `computeScroll`: already-in-band → `null` (no move); above band → scrolls to
  top edge; below band → scrolls to bottom edge; distance > ~1 viewport →
  `behavior: 'auto'`, else `'smooth'`.
- `activeAnchorIndex`: single active → its index; overlapping actives → earliest
  start (first in order); gap → `null`; boundary (`current === in_secs`) →
  active.

**Manual acceptance flows** — see below.

## Manual acceptance flows

Setup: a running dev server; clip with several markers, including a long enough
list to overflow the column. Reference clip:
`http://127.0.0.1:8765/clips/889070`.

1. **Timeline + column highlight in sync.** Open the clip, press play. As the
   playhead enters each marker's segment, *expected:* the matching range turns
   orange on the timeline **and** the matching card highlights in the column at
   the same time; the highlight clears when the playhead leaves the segment.

2. **No move when already visible.** With playback near the top of the list (the
   first annotation visible), play through it. *Expected:* the first card
   highlights without the list scrolling at all.

3. **Auto-scroll to off-screen active (minimal movement).** Continue playback
   until the active annotation would fall below the visible area. *Expected:* the
   list scrolls just enough to bring the active card to the lower comfort edge —
   not centred, not flush to the very bottom.

4. **Timeline seek follows.** Drag/click the timeline to jump to a marker far
   down the list. *Expected:* the column jumps (instantly, no slow glide) so the
   now-active card is visible; its highlight matches the timeline.

5. **Click-to-seek stays consistent.** Click an annotation card lower in the
   list. *Expected:* the video seeks to that annotation's start (existing
   behaviour), the card highlights, and the list does not jump away from it.

6. **Gap = no movement.** Pause/scrub to a point between two markers.
   *Expected:* no card is highlighted and the list does not scroll.

7. **Manual-scroll pause.** During playback, scroll the column away from the
   active card. *Expected:* the list stays where you put it for ~4s (does not
   immediately snap back), then resumes following on the next tick.

8. **Draft scope.** Switch to the draft tab on a clip with draft markers and
   repeat flows 1–3. *Expected:* draft cards highlight and the draft list
   auto-scrolls identically to published.
