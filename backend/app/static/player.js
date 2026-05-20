/* Alpine.js player component — fleshed out in Task 6. */
document.addEventListener("alpine:init", () => {
  Alpine.data("player", () => ({
    fps: 25,
    duration: 0,
    current: 0,
    init() {},
    seek() {},
    tc() { return "00:00:00:00"; },
  }));
});
