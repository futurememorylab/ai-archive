// Lightweight click/action feedback. No external dependency.
// Drives the #app-progress top bar from (a) htmx requests and
// (b) full-page navigation clicks (clip rows, rail links).
(function () {
  "use strict";

  function bar() {
    return document.getElementById("app-progress");
  }

  var active = 0;

  function start() {
    active++;
    document.documentElement.classList.add("app-busy");
    var b = bar();
    if (!b) return;
    b.classList.remove("done");
    void b.offsetWidth; // reflow so re-adding .active restarts the transition
    b.classList.add("active");
  }

  function done() {
    active = Math.max(0, active - 1);
    if (active > 0) return;
    document.documentElement.classList.remove("app-busy");
    var b = bar();
    if (!b) return;
    b.classList.remove("active");
    b.classList.add("done");
  }

  function skipProgressBar(detail) {
    var elt = detail && detail.elt;
    if (!elt || !elt.getAttribute) return false;
    // Background pollers (hx-trigger="every ...") run constantly; they must
    // not drive the global bar.
    var trg = elt.getAttribute("hx-trigger");
    if (trg && trg.indexOf("every") !== -1) return true;
    // The connection chip swaps its own innerHTML, which removes the
    // triggering Connect/Disconnect/Retry button. htmx then fires
    // htmx:afterRequest on that now-detached node, so it never bubbles to
    // this body listener → the paired done() is missed and the bar leaks
    // (stuck "loading"). The chip shows its own state via the swap, so opt
    // it out of the global bar entirely (start AND done both skipped, so the
    // counter stays balanced).
    if (elt.closest && elt.closest("#connection-chip")) return true;
    return false;
  }

  // htmx requests (tabs, version pickers, bulk refresh). htmx already adds the
  // built-in `.htmx-request` class to the requesting element for the dim/cursor
  // styles — we only manage the progress bar here.
  document.body.addEventListener("htmx:beforeRequest", function (e) {
    if (skipProgressBar(e.detail)) return;
    start();
  });
  document.body.addEventListener("htmx:afterRequest", function (e) {
    if (skipProgressBar(e.detail)) return;
    done();
  });

  // Full-page navigations: same-origin links and rows wired with
  // onclick="location.href=...". Capture phase so we react before navigation.
  function isPlainClick(ev) {
    return !ev.defaultPrevented && ev.button === 0 &&
      !ev.metaKey && !ev.ctrlKey && !ev.shiftKey && !ev.altKey;
  }
  document.addEventListener("click", function (ev) {
    if (!isPlainClick(ev)) return;
    // Row sub-cells (checkbox, cache badge) call event.stopPropagation() to
    // cancel the row's navigation. This listener runs in the capture phase —
    // before that bubble-phase stopPropagation — so without this guard a
    // checkbox click would start the progress bar for a navigation that never
    // happens, leaving the bar stuck and the row dimmed. Skip those clicks.
    if (ev.target.closest('[onclick*="stopPropagation"]')) return;
    var nav = ev.target.closest('a[href], [onclick*="location.href"]');
    if (!nav) return;
    // htmx-handled elements (hx-get/post/…) don't do a full-page navigation, so
    // the page never unloads — let the htmx:beforeRequest/afterRequest listeners
    // drive feedback. Triggering here too would start() without a matching done()
    // and leave the bar/cursor stuck on.
    if (nav.closest("[hx-get],[hx-post],[hx-put],[hx-patch],[hx-delete],[hx-boost]")) return;
    if (nav.target === "_blank") return;
    var href = nav.getAttribute("href");
    if (href && (href.charAt(0) === "#")) return;
    nav.classList.add("is-navigating");
    start();
  }, true);

  // Back/forward (bfcache) restore: clear any stuck bar.
  window.addEventListener("pageshow", function () {
    var b = bar();
    if (b) { b.classList.remove("active"); b.classList.remove("done"); }
    active = 0;
    document.documentElement.classList.remove("app-busy");
    var stuck = document.querySelectorAll(".is-navigating");
    for (var i = 0; i < stuck.length; i++) stuck[i].classList.remove("is-navigating");
  });
})();
