from pathlib import Path

JS = Path("backend/app/static/studio.js").read_text()
NAV = Path("backend/app/templates/pages/_studio_nav.html").read_text()
STUB = Path("backend/app/templates/pages/_studio_uploaded_stub.html").read_text()


def test_switchsource_uploaded_loads_real_list():
    # The stub-injection branch is gone; uploaded now swaps in the fetched list.
    assert "Uploads coming soon" not in JS


def test_upload_captures_poster_and_posts_multipart():
    assert "capturePoster" in JS or "toBlob" in JS
    assert "FormData" in JS
    assert "/api/studio/uploads" in JS


def test_nav_uploaded_badge_uses_total():
    assert "uploaded_clip_total" in NAV


def test_stub_hosts_dropzone():
    assert "studio-dropzone" in STUB or "uploadClips" in STUB


def test_createset_is_source_aware():
    assert "navSource || 'archive'" in JS
    assert "/api/studio/sets?source=" in JS
