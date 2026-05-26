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
    var b = bar();
    if (!b) return;
    active++;
    b.classList.remove("done");
    void b.offsetWidth; // reflow so re-adding .active restarts the transition
    b.classList.add("active");
  }

  function done() {
    var b = bar();
    if (!b) return;
    active = Math.max(0, active - 1);
    if (active > 0) return;
    b.classList.remove("active");
    b.classList.add("done");
  }

  function isBackgroundPoller(detail) {
    var elt = detail && detail.elt;
    var trg = elt && elt.getAttribute ? elt.getAttribute("hx-trigger") : null;
    return !!trg && trg.indexOf("every") !== -1;
  }

  // htmx requests (tabs, version pickers, bulk refresh). htmx already adds the
  // built-in `.htmx-request` class to the requesting element for the dim/cursor
  // styles — we only manage the progress bar here.
  document.body.addEventListener("htmx:beforeRequest", function (e) {
    if (isBackgroundPoller(e.detail)) return;
    start();
  });
  document.body.addEventListener("htmx:afterRequest", function (e) {
    if (isBackgroundPoller(e.detail)) return;
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
    var nav = ev.target.closest('a[href], [onclick*="location.href"]');
    if (!nav) return;
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
    var stuck = document.querySelectorAll(".is-navigating");
    for (var i = 0; i < stuck.length; i++) stuck[i].classList.remove("is-navigating");
  });
})();
