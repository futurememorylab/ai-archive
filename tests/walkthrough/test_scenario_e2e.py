"""End-to-end: run the MVP scenario in assert mode against the real app."""

from __future__ import annotations

import shutil

import pytest


def _chromium_available() -> bool:
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            path = pw.chromium.executable_path
        return bool(path)
    except Exception:
        return False


@pytest.mark.timeout(180)
def test_review_edit_scenario_passes_in_assert_mode():
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg required to seed the proxy video")
    if not _chromium_available():
        pytest.skip("chromium not installed (run: playwright install chromium)")

    from tests.walkthrough.run import run_scenarios

    results = run_scenarios(["review-edit-annotation"], record=False)
    assert len(results) == 1
    assert results[0]["ok"], results[0]["error"]
