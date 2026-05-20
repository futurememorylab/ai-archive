# Player Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the clip-detail video player to match the Claude Design mockup (HUD overlays, dedicated transport, prev/next-marker, frame-step, keyboard shortcuts strip) on the existing Alpine.js + Jinja + vanilla-CSS stack, with no new frameworks.

**Architecture:** One view-model addition (sort markers asc by `in_secs`). One template rewrite (player section only). One JS replacement (extended Alpine `player` component with transport methods + global keydown handler). One CSS replacement (port player + transport + kbdbar rules from design bundle, adapted to existing tokens). Risk markers, right-rail tabs, accept/reject, and Set IN/OUT are out of scope per the spec.

**Tech Stack:** FastAPI · Jinja2 · Alpine.js (already in use) · vanilla CSS. Python tests via pytest 9.x. No JS test framework (project has none — frontend changes verified manually per spec checklist).

**Reference spec:** `docs/specs/2026-05-20-player-redesign-design.md`

**Reference design bundle (read-only, do not commit):** `/tmp/design_bundle/catdv-annotator/project/` — `styles.css` (CSS source of truth), `icons.jsx` (SVG sources), `review.jsx` (interaction reference).

---

## File Structure

**Modify:**
- `backend/app/ui/view_models.py` — sort `clip.markers` ascending by `in_secs` in `clip_detail()` output. ~5-line change inside the existing function.
- `backend/app/templates/pages/clip_detail.html` — replace the `<section class="player-wrap">` block (lines 37–58) with the new viewer + transport + kbdbar structure. Other sections (header, anno-col, scripts) unchanged.
- `backend/app/static/player.js` — replace the entire file with an extended Alpine factory: `playing`, `togglePlay`, `stepFrame`, `prevMarker`, `nextMarker`, `frame`, `quintileTc`, keyboard handler.
- `backend/app/static/app.css` — replace the player + timeline block (lines 246–355, roughly) with ported rules from design `styles.css`. Add `.viewer`, `.hud-*`, `.transport`, `.transport-row`, `.tbtn`, `.kbdbar` rules. Drop `.range.risk` (out of scope).

**Create:**
- `tests/unit/test_player_markers_sorted.py` — pytest verifying `clip_detail()` returns markers sorted by `in_secs`.

**No new dependencies, no new endpoints, no schema changes.**

---

## Task 1: Sort markers in `clip_detail` view-model (TDD)

**Files:**
- Test: `tests/unit/test_player_markers_sorted.py` (create)
- Modify: `backend/app/ui/view_models.py:114-141` (`clip_detail` function)

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_player_markers_sorted.py`:

```python
"""Markers in clip_detail() must be sorted ascending by in_secs.

The frontend player's prev/next-marker navigation depends on this ordering;
sorting in the view-model keeps the template + JS simple.
"""
from backend.app.archive.model import CanonicalClip, Marker, Timecode
from backend.app.ui.view_models import clip_detail


def _clip_with_markers(*in_secs: float) -> CanonicalClip:
    return CanonicalClip(
        key=("catdv", "12041"),
        name="Test_Clip",
        duration_secs=600.0,
        fps=25.0,
        markers=tuple(
            Marker(
                name=f"m@{s}",
                in_=Timecode(secs=s, fps=25.0),
                out=Timecode(secs=s + 1.0, fps=25.0),
            )
            for s in in_secs
        ),
        fields={},
        notes={},
    )


def test_clip_detail_markers_sorted_ascending_by_in_secs():
    clip = _clip_with_markers(120.0, 30.0, 250.5, 90.0)
    d = clip_detail(clip)
    in_secs = [m["in_secs"] for m in d["clip"]["markers"]]
    assert in_secs == [30.0, 90.0, 120.0, 250.5]


def test_clip_detail_markers_sort_is_stable_for_equal_in_secs():
    clip = _clip_with_markers(50.0, 50.0, 50.0)
    d = clip_detail(clip)
    names = [m["name"] for m in d["clip"]["markers"]]
    # All three have in_secs == 50.0; original insertion order preserved.
    assert names == ["m@50.0", "m@50.0", "m@50.0"]


