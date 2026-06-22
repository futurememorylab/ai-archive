"""Smoke test: the in-process walkthrough app boots, renders the clip, streams media."""

from __future__ import annotations

import shutil
import urllib.request

import pytest

from tests.walkthrough.app_server import WalkthroughApp


@pytest.fixture
def app_url(tmp_path):
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg required to seed the proxy video")
    app = WalkthroughApp(data_dir=tmp_path, port=8766)
    app.start()
    try:
        yield app.base_url
    finally:
        app.stop()


def _get(url: str):
    with urllib.request.urlopen(url, timeout=10) as r:  # noqa: S310
        return r.status, r.read()


def test_clips_list_renders(app_url):
    status, body = _get(f"{app_url}/")
    assert status == 200
    assert b"archive_30s" in body


def test_clip_detail_renders_with_draft(app_url):
    status, body = _get(f"{app_url}/clips/101")
    assert status == 200
    # The draft panel is Alpine-driven (no server-rendered data-draft-empty
    # hook — see tests/integration/test_clip_detail_draft.py, where the same
    # redesign is documented; data-draft-empty="false" never exists in the
    # app). The seeded draft proposes decade "20.léta" + marker "Establishing
    # shot"; both are serialised into the page x-data JSON via Python json with
    # ensure_ascii=True, so non-ASCII is \\u-escaped ("20.l\\u00e9ta"). These
    # markers prove the draft, seeded on the server's own event loop, rendered.
    assert b"review-bar" in body
    assert b"20.l\\u00e9ta" in body  # proposed decade value (JSON \\u-escaped)
    assert b"Establishing shot" in body  # proposed marker name


def test_media_streams(app_url):
    status, body = _get(f"{app_url}/api/media/101")
    assert status == 200
    assert len(body) > 1000
