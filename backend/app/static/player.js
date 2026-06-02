// Alpine player component for clip_detail.html.
// Markers must arrive sorted ascending by in_secs (see clip_detail view-model);
// prev/next-marker navigation depends on it.

document.addEventListener("alpine:init", () => {
  Alpine.data("player", (fps, duration, markers, draftMarkers) => ({
    fps: fps || 25,
    duration: duration || 0,
    current: 0,
    playing: false,
    markers: Array.isArray(markers) ? markers : [],
    draftMarkers: Array.isArray(draftMarkers) ? draftMarkers : [],

    // Review-mode inline editor: at most one item expanded at a time.
    // Lives on the player root so the panel (child reviewQueue scope) and a
    // later timeline-drag task can both react to which item is being edited.
    editingItemId: null,

    activeMarkers() {
      return this.scope === "draft" ? this.draftMarkers : this.markers;
    },

    init() {
      const v = this.$refs.video;
      if (!v) return;
      v.addEventListener("timeupdate", () => {
        // Quantize to frame boundary: timeupdate fires ~4×/sec and every
        // `current` write fans out to N marker `isMarkerActive` recomputes.
        if (Math.abs(v.currentTime - this.current) >= 0.5 / this.fps) {
          this.current = v.currentTime;
        }
      });
      v.addEventListener("loadedmetadata", () => {
        if (!this.duration || isNaN(this.duration)) this.duration = v.duration;
      });
      v.addEventListener("play",  () => { this.playing = true; });
      v.addEventListener("pause", () => { this.playing = false; });
    },

    // ─── timeline marker drag (review draft markers, edit-activated) ─
    // Draft markers carry item_id + in_secs/out_secs (see draft_view.py).
    // Dragging mutates the Alpine model (the .range :style binds to it so
    // the bar moves live), then persists via the review decision endpoint.
    _drag: null,
    _timelineEl() { return this.$root.querySelector(".timeline"); },
    _xToSecs(clientX) {
      const el = this._timelineEl();
      if (!el) return 0;
      const r = el.getBoundingClientRect();
      if (!r.width || !this.duration) return 0;
      const frac = Math.max(0, Math.min(1, (clientX - r.left) / r.width));
      return frac * this.duration;
    },
    _draftItem(id) { return this.draftMarkers.find(m => m.item_id === id); },

    startMarkerDrag(e, id, mode) {
      e.preventDefault(); e.stopPropagation();
      this.editingItemId = id;
      const m = this._draftItem(id); if (!m) return;
      this._drag = { id, mode, t0: this._xToSecs(e.clientX), in0: m.in_secs, out0: m.out_secs };
      if (e.target.setPointerCapture) e.target.setPointerCapture(e.pointerId);
      const move = (ev) => this._onMarkerDrag(ev);
      const up = () => {
        this._endMarkerDrag();
        window.removeEventListener("pointermove", move);
        window.removeEventListener("pointerup", up);
      };
      window.addEventListener("pointermove", move);
      window.addEventListener("pointerup", up);
    },

    _onMarkerDrag(e) {
      const d = this._drag; if (!d) return;
      const m = this._draftItem(d.id); if (!m) return;
      const dt = this._xToSecs(e.clientX) - d.t0;
      const dur = this.duration;
      if (d.mode === "move") {
        const len = (d.out0 != null ? d.out0 : d.in0) - d.in0;
        m.in_secs = Math.max(0, Math.min(d.in0 + dt, dur - len));
        if (d.out0 != null) m.out_secs = m.in_secs + len;
      } else if (d.mode === "in") {
        m.in_secs = Math.max(0, Math.min(d.in0 + dt, (m.out_secs != null ? m.out_secs : dur)));
      } else if (d.mode === "out") {
        m.out_secs = Math.min(dur, Math.max(d.out0 + dt, m.in_secs));
      }
    },

    _endMarkerDrag() {
      const d = this._drag;
      this._drag = null;
      if (d) this._persistMarker(d.id);
    },

    nudgeMarker(dir, fine) {
      if (this.editingItemId == null) return;
      const m = this._draftItem(this.editingItemId); if (!m) return;
      const step = fine ? (1 / (this.fps || 25)) : 1.0;   // Shift = 1 frame, else 1 second
      m.in_secs = Math.max(0, m.in_secs + dir * step);
      if (m.out_secs != null) m.out_secs = Math.max(m.in_secs, m.out_secs + dir * step);
      this._persistMarker(this.editingItemId);
    },

    // SMPTE readout for the in/out edge of a draft marker (read-only panel).
    riReadout(id, edge) {
      const m = this._draftItem(id);
      const v = m ? (edge === "in" ? m.in_secs : m.out_secs) : null;
      return v == null ? "—" : this.tc(v);
    },

    // Persist the *whole* marker value: the backend's COALESCE on edited_value
    // replaces it wholesale, and write_queue requires {name, in:{secs}} or the
    // marker is silently dropped on apply. So always send the full shape.
    _persistMarker(id) {
      const m = this._draftItem(id); if (!m) return;
      m.status = "accepted";
      const edited = {
        name: m.name || "",
        category: m.category != null ? m.category : null,
        description: m.description != null ? m.description : null,
        in: { secs: m.in_secs },
      };
      if (m.color != null) edited.color = m.color;
      if (m.out_secs != null) edited.out = { secs: m.out_secs };
      fetch(`/api/review/items/${id}/decision`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ decision: "accepted", edited_value: edited }),
      }).then(r => {
        if (!r.ok) {
          console.error(`marker persist failed for item ${id}: ${r.status}`);
          Alpine.store("toast").push(
            `Marker edit not saved (HTTP ${r.status}).`,
            { level: "error" },
          );
        }
      }).catch(err => {
        console.error(`marker persist error for item ${id}`, err);
        Alpine.store("toast").push(
          `Marker edit not saved: ${err.message || String(err)}`,
          { level: "error" },
        );
      });
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

    seekFromEvent(e) {
      // Rect of the timeline itself, not e.target — clicks may land on
      // .ticks/.ranges children whose offsetX is relative to those.
      const rect = e.currentTarget.getBoundingClientRect();
      if (!rect.width || !this.duration) return;
      const frac = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
      this.seek(frac * this.duration);
    },

    _jumpMarker(direction) {
      const list = this.activeMarkers();
      if (!list.length) return;
      const EPS = 0.001;
      const ahead = direction > 0;
      const pick =
        (ahead
          ? list.find(m => m.in_secs > this.current + EPS)
          : [...list].reverse().find(m => m.in_secs < this.current - EPS))
        ?? list[ahead ? 0 : list.length - 1];
      this.seek(pick.in_secs);
    },

    prevMarker() { this._jumpMarker(-1); },
    nextMarker() { this._jumpMarker(1); },

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
      return this.tc((i / 4) * this.duration);
    },

    isMarkerActive(m) {
      if (m.in_secs == null) return false;
      const out = m.out_secs != null ? m.out_secs : m.in_secs + 0.04;
      return this.current >= m.in_secs && this.current <= out;
    },

    handleKey(e) {
      const t = e.target;
      if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" ||
                t.isContentEditable)) return;
      if (e.metaKey || e.ctrlKey || e.altKey) return;

      switch (e.key) {
        case " ":
        case "Spacebar":
          e.preventDefault(); this.togglePlay(); break;
        case ",":
          e.preventDefault(); this.stepFrame(-1); break;
        case ".":
          e.preventDefault(); this.stepFrame(1); break;
        case "ArrowLeft":
          // While a marker is being edited, ←/→ nudge it (Shift = 1 frame);
          // otherwise fall through to the browser/no-op seek default.
          if (this.editingItemId != null) { e.preventDefault(); this.nudgeMarker(-1, e.shiftKey); }
          break;
        case "ArrowRight":
          if (this.editingItemId != null) { e.preventDefault(); this.nudgeMarker(1, e.shiftKey); }
          break;
        case "ArrowUp":
          if (this.activeMarkers().length) { e.preventDefault(); this.prevMarker(); }
          break;
        case "ArrowDown":
          if (this.activeMarkers().length) { e.preventDefault(); this.nextMarker(); }
          break;
        case "j": case "J":
          e.preventDefault(); this.stepFrame(-1); break;
        case "k": case "K":
          e.preventDefault(); this.pause(); break;
        case "l": case "L":
          e.preventDefault(); this.play(); break;
        case "Home":
          e.preventDefault(); this.seek(0); break;
        case "End":
          e.preventDefault(); this.seek(this.duration); break;
      }
    },
  }));
});