def test_clip_detail_handles_zero_markers():
    clip = _clip_with_markers()
    d = clip_detail(clip)
    assert d["clip"]["markers"] == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_player_markers_sorted.py -v`

Expected: First test FAILS (markers come back in insertion order `[120, 30, 250.5, 90]`, not sorted). Second + third PASS already.

- [ ] **Step 3: Implement the sort**

Edit `backend/app/ui/view_models.py`, in `clip_detail()`, change the markers comprehension to sort by `in_.secs`:

```python
    return {
        "clip": {
            "id": clip_id,
            "name": clip.name,
            "duration_secs": clip.duration_secs,
            "fps": clip.fps or 25.0,
            "format": _format_summary(clip.provider_data),
            "media_url": f"/api/media/{clip_id}",
            "markers": [
                _marker_view(m)
                for m in sorted(clip.markers, key=lambda m: m.in_.secs)
            ],
            "fields": fields_view,
            "notes": _fix(clip.provider_data.get("notes")) or None,
            "big_notes": _fix(clip.provider_data.get("bigNotes")) or None,
            "cache": cache_status_view(cache_status) if cache_status else None,
        },
    }
```

(Python's `sorted` is stable, satisfying the second test.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_player_markers_sorted.py tests/unit/test_view_models.py -v`

Expected: All three new tests PASS, no regressions in existing `test_view_models.py`.

- [ ] **Step 5: Commit**

```bash
git add tests/unit/test_player_markers_sorted.py backend/app/ui/view_models.py
git commit -m "feat(view-model): sort clip_detail markers by in_secs

Frontend player navigates markers by position; sorting in the view-model
keeps the template loop and the prev/next-marker JS trivial."
```

---

## Task 2: Replace `player.js` with extended Alpine component

**Files:**
- Modify (full rewrite): `backend/app/static/player.js`

This task has no automated test (no JS test framework in the project). Manual verification at the end. The full file is below — copy verbatim.

- [ ] **Step 1: Replace the file contents**

Overwrite `backend/app/static/player.js` with:

