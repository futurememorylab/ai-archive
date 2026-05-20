// Alpine player component for clip_detail.html.
// Markers must arrive sorted ascending by in_secs (see clip_detail view-model);
// prev/next-marker navigation depends on it.

document.addEventListener("alpine:init", () => {
  Alpine.data("player", (fps, duration, markers) => ({
    fps: fps || 25,
    duration: duration || 0,
    current: 0,
    playing: false,
    markers: Array.isArray(markers) ? markers : [],

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
      if (!this.markers.length) return;
      const EPS = 0.001;
      const ahead = direction > 0;
      const pick =
        (ahead
          ? this.markers.find(m => m.in_secs > this.current + EPS)
          : [...this.markers].reverse().find(m => m.in_secs < this.current - EPS))
        ?? this.markers[ahead ? 0 : this.markers.length - 1];
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
        case "ArrowUp":
          if (this.markers.length) { e.preventDefault(); this.prevMarker(); }
          break;
        case "ArrowDown":
          if (this.markers.length) { e.preventDefault(); this.nextMarker(); }
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
