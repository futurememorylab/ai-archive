"""Unit tests for the Walkthrough harness (no browser needed)."""

from __future__ import annotations

from unittest.mock import MagicMock

from tests.walkthrough.harness import Walkthrough, step_overlay_html


def test_overlay_html_contains_step_number_and_label():
    html = step_overlay_html(3, "Correct the Decade field")
    assert "Step 3" in html
    assert "Correct the Decade field" in html


def test_overlay_html_escapes_label():
    html = step_overlay_html(1, "<script>alert(1)</script>")
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_assert_mode_does_no_recording():
    page = MagicMock()
    page.screencast = MagicMock()
    wt = Walkthrough(page, record=False)
    wt.start("Title", "Desc")
    ran = []
    wt.step("do a thing", lambda p: ran.append(True))
    wt.finish()
    assert ran == [True]
    page.screencast.start.assert_not_called()
    assert wt.step_count == 1


def test_record_mode_starts_screencast_and_advances_steps():
    page = MagicMock()
    wt = Walkthrough(page, record=True, video_path="/tmp/x.webm")
    wt.start("Title", "Desc")
    wt.step("one", lambda p: None)
    wt.step("two", lambda p: None)
    wt.finish()
    page.screencast.start.assert_called_once()
    page.screencast.show_chapter.assert_called_once()
    page.screencast.stop.assert_called_once()
    assert wt.step_count == 2