```js
// Alpine player component for clip_detail.html.
//
// Props (from x-data="player(fps, duration, markers)"):
//   fps      — frames per second (number)
//   duration — clip duration in seconds (number)
//   markers  — sorted-ascending list of {name, in_secs, out_secs}
//
// Markers may have out_secs === null (point markers). Sorting is the
// server's job (see clip_detail view-model).

document.addEventListener("alpine:init", () => {
  Alpine.data("player", (fps, duration, markers) => ({
    fps: fps || 25,
    duration: duration || 0,
    current: 0,
    playing: false,
    markers: Array.isArray(markers) ? markers : [],

    _onKey: null,

    init() {
      const v = this.$refs.video;
      if (v) {
        v.addEventListener("timeupdate", () => { this.current = v.currentTime; });
        v.addEventListener("loadedmetadata", () => {
          if (!this.duration || isNaN(this.duration)) this.duration = v.duration;
        });
        v.addEventListener("play",  () => { this.playing = true; });
        v.addEventListener("pause", () => { this.playing = false; });
      }
      this._onKey = (e) => this._handleKey(e);
      document.addEventListener("keydown", this._onKey);
    },

    destroy() {
      if (this._onKey) document.removeEventListener("keydown", this._onKey);
    },

    // ─── transport ────────────────────────────────────────────────
    togglePlay() {
      const v = this.$refs.video;
      if (!v) return;
      if (v.paused) v.play().catch(() => {}); else v.pause();
    },

    play() {
      const v = this.$refs.video;
      if (v) v.play().catch(() => {});
    },

    pause() {
      const v = this.$refs.video;
      if (v) v.pause();
    },

    stepFrame(delta) {
      const v = this.$refs.video;
      if (!v) return;
      v.pause();
      const next = Math.max(0, Math.min(
        v.currentTime + delta / this.fps,
        v.duration || this.duration
      ));
      v.currentTime = next;
    },

    seek(secs) {
      const v = this.$refs.video;
      if (!v) return;
      const clamped = Math.max(0, Math.min(secs, v.duration || this.duration || secs));
      v.currentTime = clamped;
      v.play().catch(() => {});
    },

    hasMarkers() {
      return this.markers.length > 0;
    },

    prevMarker() {
      if (!this.markers.length) return;
      // First marker strictly before current; falls back to last marker if
      // we're already at the start so wraparound is friendly.
      const EPS = 0.001;
      let pick = null;
      for (let i = this.markers.length - 1; i >= 0; i--) {
        if (this.markers[i].in_secs < this.current - EPS) { pick = this.markers[i]; break; }
      }
      if (!pick) pick = this.markers[this.markers.length - 1];
      this.seek(pick.in_secs);
    },

    nextMarker() {
      if (!this.markers.length) return;
      const EPS = 0.001;
      let pick = null;
      for (let i = 0; i < this.markers.length; i++) {
        if (this.markers[i].in_secs > this.current + EPS) { pick = this.markers[i]; break; }
      }
      if (!pick) pick = this.markers[0];
      this.seek(pick.in_secs);
    },

    // ─── formatting ───────────────────────────────────────────────
    tc(secs) {
      const f = Math.round((secs || 0) * this.fps);
      const fpsR = Math.round(this.fps);
      const ff = f % fpsR;
      const ts = Math.floor(f / fpsR);
      const ss = ts % 60;
      const mm = Math.floor(ts / 60) % 60;
      const hh = Math.floor(ts / 3600);
      const pad = (x) => String(x).padStart(2, "0");
      return `${pad(hh)}:${pad(mm)}:${pad(ss)}:${pad(ff)}`;
    },

    frame(secs) {
      return Math.round((secs || 0) * this.fps);
    },

    frameStr(secs) {
      return this.frame(secs).toLocaleString();
    },

    pct(secs) {
      if (!this.duration) return 0;
      return (secs / this.duration) * 100;
    },

    quintileTc(i) {
      // i ∈ 0..4 → TC at 0, 25%, 50%, 75%, 100% of duration.
      return this.tc((i / 4) * this.duration);
    },

    // ─── marker range styling ─────────────────────────────────────
    isMarkerActive(m) {
      if (m.in_secs == null) return false;
      const out = m.out_secs != null ? m.out_secs : m.in_secs + 0.04;
      return this.current >= m.in_secs && this.current <= out;
    },

    rangeLeftPct(m) {
      return this.pct(m.in_secs);
    },

    rangeWidthPct(m) {
      const out = m.out_secs != null ? m.out_secs : m.in_secs + 1.0;
      const w = this.pct(out) - this.pct(m.in_secs);
      return Math.max(0.4, w);  // minimum visible width
    },

    // ─── keyboard ─────────────────────────────────────────────────
    _handleKey(e) {
      // Ignore when user is typing.
      const t = e.target;
      if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" ||
                t.isContentEditable)) return;
      // Ignore with modifiers (let browser shortcuts through).
      if (e.metaKey || e.ctrlKey || e.altKey) return;

      switch (e.key) {
        case " ":
        case "Spacebar":
          e.preventDefault(); this.togglePlay(); break;
        case ",":
          e.preventDefault(); this.stepFrame(-1); break;
        case ".":
          e.preventDefault(); this.stepFrame(1); break;
        case "ArrowUp":
          if (this.hasMarkers()) { e.preventDefault(); this.prevMarker(); }
          break;
        case "ArrowDown":
          if (this.hasMarkers()) { e.preventDefault(); this.nextMarker(); }
          break;
        case "j": case "J":
          e.preventDefault(); this.stepFrame(-1); break;   // reverse-play fallback
        case "k": case "K":
          e.preventDefault(); this.pause(); break;
        case "l": case "L":
          e.preventDefault(); this.play(); break;
        case "Home":
          e.preventDefault(); this.seek(0); break;
        case "End":
          e.preventDefault(); this.seek(this.duration); break;
        default: break;
      }
    },
  }));
});
```

- [ ] **Step 2: Sanity-check syntax**

Run: `node --check backend/app/static/player.js`

Expected: no output (file parses cleanly). If `node` isn't installed, skip — the dev server will fail loudly on a parse error.

- [ ] **Step 3: Commit**

