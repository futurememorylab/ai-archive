// Studio split-pane resizers — hand-rolled, no Node dependency (ADR 0001).
//
// A thin divider element (.studio-resizer with data-studio-resizer="player"
// | "cmp") sits between two panes. Dragging it writes a CSS custom property
// on the container, which drives that container's grid/flex track sizes:
//
//   player divider → on .studio-right:
//     - layout 'under': vertical drag → --studio-player-h (px)  [row-resize]
//     - layout 'right': horizontal drag → --studio-player-w (px) [col-resize]
//   cmp divider → on .studio-compare-row:
//     - always horizontal drag → --studio-cmp-cur (% of row width) [col-resize]
//
// One delegated `pointerdown` listener on `document` matches `.studio-resizer`,
// so dividers that are HTMX-injected or CSS-toggled need no re-wiring. We use
// plain pointer events + setPointerCapture; no Alpine init / htmx.process —
// htmxAlpine.js stays the single HTMX↔Alpine lifecycle owner.
//
// Sizes round-trip through Alpine.store('studio') (playerH / playerW / cmpCur
// + saveResize()), which persists them into localStorage['studio.layoutPrefs'].

// Pure helper: new size = (start + delta) clamped to [lo, hi]. Mirror of
// tests/unit/test_studio_resize_clamp.py::clamp_size — keep the two in sync.
function clampSize(start, delta, lo, hi) {
  const size = start + delta;
  if (size < lo) return lo;
  if (size > hi) return hi;
  return size;
}

(function () {
  // Sane clamp bounds (see spec "Error handling / edge cases").
  const PLAYER_MIN_PX = 160;
  const PLAYER_MAX_FRAC = 0.7; // ≤ 70% of the container
  const CMP_MIN_PCT = 20;
  const CMP_MAX_PCT = 80;

  let drag = null; // { kind, el, container, axis, startVar, startPx, startPct, ... }

  function store() {
    return window.Alpine?.store?.("studio") ?? null;
  }

  function onPointerDown(evt) {
    if (drag) return;
    const el = evt.target.closest(".studio-resizer");
    if (!el) return;
    const kind = el.dataset.studioResizer; // "player" | "cmp"
    if (kind === "player") {
      const container = el.closest(".studio-right");
      if (!container) return;
      const layout = store()?.layout || "under";
      const slot = container.querySelector(".studio-player-slot");
      if (layout === "right") {
        const startPx = slot ? slot.getBoundingClientRect().width : 320;
        drag = {
          kind,
          el,
          container,
          axis: "x",
          startPx,
          containerSize: container.getBoundingClientRect().width,
          startClient: evt.clientX,
          cssVar: "--studio-player-w",
          storeField: "playerW",
        };
      } else {
        const startPx = slot ? slot.getBoundingClientRect().height : 320;
        drag = {
          kind,
          el,
          container,
          axis: "y",
          startPx,
          containerSize: container.getBoundingClientRect().height,
          startClient: evt.clientY,
          cssVar: "--studio-player-h",
          storeField: "playerH",
        };
      }
    } else if (kind === "cmp") {
      const container = el.closest(".studio-compare-row");
      if (!container) return;
      const cur = container.querySelector('.studio-prompt-card[data-side="cur"]');
      const rowW = container.getBoundingClientRect().width || 1;
      const startPct = cur
        ? (cur.getBoundingClientRect().width / rowW) * 100
        : 50;
      drag = {
        kind,
        el,
        container,
        axis: "x",
        startPct,
        containerSize: rowW,
        startClient: evt.clientX,
        cssVar: "--studio-cmp-cur",
        storeField: "cmpCur",
        isPct: true,
      };
    } else {
      return;
    }

    try {
      el.setPointerCapture(evt.pointerId);
    } catch (e) {
      /* setPointerCapture can throw if the pointer is already gone */
    }
    document.body.classList.add("studio-resizing");
    evt.preventDefault();
  }

  function onPointerMove(evt) {
    if (!drag) return;
    const delta =
      drag.axis === "x"
        ? evt.clientX - drag.startClient
        : evt.clientY - drag.startClient;

    if (drag.isPct) {
      const deltaPct = (delta / (drag.containerSize || 1)) * 100;
      const pct = clampSize(drag.startPct, deltaPct, CMP_MIN_PCT, CMP_MAX_PCT);
      drag.lastValue = (Math.round(pct * 10) / 10) + "%";
      drag.container.style.setProperty(drag.cssVar, drag.lastValue);
    } else {
      const maxPx = Math.max(
        PLAYER_MIN_PX,
        drag.containerSize * PLAYER_MAX_FRAC,
      );
      const px = clampSize(drag.startPx, delta, PLAYER_MIN_PX, maxPx);
      drag.lastValue = Math.round(px) + "px";
      drag.container.style.setProperty(drag.cssVar, drag.lastValue);
    }
  }

  function onPointerUp(evt) {
    if (!drag) return;
    try {
      drag.el.releasePointerCapture(evt.pointerId);
    } catch (e) {
      /* already released */
    }
    document.body.classList.remove("studio-resizing");
    const s = store();
    if (s && drag.lastValue != null) {
      s[drag.storeField] = drag.lastValue;
      if (typeof s.saveResize === "function") s.saveResize();
    }
    drag = null;
  }

  document.addEventListener("pointerdown", onPointerDown);
  document.addEventListener("pointermove", onPointerMove);
  document.addEventListener("pointerup", onPointerUp);
  document.addEventListener("pointercancel", onPointerUp);
})();

// Exposed for the Python↔JS parity check (tests/unit/test_studio_resize_clamp.py).
window.clampSize = clampSize;
