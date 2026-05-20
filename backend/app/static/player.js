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
