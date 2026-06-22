"""Unit test for the static gallery HTML."""

from __future__ import annotations

from tests.walkthrough.gallery import render_gallery


def test_gallery_lists_each_scenario_with_video():
    html = render_gallery(
        [
            {"slug": "a", "title": "Flow A", "description": "Does A", "video": "a.webm"},
            {"slug": "b", "title": "Flow B", "description": "Does B", "video": "b.webm"},
        ]
    )
    assert "Flow A" in html and "Flow B" in html
    assert 'src="a.webm"' in html and 'src="b.webm"' in html
    assert "<video" in html


def test_gallery_escapes_title():
    html = render_gallery(
        [{"slug": "x", "title": "<b>x</b>", "description": "d", "video": "x.webm"}]
    )
    assert "<b>x</b>" not in html
    assert "&lt;b&gt;x&lt;/b&gt;" in html
