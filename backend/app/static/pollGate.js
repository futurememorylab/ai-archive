// Pause topbar background polling while the tab is hidden.
//
// The topbar pills (review / usage / sync / connection) refresh via HTMX
// `every Ns` timers. Those timers keep firing in a backgrounded tab — wasted
// HTTP requests and SQLite aggregates for a view nobody is looking at. HTMX
// has no built-in "pause when hidden", but every poll is a GET to a `/ui/*`
// endpoint, so we cancel those requests before they go out while the document
// is hidden, and repaint the pills once the tab is shown again.
//
// Only `/ui/*` polls are affected — user actions and full-page navigations are
// never `/ui/*`, so interactivity is untouched.
(function () {
  var POLL_IDS = ["review-pill", "usage-pill", "sync-chip", "connection-chip"];

  function pollPath(detail) {
    var p = (detail && detail.requestConfig && detail.requestConfig.path) || "";
    return typeof p === "string" && p.indexOf("/ui/") === 0;
  }

  // Cancel a background poll while hidden (htmx:beforeRequest is cancelable).
  document.body.addEventListener("htmx:beforeRequest", function (e) {
    if (document.hidden && pollPath(e.detail)) e.preventDefault();
  });

  // Tab came back — repaint the pills now instead of waiting out the interval
  // that was suppressed while hidden.
  document.addEventListener("visibilitychange", function () {
    if (document.hidden || !window.htmx) return;
    POLL_IDS.forEach(function (id) {
      var el = document.getElementById(id);
      if (el) window.htmx.trigger(el, "load");
    });
  });
})();