```bash
git add backend/app/static/player.js
git commit -m "feat(player): extend Alpine component with transport + keyboard

Adds togglePlay, stepFrame, prev/next-marker, frame-counter helpers,
quintile TC labels, marker-active highlight, and a global keydown
handler (Space, ,/., arrows, J/K/L, Home/End). Ignores keys when
focus is in an input or when modifiers are held."
```

---

## Task 3: Rewrite the player section of `clip_detail.html`

**Files:**
- Modify: `backend/app/templates/pages/clip_detail.html:37-58` (the `<section class="player-wrap">` block)

- [ ] **Step 1: Replace the player section**

In `backend/app/templates/pages/clip_detail.html`, change the `x-data` attribute on line 11 to pass markers as JSON:

```jinja
<div class="detail"
     x-data='player({{ clip.fps }}, {{ clip.duration_secs }}, {{ clip.markers|tojson }})'>
```

Then replace lines 37–58 (the entire `<section class="player-wrap">…</section>` block) with:

```jinja
  <section class="player-wrap">
    <div class="viewer">
      <video x-ref="video"
             class="video"
             src="{{ clip.media_url }}"
             preload="metadata"
             @dblclick="$refs.video.requestFullscreen && $refs.video.requestFullscreen()"></video>
      <div class="hud hud-tl">
        <div class="hud-lbl">timecode · <span x-text="Math.round(fps) + 'p'">25p</span></div>
        <div class="hud-tc" x-text="tc(current)">00:00:00:00</div>
        <div class="hud-val">
          frame <span x-text="frameStr(current)">0</span>
          / <span x-text="frameStr(duration)">0</span>
        </div>
      </div>
    </div>

    {% if clip.duration_secs %}
    <div class="transport">
      <div class="timeline">
        <div class="ticks"></div>
        <div class="ranges">
          {% for m in clip.markers %}
            {# Jinja loop order matches the Alpine `markers` array — both come
               from the same view-model field, sorted ascending by in_secs. #}
            <div class="range"
                 :class="{ active: isMarkerActive(markers[{{ loop.index0 }}]) }"
                 style="left: {{ (m.in_secs / clip.duration_secs) * 100 }}%; width: {{ (((m.out_secs or m.in_secs + 1) - m.in_secs) / clip.duration_secs) * 100 }}%"
                 title="{{ m.name }}"
                 @click="seek({{ m.in_secs }})"></div>
          {% endfor %}
        </div>
        <div class="playhead" :style="`left: ${pct(current)}%`"></div>
        <div class="tc-labels">
          <span x-text="quintileTc(0)">00:00:00:00</span>
          <span x-text="quintileTc(1)"></span>
          <span x-text="quintileTc(2)"></span>
          <span x-text="quintileTc(3)"></span>
          <span x-text="quintileTc(4)">{{ duration_smpte }}</span>
        </div>
      </div>

      <div class="transport-row">
        <div class="transport-btns">
          <button type="button" class="tbtn"
                  :disabled="!hasMarkers()"
                  @click="prevMarker()"
                  title="prev marker (↑)">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><polygon points="19 4 8 12 19 20 19 4"/><line x1="5" y1="4" x2="5" y2="20"/></svg>
          </button>
          <button type="button" class="tbtn"
                  @click="stepFrame(-1)"
                  title="step back 1 frame (,)">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><line x1="6" y1="4" x2="6" y2="20"/><polygon points="20 4 10 12 20 20 20 4"/></svg>
          </button>
          <button type="button" class="tbtn play"
                  @click="togglePlay()"
                  title="play/pause (Space)">
            <svg x-show="!playing" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><polygon points="6 4 20 12 6 20 6 4"/></svg>
            <svg x-show="playing"  viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><rect x="6" y="4" width="4" height="16"/><rect x="14" y="4" width="4" height="16"/></svg>
          </button>
          <button type="button" class="tbtn"
                  @click="stepFrame(1)"
                  title="step fwd 1 frame (.)">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="4" x2="18" y2="20"/><polygon points="4 4 14 12 4 20 4 4"/></svg>
          </button>
          <button type="button" class="tbtn"
                  :disabled="!hasMarkers()"
                  @click="nextMarker()"
                  title="next marker (↓)">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><polygon points="5 4 16 12 5 20 5 4"/><line x1="19" y1="4" x2="19" y2="20"/></svg>
          </button>
        </div>

        <div class="tc-readout mono">
          <span class="cur" x-text="tc(current)">00:00:00:00</span>
          <span class="slash">/</span>
          <span class="end" x-text="tc(duration)">{{ duration_smpte }}</span>
        </div>

        <div class="grow"></div>

        <div class="transport-meta mono">
          <span>SHUTTLE</span>
          <span class="tag">1×</span>
          <span class="dot-sep">·</span>
          <span>AUDIO</span>
          <span class="tag">—</span>
        </div>
      </div>
    </div>
    {% endif %}

    <div class="kbdbar mono">
      <span class="group"><span class="kbd">Space</span><span>play/pause</span></span>
      <span class="sep"></span>
      <span class="group"><span class="kbd">,</span><span class="kbd">.</span><span>step 1f</span></span>
      <span class="sep"></span>
      <span class="group"><span class="kbd">↑</span><span class="kbd">↓</span><span>prev/next marker</span></span>
      <span class="sep"></span>
      <span class="group"><span class="kbd">J</span><span class="kbd">K</span><span class="kbd">L</span><span>shuttle</span></span>
      <span class="sep"></span>
      <span class="group"><span class="kbd">Home</span><span class="kbd">End</span><span>start/end</span></span>
    </div>
  </section>
```

