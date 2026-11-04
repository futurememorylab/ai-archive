// playerTimelineMixin — pointer and shuttle controls composed into
// Alpine.data("player") (player.js spreads it in). Three things live here:
//   * marker drag: edit a draft marker's in/out on the timeline,
//   * scrub: press on the timeline and drag the playhead,
//   * J/K/L shuttle: L plays forward, J backward, each extra press doubles
//     the speed (1× → 2× → 4× → 8×), K stops. The SHUTTLE tag in the transport
//     shows the live speed via shuttleLabel().
// Methods use the player's own state (duration, fps, canPlay, $refs.video,
// seek, _requestPlay, startEdit), so this only works inside player().

const SHUTTLE_MAX = 8;

function playerTimelineMixin() {
  return {
    // ─── timeline marker drag (review draft markers, edit-activated) ─
    // Draft markers carry item_id + in_secs/out_secs (see draft_view.py).
    // Dragging mutates the Alpine model (the .range :style binds to it so
    // the bar moves live); persisting happens on Save via reviewMixin.saveEdit().
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

    // Follow the pointer until it is released: onMove(ev) per move, onUp() once.
    _trackPointer(onMove, onUp) {
      const up = () => {
        window.removeEventListener("pointermove", onMove);
        window.removeEventListener("pointerup", up);
        onUp();
      };
      window.addEventListener("pointermove", onMove);
      window.addEventListener("pointerup", up);
    },

    startMarkerDrag(e, id, mode) {
      e.preventDefault(); e.stopPropagation();
      // Enter the buffered edit (snapshots for Cancel, auto-saves any other
      // open edit). reviewMixin provides startEdit on the clip-detail page;
      // fall back to the raw flag elsewhere.
      if (typeof this.startEdit === "function") this.startEdit(id, { seek: false });
      else this.editingItemId = id;
      const m = this._draftItem(id); if (!m) return;
      this._drag = { id, mode, t0: this._xToSecs(e.clientX), in0: m.in_secs, out0: m.out_secs };
      if (e.target.setPointerCapture) e.target.setPointerCapture(e.pointerId);
      this._trackPointer((ev) => this._onMarkerDrag(ev), () => { this._drag = null; });
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

    // ─── scrub: drag the playhead along the timeline ──────────────
    // A plain click still jumps there and plays (as before). A drag pauses,
    // follows the pointer (one seek per animation frame), and on release
    // resumes only if the clip was playing when the drag began.
    startScrub(e) {
      if (e.button !== 0 || !this.duration) return;
      e.preventDefault();
      const v = this.$refs.video;
      const wasPlaying = this.playing;
      let moved = false, raf = 0, x = e.clientX;
      this._setShuttle(0);
      this.seek(this._xToSecs(x), { play: false });
      const apply = () => { raf = 0; this.current = this._xToSecs(x); this.seek(this.current, { play: false }); };
      this._trackPointer((ev) => {
        if (Math.abs(ev.clientX - e.clientX) > 3) moved = true;
        x = ev.clientX;
        if (!raf) raf = requestAnimationFrame(apply);
      }, () => {
        if (raf) { cancelAnimationFrame(raf); apply(); }
        if (v && (!moved || wasPlaying)) this._requestPlay();
      });
    },

    // ─── J/K/L shuttle ────────────────────────────────────────────
    // shuttleRate: 0 stopped, >0 forward speed, <0 backward speed.
    shuttleRate: 0,
    _rev: null,   // requestAnimationFrame id of the backward-play loop

    // Called once from player.init(): a pause or end that we did not start
    // (Space, a step, the clip ending) drops the shuttle back to normal speed.
    _shuttleInit(v) {
      const reset = () => {
        if (this._rev) return;   // backward play keeps the video paused on purpose
        this.shuttleRate = 0;
        v.playbackRate = 1;
      };
      v.addEventListener("pause", reset);
      v.addEventListener("ended", reset);
      v.addEventListener("play", () => {
        if (this._rev) this._stopReverse();
        if (this.shuttleRate <= 0) this.shuttleRate = v.playbackRate;
      });
    },

    // dir = 1 (L) or -1 (J): start that way at 1×, or double the speed if
    // already going that way.
    shuttle(dir) {
      const r = this.shuttleRate;
      const same = Math.sign(r) === dir;
      this._setShuttle(same ? dir * Math.min(SHUTTLE_MAX, Math.abs(r) * 2) : dir);
    },

    _setShuttle(rate) {
      const v = this.$refs.video;
      this._stopReverse();
      this.shuttleRate = rate;
      if (!v) return;
      if (rate === 0) { v.playbackRate = 1; v.pause(); return; }
      if (!this.canPlay) { this.shuttleRate = 0; return; }
      if (rate > 0) {
        v.playbackRate = rate;
        if (v.paused) this._requestPlay();
        return;
      }
      // Browsers can't play video backwards, so step the playhead back each
      // animation frame while paused. Coarser than forward play, fine for review.
      v.playbackRate = 1;
      this._startReverse(v);
      v.pause();
    },

    _startReverse(v) {
      let last = performance.now();
      let t = v.currentTime;
      const tick = (now) => {
        t = Math.max(0, t - Math.abs(this.shuttleRate) * (now - last) / 1000);
        last = now;
        if (!v.seeking) { v.currentTime = t; this.current = t; }
        if (t <= 0) { this._setShuttle(0); return; }
        this._rev = requestAnimationFrame(tick);
      };
      this._rev = requestAnimationFrame(tick);
    },

    _stopReverse() {
      if (this._rev) cancelAnimationFrame(this._rev);
      this._rev = null;
    },

    shuttleLabel() {
      const r = this.shuttleRate;
      if (!r) return "1×";
      return (r < 0 ? "−" : "") + Math.abs(r) + "×";
    },
  };
}
