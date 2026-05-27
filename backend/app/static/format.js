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
  function autosize(el) {
    if (!el) return;
    el.style.height = "auto";
    el.style.height = el.scrollHeight + "px";
  }
  window.fmtTimecode = fmtTimecode;
  window.fmtBytes = fmtBytes;
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
