document.addEventListener("alpine:init", () => {
  Alpine.data("player", (fps, duration) => ({
    fps: fps || 25,
    duration: duration || 0,
    current: 0,
    init() {
      const v = this.$refs.video;
      if (!v) return;
      v.addEventListener("timeupdate", () => { this.current = v.currentTime; });
      v.addEventListener("loadedmetadata", () => {
        if (!this.duration || isNaN(this.duration)) this.duration = v.duration;
      });
    },
    seek(secs) {
      const v = this.$refs.video;
      if (!v) return;
      v.currentTime = Math.max(0, Math.min(secs, v.duration || secs));
      v.play().catch(() => {});
    },
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
    pct(secs) {
      if (!this.duration) return 0;
      return (secs / this.duration) * 100;
    },
  }));
});
