from pathlib import Path

JS = Path("backend/app/static/studio.js").read_text()
UPLOAD_JS = Path("backend/app/static/studioUpload.js").read_text()
NAV = Path("backend/app/templates/pages/_studio_nav.html").read_text()
SET = Path("backend/app/templates/pages/_studio_set.html").read_text()


def test_switchsource_uploaded_loads_real_list():
    # The stub-injection branch is gone; uploaded now swaps in the fetched list.
    assert "Uploads coming soon" not in JS


def test_upload_captures_poster_and_posts_multipart():
    assert "capturePoster" in JS or "toBlob" in JS
    assert "FormData" in UPLOAD_JS
    assert "/api/studio/uploads" in UPLOAD_JS


def test_nav_uploaded_badge_uses_total():
    assert "uploaded_clip_total" in NAV


def test_uploaded_set_hosts_per_set_dropzone():
    # The dropzone now lives inside an uploaded set's body, wired to that set.
    assert "studio-dropzone" in SET
    assert "uploadClips({{ set_id }})" in SET


def test_uploadclips_targets_a_set():
    assert "uploadClips', (setId)" in JS or "uploadClips', (setId " in JS
    assert "fd.append('set_id'" in UPLOAD_JS


def test_createset_is_source_aware():
    assert "navSource || 'archive'" in JS
    assert "/api/studio/sets?source=" in JS


def test_upload_goes_straight_to_the_bucket_when_the_app_offers_it():
    # Cloud Run refuses requests over 32 MB: the browser asks for a signed URL,
    # PUTs the file there, then completes (or aborts) with the app.
    assert "/api/studio/uploads/direct" in UPLOAD_JS
    assert "'PUT'" in UPLOAD_JS
    assert "/complete" in UPLOAD_JS
    assert "method: 'DELETE'" in UPLOAD_JS
    # Local runs answer {direct: false} and keep uploading through the app.
    assert "if (!start.direct) return viaApp(" in UPLOAD_JS
    assert "window.studioUpload.send(" in JS


def test_studio_upload_script_loads_before_studio_js():
    layout = Path("backend/app/templates/pages/layout.html").read_text()
    assert layout.index("/static/studioUpload.js") < layout.index("/static/studio.js")