Note: the duplicate `tc-readout` in the header (`.detail-hdr`) stays — it remains the at-a-glance readout while editing other things. Both bind to the same `current` and stay in sync via Alpine reactivity.

- [ ] **Step 2: Visual smoke-check (server already discipline-checked)**

Per `CLAUDE.md`, before starting a dev server:

```bash
/usr/sbin/lsof -nP -iTCP:8765 -sTCP:LISTEN
/bin/ps -ef | /usr/bin/grep -E '(uvicorn|backend\.app)' | /usr/bin/grep -v grep
```

If anything is listening on 8765, reuse it; otherwise start with `./run.sh`.

Open a clip-detail page in the browser. Confirm:
- The viewer shows the video with an HUD overlay top-left (timecode + frame counts).
- Below the video there's a timeline with marker ranges and 5 TC labels.
- Below the timeline is the transport-row with 5 buttons + TC readout + shuttle/audio strip.
- Below that is the keyboard-shortcuts strip (`Space ⋅ , . ⋅ ↑ ↓ ⋅ J K L ⋅ Home End`).
- Styles aren't loaded yet → it will look raw. That's expected; CSS lands in Task 4.

If anything fails to render or the page errors, check the browser console for Alpine errors and fix template syntax before continuing.

- [ ] **Step 3: Shut the server down gracefully (only if you started it)**

```bash
/bin/kill -TERM <pid>
```

Confirm the log shows `Application shutdown complete.` per `CLAUDE.md`. Skip this if you reused an already-running instance.

- [ ] **Step 4: Commit**

```bash
git add backend/app/templates/pages/clip_detail.html
git commit -m "feat(player): rebuild template with HUD + transport + kbdbar

Replaces the minimal native-controls player with viewer + HUD overlay,
a dedicated transport (timeline + 5-quintile TC labels + prev/step/play/
step/next buttons + TC readout + shuttle/audio strip), and a keyboard
shortcuts hint strip. Markers passed to Alpine as JSON, pre-sorted by
the view-model."
```

---

## Task 4: Port player + transport CSS into `app.css`

**Files:**
- Modify: `backend/app/static/app.css` — replace the existing player/timeline block (currently lines 246–355).

The design's CSS uses some custom properties not present in `app.css` (e.g. `--bg-2`, `--surface`, `--surface-2`, `--line-2`, `--text-4`, `--r-2`, `--f-mono`, `--accent`, `--accent-fg`, `--panel`). Before writing the new rules, audit `:root` in `app.css`. If any of those tokens are missing, add them with sensible dark-theme defaults (the design's defaults below are good starting values). Do this in the same edit.

**Design token defaults (use these if `app.css` is missing the variable):**

