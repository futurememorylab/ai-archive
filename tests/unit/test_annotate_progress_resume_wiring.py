"""Spec 2026-06-23-annotate-cache-queue-consistency §3–§4: static wiring guards
for the annotate button's caching percentage + reload resume. Same
source-presence style as test_clip_annotate_feedback.py (no JS runtime)."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TPL = ROOT / "backend" / "app" / "templates" / "pages"
STATIC = ROOT / "backend" / "app" / "static"


def test_shared_cache_progress_helper_exists():
    js = (STATIC / "format.js").read_text()
    # One helper both surfaces read progress through, so the % math agrees.
    assert "window.cacheProgressForClip" in js
    assert "/api/cache/prefetch/queue" in js


def test_clip_annotate_shows_caching_percentage():
    js = (STATIC / "clipAnnotate.js").read_text()
    assert "cachePct" in js
    # Progress comes from the shared helper, polled only during caching.
    assert "cacheProgressForClip" in js
    assert "_startCachePoll" in js and "_stopCachePoll" in js
    # The button template renders the percentage next to "Caching".
    html = (TPL / "_annotate_dropdown.html").read_text()
    assert "cachePct" in html


def test_clip_annotate_resumes_running_job_on_reload():
    js = (STATIC / "clipAnnotate.js").read_text()
    # On mount it must ask the server for a running job for this clip and
    # reattach the stream.
    assert "/api/jobs/active-for-clip/" in js
    assert "_resumeRun" in js
    # Resume reuses the existing stream-attach path, not a parallel copy.
    assert "this.attachStream(jobId)" in js


def test_annotate_resume_preserves_elapsed_time():
    # The elapsed timer must resume from the true run start (job started_at),
    # not restart at 0:00 on every page reload.
    js = (STATIC / "clipAnnotate.js").read_text()
    assert "started_at" in js
    assert "Date.parse" in js
    # The shared timer accepts a backdated offset.
    fmt = (STATIC / "format.js").read_text()
    assert "offsetSeconds" in fmt


def test_cache_badge_defers_to_annotate_button_for_progress():
    # During an annotate, only the annotate button shows caching progress — the
    # cache badge must not also spin. On reload-resume cacheActions skips
    # annotate-driven queue rows, and there is no onAnnotateUpload spinner.
    js = (STATIC / "cacheActions.js").read_text()
    assert "requested_by !== 'annotate'" in js
    assert "onAnnotateUpload" not in js


def test_annotate_resume_uses_x_init_not_init():
    # clipAnnotate is Object.assign-merged with player()/reviewMixin() in the
    # clip page x-data, and Alpine honours a single init() — player owns it.
    # The resume hook must be a distinct method (_annotateInit) wired via
    # x-init, or it would silently clobber the player's init().
    js = (STATIC / "clipAnnotate.js").read_text()
    assert "_annotateInit()" in js
    assert "async init()" not in js and "\n    init()" not in js
    html = (TPL / "clip_detail.html").read_text()
    assert "_annotateInit()" in html
