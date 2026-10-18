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
//   detail-chat divider → on .detail (clip-detail page, top of the chat):
//     - vertical drag → --detail-chat-h (px) sizing the chat dialog under
//       the video [row-resize].
//
// One delegated `pointerdown` listener on `document` matches `.studio-resizer`,
// so dividers that are HTMX-injected or CSS-toggled need no re-wiring. We use
// plain pointer events + setPointerCapture; no Alpine init / htmx.process —
// htmxAlpine.js stays the single HTMX↔Alpine lifecycle owner.
//
// Studio sizes round-trip through Alpine.store('studio') (playerH / playerW /
// cmpCur + saveResize()) → localStorage['studio.layoutPrefs']. The clip-detail
// dividers have no studio store, so they persist straight to
// localStorage['catdv:detailLayout'] as {sideW, chatH} (restored by an inline
// script on the page).

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
  const CHAT_MIN_PX = 120;   // the chat input row plus a line or two
  const VIDEO_KEEP_PX = 200; // video + transport left above the chat
  const DETAIL_LAYOUT_KEY = "catdv:detailLayout";

  let drag = null; // { el, container, axis, startPx | startPct, cssVar, ... }

  function store() {
    return window.Alpine?.store?.("studio") ?? null;
  }

  // One descriptor builder per divider kind: (el, evt) → drag state, or null
  // when the divider isn't inside its container.
  function playerDrag(el, evt) {
    const container = el.closest(".studio-right");
    if (!container) return null;
    // `right` resizes the player column (width); `under` the player row
    // (height). Same descriptor, parameterized by axis/dimension.
    const horizontal = (store()?.layout || "under") === "right";
    const dim = horizontal ? "width" : "height";
    const slot = container.querySelector(".studio-player-slot");
    return {
      container,
      axis: horizontal ? "x" : "y",
      startPx: slot ? slot.getBoundingClientRect()[dim] : 320,
      containerSize: container.getBoundingClientRect()[dim],
      startClient: horizontal ? evt.clientX : evt.clientY,
      cssVar: horizontal ? "--studio-player-w" : "--studio-player-h",
      storeField: horizontal ? "playerW" : "playerH",
    };
  }

  function cmpDrag(el, evt) {
    const container = el.closest(".studio-compare-row");
    if (!container) return null;
    const cur = container.querySelector('.studio-prompt-card[data-side="cur"]');
    const rowW = container.getBoundingClientRect().width || 1;
    return {
      container,
      axis: "x",
      startPct: cur ? (cur.getBoundingClientRect().width / rowW) * 100 : 50,
      containerSize: rowW,
      startClient: evt.clientX,
      cssVar: "--studio-cmp-cur",
      storeField: "cmpCur",
      isPct: true,
    };
  }

  // Clip-detail page: divider between the player (left) and the annotation
  // column (right). The CSS var sizes the RIGHT column, so dragging the
  // divider left must WIDEN it → invert the delta.
  function detailSideDrag(el, evt) {
    const container = el.closest(".detail");
    if (!container) return null;
    const side = container.querySelector(".anno-col");
    const containerW = container.getBoundingClientRect().width;
    return {
      container,
      axis: "x",
      startPx: side ? side.getBoundingClientRect().width : 400,
      containerSize: containerW,
      startClient: evt.clientX,
      cssVar: "--detail-side-w",
      layoutField: "sideW",
      invert: true,
      lo: 300,
      hi: Math.max(300, containerW * 0.6),
    };
  }

  // Clip-detail page: divider between the video and the chat dialog under
  // it. The var sizes the dialog BELOW the divider, so dragging up widens
  // it → invert. Capped so the video and its transport keep some room.
  function detailChatDrag(el, evt) {
    const container = el.closest(".detail");
    const dialog = el.closest(".live-dialog");
    const wrap = el.closest(".player-wrap");
    if (!container || !dialog || !wrap) return null;
    const wrapH = wrap.getBoundingClientRect().height;
    return {
      container,
      axis: "y",
      startPx: dialog.getBoundingClientRect().height,
      containerSize: wrapH,
      startClient: evt.clientY,
      cssVar: "--detail-chat-h",
      layoutField: "chatH",
      invert: true,
      lo: CHAT_MIN_PX,
      hi: Math.max(CHAT_MIN_PX, wrapH - VIDEO_KEEP_PX),
    };
  }

  const KINDS = {
    player: playerDrag,
    cmp: cmpDrag,
    "detail-side": detailSideDrag,
    "detail-chat": detailChatDrag,
  };

  function onPointerDown(evt) {
    if (drag) return;
    const el = evt.target.closest(".studio-resizer");
    if (!el) return;
    const build = KINDS[el.dataset.studioResizer];
    const state = build ? build(el, evt) : null;
    if (!state) return;
    drag = { el, ...state };

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
    if (drag.layoutField) {
      // Clip-detail dividers have no studio store: persist straight to
      // localStorage, merged so each divider keeps the other's size.
      try {
        if (drag.lastValue != null) {
          const prefs = JSON.parse(localStorage.getItem(DETAIL_LAYOUT_KEY) || "{}");
          prefs[drag.layoutField] = drag.lastValue;
          localStorage.setItem(DETAIL_LAYOUT_KEY, JSON.stringify(prefs));
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