```
--bg-2:       #0c0e10;
--panel:      #14161a;
--surface:    #1b1e23;
--surface-2:  #23272e;
--line:       #2a2e35;
--line-2:     #353a43;
--text:       #e6e8eb;
--text-2:     #b6bac1;
--text-3:     #7a8089;
--text-4:     #4d525a;
--accent:     #5db4ff;
--accent-fg:  #061018;
--r-2:        4px;
--f-mono:     ui-monospace, "SF Mono", Menlo, Consolas, monospace;
```

- [ ] **Step 1: Audit and (if needed) add missing tokens**

Open `backend/app/static/app.css`, find the `:root` block (or wherever existing tokens are declared), and add any of the above that are missing. Don't redefine existing ones — keep what's there.

- [ ] **Step 2: Replace the player + timeline CSS block**

Find the existing block in `app.css` that starts at `.player-wrap {` (around line 246) and ends after the existing `.timeline .playhead` rules (around line 355). Replace the entire span with:

```css
/* ─── player ──────────────────────────────────────────────────────── */
.player-wrap {
  grid-area: play;
  background: #050607;
  border-right: 1px solid var(--line);
  display: flex; flex-direction: column;
  min-width: 0;
  padding: 0;
}

.viewer {
  flex: 1; min-height: 0;
  display: flex; align-items: center; justify-content: center;
  background: #000;
  position: relative;
  overflow: hidden;
  padding: 12px;
}
.video {
  width: 100%; height: 100%;
  object-fit: contain;
  background: #000;
}

/* HUD overlay */
.hud { position: absolute; font-family: var(--f-mono); pointer-events: none; }
.hud-tl { top: 26px; left: 26px; }
.hud-tc {
  font-size: 22px; font-weight: 600;
  color: #f5a623; letter-spacing: 0.02em;
  text-shadow: 0 0 12px rgba(0,0,0,0.6), 0 1px 0 rgba(0,0,0,0.8);
  font-variant-numeric: tabular-nums;
}
.hud-lbl {
  font-size: 9.5px; color: rgba(255,255,255,0.55);
  text-transform: uppercase; letter-spacing: 0.18em;
  margin-bottom: 2px;
}
.hud-val {
  font-size: 11px; color: rgba(255,255,255,0.85);
  font-variant-numeric: tabular-nums;
  text-shadow: 0 1px 0 rgba(0,0,0,0.7);
}

/* transport */
.transport {
  background: var(--panel);
  border-top: 1px solid var(--line);
  display: flex; flex-direction: column;
  padding: 8px 14px 10px;
  gap: 8px;
}

.timeline {
  position: relative;
  height: 36px;
  background: var(--bg-2);
  border: 1px solid var(--line);
  border-radius: var(--r-2);
  overflow: hidden;
}
.timeline .ticks {
  position: absolute; inset: 0;
  background-image:
    repeating-linear-gradient(90deg, var(--line)   0 1px, transparent 1px 60px),
    repeating-linear-gradient(90deg, var(--line-2) 0 1px, transparent 1px 300px);
  pointer-events: none;
}
.timeline .ranges {
  position: absolute; left: 0; right: 0; top: 6px; bottom: 6px;
}
.timeline .range {
  position: absolute; top: 0; bottom: 0;
  background: color-mix(in oklab, var(--accent) 35%, transparent);
  border-left: 2px solid var(--accent);
  border-right: 2px solid var(--accent);
  border-radius: 2px;
  min-width: 4px;
  cursor: pointer;
}
.timeline .range:hover {
  background: color-mix(in oklab, var(--accent) 55%, transparent);
}
.timeline .range.active {
  background: color-mix(in oklab, var(--accent) 55%, transparent);
  box-shadow:
    0 0 0 1px var(--accent),
    0 0 18px color-mix(in oklab, var(--accent) 40%, transparent);
}
.timeline .playhead {
  position: absolute; top: -2px; bottom: -2px; width: 2px;
  background: #fff;
  box-shadow: 0 0 8px rgba(255,255,255,0.6);
  pointer-events: none;
}
.timeline .playhead::before {
  content: ""; position: absolute; top: -4px; left: -5px;
  width: 12px; height: 6px; background: #fff;
  clip-path: polygon(0 0, 100% 0, 50% 100%);
}
.timeline .tc-labels {
  position: absolute; left: 0; right: 0; bottom: 1px;
  display: flex; justify-content: space-between;
  padding: 0 4px;
  font-family: var(--f-mono); font-size: 9.5px;
  color: var(--text-4);
  pointer-events: none;
}

.transport-row {
  display: flex; align-items: center; gap: 14px;
  flex-wrap: wrap;
}
.transport-row .grow { flex: 1; }

.transport-btns { display: flex; gap: 4px; }
.tbtn {
  width: 30px; height: 26px;
  display: flex; align-items: center; justify-content: center;
  background: var(--surface);
  border: 1px solid var(--line-2);
  border-radius: var(--r-2);
  color: var(--text-2);
  cursor: pointer;
}
.tbtn:hover:not(:disabled) { color: var(--text); background: var(--surface-2); }
.tbtn:disabled { opacity: 0.35; cursor: not-allowed; }
.tbtn.play {
  background: var(--accent); color: var(--accent-fg);
  border-color: transparent; width: 36px;
}
.tbtn svg { width: 14px; height: 14px; }

.transport .tc-readout {
  font-family: var(--f-mono); font-size: 14px; font-weight: 600;
  color: var(--accent);
  letter-spacing: 0.02em;
}
.transport .tc-readout .slash { color: var(--text-3); margin: 0 6px; font-weight: 400; }
.transport .tc-readout .end   { color: var(--text-2); font-weight: 400; }

.transport-meta {
  display: flex; align-items: center; gap: 6px;
  font-family: var(--f-mono); font-size: 11px; color: var(--text-3);
}
.transport-meta .tag {
  display: inline-flex; align-items: center;
  height: 18px; padding: 0 6px; border-radius: 3px;
  background: var(--surface); border: 1px solid var(--line-2);
  color: var(--text-2); font-size: 10.5px;
}
.transport-meta .dot-sep { color: var(--text-4); margin: 0 4px; }

/* keyboard shortcuts strip */
.kbdbar {
  background: var(--panel);
  border-top: 1px solid var(--line);
  display: flex; align-items: center; gap: 14px;
  padding: 8px 14px;
  font-family: var(--f-mono); font-size: 10.5px;
  color: var(--text-3);
  overflow-x: auto;
}
.kbdbar::-webkit-scrollbar { display: none; }
.kbdbar .group { display: flex; align-items: center; gap: 6px; flex: none; }
.kbdbar .sep   { width: 1px; height: 16px; background: var(--line); }
.kbd {
  display: inline-flex; align-items: center; justify-content: center;
  min-width: 18px; height: 18px; padding: 0 5px;
  background: var(--surface);
  border: 1px solid var(--line-2);
  border-bottom-width: 2px;
  border-radius: 3px;
  font-family: var(--f-mono); font-size: 10px;
  color: var(--text-2);
}
```

