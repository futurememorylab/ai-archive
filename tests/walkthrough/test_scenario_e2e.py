"""End-to-end: run the MVP scenario in assert mode against the real app."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest


def _chromium_available() -> bool:
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            path = pw.chromium.executable_path
    except Exception:
        return False
    # executable_path is where Playwright EXPECTS the browser, returned even
    # when it was never installed (or a version bump moved it) — check it.
    return bool(path) and Path(path).is_file()


@pytest.mark.timeout(180)
def test_review_edit_scenario_passes_in_assert_mode():
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg required to seed the proxy video")
    if not _chromium_available():
        pytest.skip("chromium not installed (run: playwright install chromium)")

    from tests.walkthrough.app_server import free_port
    from tests.walkthrough.run import run_scenarios

    results = run_scenarios(["review-edit-annotation"], record=False, port=free_port())
    assert len(results) == 1
    assert results[0]["ok"], results[0]["error"]


@pytest.mark.timeout(180)
def test_draft_marker_seeks_right_after_annotating():
    """A fresh draft marker seeks without a reload even when an earlier play
    failed because the proxy wasn't cached yet (player.js _recoverMedia)."""
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg required to seed the proxy video")
    if not _chromium_available():
        pytest.skip("chromium not installed (run: playwright install chromium)")

    from tests.walkthrough.app_server import free_port
    from tests.walkthrough.run import run_scenarios

    results = run_scenarios(["clip-annotate-seek-draft"], record=False, port=free_port())
    assert results[0]["ok"], results[0]["error"]
