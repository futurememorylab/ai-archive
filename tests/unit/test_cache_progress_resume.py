"""Issue #78 testing findings — two cache-progress regressions:

1. The active queue panel polls every 2s with hx-swap. htmx adds the global
   ``.htmx-request`` class (``opacity: 0.6``) to the polling element on every
   tick, so the whole table blinks. The poll panel must be exempted.

2. The clip-page cache control only spins/polls after the user clicks Cache.
   If the page is reloaded (or navigated away and back) while a prefetch is
   still in flight, the in-progress state is lost. cacheActions must detect a
   running prefetch for its clip on init and resume the spinner + poll.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "backend" / "app" / "static"


def test_polling_queue_panel_is_exempt_from_request_dim():
    css = (STATIC / "app.css").read_text()
    # The 2s background poll must not inherit the .htmx-request opacity dim,
    # otherwise the table blinks on every tick.
    assert "#prefetch-panel.htmx-request" in css
    seg = css[css.index("#prefetch-panel.htmx-request"):]
    block = seg[: seg.index("}")]
    assert "opacity: 1" in block


def test_cache_actions_resumes_in_flight_prefetch_on_init():
    js = (STATIC / "cacheActions.js").read_text()
    # Alpine calls init() on mount; it must look at the live queue and resume.
    assert "init()" in js
    assert "/api/cache/prefetch/queue" in js
    # Only resume for an actually-running row.
    assert "downloading" in js and "queued" in js
    # Resuming reuses the existing poll loop rather than a parallel copy.
    assert "_pollUntilDone" in js
