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
      // offsetSeconds backdates the start so the timer resumes from time
      // already elapsed (e.g. a job that began before a page reload). Defaults
      // to 0 — a fresh run starts at 0:00.
      start(onTick, offsetSeconds = 0) {
        const offMs = Math.max(0, Number(offsetSeconds) || 0) * 1000;
        startMs = performance.now() - offMs;
        onTick(fmtTimecode(offMs / 1000));
        id = setInterval(() => {
          onTick(fmtTimecode((performance.now() - startMs) / 1000));
        }, 1000);
      },
      stop() {
        if (id !== null) { clearInterval(id); id = null; }
      },
    };
  }
  // Shared cache-progress probe: reads the live prefetch queue and returns
  // { status, pct } for a clip's active row, or null when the clip has no
  // active row. `pct` is an integer 0–100 only while `downloading` with a
  // known total, else null. Both the per-clip cache control (cacheActions)
  // and the annotate button (clipAnnotate) read progress through this single
  // helper so they always show the same percentage math.
  async function cacheProgressForClip(clipId) {
    let res;
    try {
      res = await fetch("/api/cache/prefetch/queue");
    } catch {
      return null; // offline / transient
    }
    if (!res.ok) return null;
    let q;
    try {
      q = await res.json();
    } catch {
      return null;
    }
    const row = (q.active || []).find(
      (r) => String(r.provider_clip_id) === String(clipId),
    );
    if (!row) return null;
    const pct =
      row.status === "downloading" && row.bytes_total > 0
        ? Math.floor((100 * row.bytes_downloaded) / row.bytes_total)
        : null;
    return { status: row.status, pct };
  }

  window.fmtTimecode = fmtTimecode;
  window.cacheProgressForClip = cacheProgressForClip;
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
