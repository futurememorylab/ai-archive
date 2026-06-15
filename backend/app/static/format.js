// Shared formatting + UI helpers. Loaded first so window.* exist before
// Alpine/feature scripts initialize. No build step (ADR 0001) — plain globals.
(function () {
  function fmtTimecode(seconds) {
    const s = Math.max(0, Math.floor(Number(seconds) || 0));
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    const sec = s % 60;
    const pad = (x) => String(x).padStart(2, "0");
    return h > 0 ? `${h}:${pad(m)}:${pad(sec)}` : `${m}:${pad(sec)}`;
  }
  function fmtBytes(n) {
    n = Number(n) || 0;
    if (!n) return "0 B";
    const u = ["B", "KB", "MB", "GB", "TB"];
    let i = 0;
    while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
    return (i === 0 ? n.toFixed(0) : n.toFixed(1)) + " " + u[i];
  }
  function fmtUsd(x) {
    if (x === null || x === undefined || isNaN(Number(x))) return "—";
    const n = Number(x);
    return "$" + (n < 0.1 ? n.toFixed(3) : n.toFixed(2));
  }
  function autosize(el) {
    if (!el) return;
    el.style.height = "auto";
    el.style.height = el.scrollHeight + "px";
  }
  // Shared 1Hz elapsed timer, mirroring the Studio run button's ticker
  // (studioStore.js). Reuses fmtTimecode for the label. Returns
  // { start(onTick), stop() }; onTick receives the formatted label
  // ("0:00", "0:12", "1:05", …) immediately and then once per second.
  function elapsedTimer() {
    let startMs = 0;
    let id = null;
    return {
      start(onTick) {
        startMs = performance.now();
        onTick(fmtTimecode(0));
        id = setInterval(() => {
          onTick(fmtTimecode((performance.now() - startMs) / 1000));
        }, 1000);
      },
      stop() {
        if (id !== null) { clearInterval(id); id = null; }
      },
    };
  }
  window.fmtTimecode = fmtTimecode;
  window.elapsedTimer = elapsedTimer;
  window.fmtBytes = fmtBytes;
  window.fmtUsd = fmtUsd;
  window.autosize = autosize;
  // Autosize .txt-area to content, unless it opts out with .no-autosize
  // (fields that are sized to the viewport instead, so they don't grow the page).
  document.addEventListener("input", (e) => {
    const t = e.target;
    if (t.classList && t.classList.contains("txt-area") && !t.classList.contains("no-autosize")) {
      autosize(t);
    }
  });
  document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll("textarea.txt-area:not(.no-autosize)").forEach(autosize);
  });
})();