- [ ] **Step 3: Visual verification**

Reuse a running dev server if there is one (check `/usr/sbin/lsof -nP -iTCP:8765 -sTCP:LISTEN`); otherwise start with `./run.sh`.

Open a clip-detail page and walk through the spec's manual checklist (`docs/specs/2026-05-20-player-redesign-design.md` § Testing):

1. Markers render as accent-colored ranges; hovering brightens them; clicking one seeks + plays.
2. Press **Space** — toggles play/pause; the button icon swaps between play triangle and pause bars.
3. Press **,** and **.** — `current` shifts by exactly 1 frame (40ms at 25fps). HUD frame counter increments by 1.
4. Press **↑**/**↓** — playhead jumps to prev/next marker; at boundaries the buttons are visually disabled (~35% opacity).
5. Quintile TC labels show 5 strictly-increasing timecodes spanning the clip.
6. The range whose `[in_secs, out_secs]` brackets `current` gets the brighter `.active` halo.
7. Open the cache search input (or any text input) and press **Space** — must NOT toggle play.
8. Load a clip with zero markers — prev/next buttons disabled; player still plays.
9. Double-click the video — enters fullscreen.

If any item fails, fix in this task before committing.

- [ ] **Step 4: Shut the server down gracefully (only if you started it)**

```bash
/bin/kill -TERM <pid>
```

Confirm `Application shutdown complete.` in the log.

- [ ] **Step 5: Commit**

```bash
git add backend/app/static/app.css
git commit -m "feat(player): port HUD + transport + kbdbar CSS

Visual parity with the Claude Design mockup's player. Uses
existing CSS tokens with sensible dark-theme fallbacks for any
that were missing. Drops native <video> controls; the custom
transport replaces them and double-click on the viewer goes
fullscreen."
```

---

## Task 5: Full regression test

**Files:** none modified.

- [ ] **Step 1: Run the entire pytest suite**

Run: `.venv/bin/python -m pytest -q`

Expected: all tests pass (the only change touching backend behavior is the marker sort, which is covered by Task 1's tests and shouldn't break any existing assertions).

If anything regresses, fix it inline — do not skip or `xfail`.

- [ ] **Step 2: Confirm git tree is clean**

Run: `git status`

Expected: `nothing to commit, working tree clean`.

---

## Self-Review Notes (for the implementing engineer)

Before opening a PR, re-read `docs/specs/2026-05-20-player-redesign-design.md` and confirm:

1. **Spec coverage:** every "IN scope" item in the spec maps to something in Tasks 1–4. Risk markers, right-rail tabs, decisions, "Apply to CatDV", Set IN/OUT — confirm none of those appeared in your diff.
2. **Token reuse:** if you had to add new CSS variables in Task 4, document them in the PR description so they aren't surprises.
3. **Native controls:** confirm the `<video>` element has no `controls` attribute in the final template.
4. **Header TC readout:** the duplicate in `.detail-hdr` is intentional — leave it; it's the at-a-glance readout for when the user is reading marker metadata in the right column.

---

## Handover: subagent-driven remote execution

This plan is structured for handoff to a remote agent using `superpowers:subagent-driven-development`. Each task is independently committable; review checkpoints between tasks let the driver catch drift early.

**Recommended dispatch order (sequential — each task assumes the previous one is committed):**

1. **Task 1** — backend view-model. Pure pytest TDD. Self-contained, no UI knowledge needed.
2. **Task 2** — `player.js` rewrite. No tests, but the file is verbatim from this plan; the subagent's job is "paste, save, syntax-check, commit." Don't let it improvise.
3. **Task 3** — template rewrite. Same: verbatim paste. The visual smoke-check at the end is just "page renders without errors"; full visual verification happens after Task 4 lands styles.
4. **Task 4** — CSS port. Verify against the spec's manual checklist before committing. **This is the only task that benefits from a human-in-the-loop review** because visual parity is subjective.
5. **Task 5** — regression sweep + clean tree confirmation.

**Driver instructions to embed in each subagent dispatch:**

- Reference this plan path verbatim: `docs/plans/2026-05-20-player-redesign.md`.
- Reference the spec for "why": `docs/specs/2026-05-20-player-redesign-design.md`.
- Reference `CLAUDE.md` for **CatDV session discipline** — any dev-server start MUST check for an existing instance first and MUST shut down with `kill -TERM` (never `-9`). The license seat is the constraint, not the port.
- The implementing agent must NOT add scope. If they think Set IN/OUT or risk markers belong, file a follow-up note in the PR description — do not implement.
- Each task ends with a commit. If a task's verification step fails, the agent fixes inline before committing; partial work doesn't merge.

**Review checkpoints between tasks:**

After Task 1: verify the new pytest file passes and existing view-model tests still pass.
After Task 2: skim the new `player.js` for typos / accidental edits to logic blocks not specified here.
After Task 3: load a clip-detail page; confirm the page renders without console errors (styling will be raw — that's fine).
After Task 4: run the spec's manual checklist end-to-end. This is the gate.
After Task 5: clean `git status`, all tests green, PR-ready.

**If the remote agent gets stuck:** the unblock path is almost always "re-read the spec, paste the code from the plan verbatim, commit." Don't let the agent re-derive the design or the code — this plan is the source of truth.
