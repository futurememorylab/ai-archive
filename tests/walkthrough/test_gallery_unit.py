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


def test_gallery_groups_by_topic_with_nav():
    html = render_gallery(
        [
            {"slug": "find", "topic": "Search page", "title": "Find", "description": "d", "video": "find.webm"},
            {"slug": "empty", "topic": "Search page", "title": "Empty", "description": "d", "video": "empty.webm"},
            {"slug": "review", "topic": "Clip page", "title": "Review", "description": "d", "video": "review.webm"},
        ]
    )
    # One section per topic, anchored.
    assert 'id="topic-search-page"' in html
    assert 'id="topic-clip-page"' in html
    # Nav menu links to each topic and each individual video.
    assert 'href="#topic-search-page"' in html
    assert 'href="#topic-clip-page"' in html
    assert 'href="#video-find"' in html and 'href="#video-review"' in html
    # Each video card carries its own anchor id.
    assert 'id="video-empty"' in html
    assert "<nav" in html
