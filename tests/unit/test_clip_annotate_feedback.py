"""Wiring guards for issue #58 — annotation feedback on the clip page.

Static-presence checks (same style as test_bulk_annotate_wiring.py): they pin
the contract between clipAnnotate (the annotate button) and cacheActions (the
cache badge) so a refactor can't silently drop the upload/cache feedback or the
running timer.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TPL = ROOT / "backend" / "app" / "templates" / "pages"
STATIC = ROOT / "backend" / "app" / "static"


def test_format_exposes_shared_elapsed_timer():
    # The annotation timer must reuse the shared helper, not hand-roll a 4th
    # copy of the 1Hz ticker (studio, liveSession already have their own).
    js = (STATIC / "format.js").read_text()
    assert "window.elapsedTimer" in js
    assert "fmtTimecode" in js  # the helper reuses the shared formatter


def test_clip_annotate_runs_a_timer_and_toasts():
    js = (STATIC / "clipAnnotate.js").read_text()
    # Reuses the shared timer + exposes its label to the button.
    assert "elapsedTimer" in js
    assert "runElapsed" in js
    # Toasts on start and end (issue #58 §3).
    assert "Alpine.store(\"toast\")" in js or "Alpine.store('toast')" in js


def test_clip_annotate_signals_cache_layer_on_upload():
    # When annotating an uncached clip the job uploads the proxy; clipAnnotate
    # must tell the cache badge so it shows "uploading" then flips to cached.
    js = (STATIC / "clipAnnotate.js").read_text()
    assert "clip-cache-uploading" in js
    assert "clip-cache-refresh" in js


def test_annotate_dropdown_shows_running_timer():
    html = (TPL / "_annotate_dropdown.html").read_text()
    assert "runElapsed" in html


def test_annotate_button_narrates_cache_then_annotate_phases():
    # The backend caches before it annotates; the button must narrate the
    # same sequence (Caching → Annotating), not a single flat "Running".
    html = (TPL / "_annotate_dropdown.html").read_text()
    assert "Caching" in html and "Annotating" in html
    assert "phase === 'caching'" in html
    js = (STATIC / "clipAnnotate.js").read_text()
    # The phase map + the single dispatcher that drives label/toasts in order.
    assert "CA_PHASE" in js
    assert "_applyStatus" in js
    # The start no longer claims "Annotating…" up front — that toast now lives
    # only in the prompting-phase handler.
    assert "_onAnnotating" in js


def test_cache_badge_settles_at_caching_handoff_not_run_end():
    # The cache badge stops spinning when caching finishes (the prompting
    # phase begins), NOT at the end of the whole annotation — otherwise it
    # spins through the entire Gemini call. The refresh is emitted by
    # _onCachingDone, which the prompting handoff invokes.
    js = (STATIC / "clipAnnotate.js").read_text()
    assert "this._onCachingDone()" in js, "handoff must settle the cache badge"
    # The settle handler is what emits the cache-refresh (badge → cached).
    body = js[js.index("_onCachingDone() {"):]
    method = body[: body.index("\n    },")]
    assert "clip-cache-refresh" in method


def test_cache_actions_listens_for_annotate_events():
    html = (TPL / "_cache_actions.html").read_text()
    assert "clip-cache-uploading.window" in html
    assert "clip-cache-refresh.window" in html
