// Split-pane resizers — hand-rolled, no Node dependency (ADR 0001).
//
// A thin divider element (.studio-resizer with data-studio-resizer="player"
// | "cmp" | "detail-side") sits between two panes. Dragging it writes a CSS
// custom property on the container, which drives that container's grid/flex
// track sizes:
//
//   player divider → on .studio-right:
//     - layout 'under': vertical drag → --studio-player-h (px)  [row-resize]
//     - layout 'right': horizontal drag → --studio-player-w (px) [col-resize]
//   cmp divider → on .studio-compare-row:
//     - always horizontal drag → --studio-cmp-cur (% of row width) [col-resize]
//   detail-side divider → on .detail (clip-detail page):
//     - horizontal drag → --detail-side-w (px) sizing the RIGHT (anno) column
//       [col-resize]; reuses the same component so the clip page doesn't grow
//       a second resizer.
//
// One delegated `pointerdown` listener on `document` matches `.studio-resizer`,
// so dividers that are HTMX-injected or CSS-toggled need no re-wiring. We use
// plain pointer events + setPointerCapture; no Alpine init / htmx.process —
// htmxAlpine.js stays the single HTMX↔Alpine lifecycle owner.
//
// Studio sizes round-trip through Alpine.store('studio') (playerH / playerW /
// cmpCur + saveResize()) → localStorage['studio.layoutPrefs']. The clip-detail
// divider has no studio store, so it persists straight to
// localStorage['catdv:detailLayout'] (restored by an inline script on the page).

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
      // `right` resizes the player column (width); `under` the player row
      // (height). Same descriptor, parameterized by axis/dimension.
      const horizontal = (store()?.layout || "under") === "right";
      const dim = horizontal ? "width" : "height";
      const slot = container.querySelector(".studio-player-slot");
      drag = {
        kind,
        el,
        container,
        axis: horizontal ? "x" : "y",
        startPx: slot ? slot.getBoundingClientRect()[dim] : 320,
        containerSize: container.getBoundingClientRect()[dim],
        startClient: horizontal ? evt.clientX : evt.clientY,
        cssVar: horizontal ? "--studio-player-w" : "--studio-player-h",
        storeField: horizontal ? "playerW" : "playerH",
      };
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
    } else if (kind === "detail-side") {
      // Clip-detail page: divider between the player (left) and the annotation
      // column (right). The CSS var sizes the RIGHT column, so dragging the
      // divider left must WIDEN it → invert the delta. Persists to localStorage.
      const container = el.closest(".detail");
      if (!container) return;
      const side = container.querySelector(".anno-col");
      const containerW = container.getBoundingClientRect().width;
      drag = {
        kind,
        el,
        container,
        axis: "x",
        startPx: side ? side.getBoundingClientRect().width : 400,
        containerSize: containerW,
        startClient: evt.clientX,
        cssVar: "--detail-side-w",
        invert: true,
        lo: 300,
        hi: Math.max(300, containerW * 0.6),
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
      // `invert` is for dividers whose CSS var sizes the pane on the OTHER side
      // of the drag direction (the clip-detail right column). `lo`/`hi` let a
      // divider supply its own clamp bounds; studio dividers omit them and fall
      // back to the player constants — behaviour unchanged.
      const eff = drag.invert ? -delta : delta;
      const lo = drag.lo ?? PLAYER_MIN_PX;
      const hi = drag.hi ?? Math.max(PLAYER_MIN_PX, drag.containerSize * PLAYER_MAX_FRAC);
      const px = clampSize(drag.startPx, eff, lo, hi);
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
    if (drag.kind === "detail-side") {
      // No studio store on the clip page — persist directly.
      try {
        if (drag.lastValue != null) {
          localStorage.setItem(
            "catdv:detailLayout",
            JSON.stringify({ sideW: drag.lastValue }),
          );
        }
      } catch (e) {
        /* localStorage unavailable — the size still applies for this session */
      }
    } else {
      const s = store();
      if (s && drag.lastValue != null) {
        s[drag.storeField] = drag.lastValue;
        if (typeof s.saveResize === "function") s.saveResize();
      }
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
