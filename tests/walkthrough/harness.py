"""Walkthrough: wraps a Playwright Page with screencast annotation + step counter.

Recording uses the screencast API (page.screencast.start / show_chapter /
show_actions / show_overlay; playwright>=1.63 for start's `size`). In assert
mode (record=False) every screencast call is skipped so headless runs do no recording work.
"""

from __future__ import annotations

import html
from collections.abc import Callable


def step_overlay_html(n: int, label: str) -> str:
    """Pure builder for the on-screen step badge (also the doc narration)."""
    safe = html.escape(label)
    return (
        '<div style="position:fixed;bottom:64px;left:72px;z-index:2147483647;'
        "font:600 18px/1.3 system-ui,sans-serif;color:#fff;background:rgba(20,20,28,.86);"
        'padding:10px 16px;border-radius:10px;box-shadow:0 2px 12px rgba(0,0,0,.4)">'
        f'<span style="color:#7dd3fc">Step {n}</span> — {safe}'
        "</div>"
    )


# In record mode, dwell after each step's action so the resulting (post-HTMX-
# swap) state paints and is captured under that step's overlay, and dwell once
# more before stopping so the final state is recorded. These are recording-only
# (skipped in assert mode) — they don't gate any assertion.
_STEP_DWELL_MS = 900
_FINAL_DWELL_MS = 1200


class Walkthrough:
    def __init__(self, page, *, record: bool, video_path: str | None = None) -> None:
        self.page = page
        self.record = record
        self.video_path = video_path
        self.step_count = 0
        self._overlay = None

    def start(self, title: str, description: str) -> None:
        if not self.record:
            return
        self.page.screencast.start(path=self.video_path, size=self._native_size())
        self.page.screencast.show_chapter(title=title, description=description)
        self.page.screencast.show_actions(position="bottom")

    def _native_size(self) -> dict[str, int] | None:
        """The page's viewport size. Passing it to screencast.start defeats
        Playwright's default 800px cap on the longest edge (None keeps the
        default)."""
        viewport = self.page.viewport_size
        if not viewport:
            return None
        return {"width": viewport["width"], "height": viewport["height"]}

    def step(self, label: str, action: Callable) -> None:
        self.step_count += 1
        if self.record:
            self._overlay = self.page.screencast.show_overlay(
                html=step_overlay_html(self.step_count, label)
            )
        action(self.page)
        if self.record:
            # Let the action's result (e.g. an HTMX table swap) paint and be
            # recorded under this step's overlay before advancing.
            self.page.wait_for_timeout(_STEP_DWELL_MS)

    def finish(self) -> str | None:
        if not self.record:
            return None
        # Dwell on the final state so it's captured before the screencast stops.
        self.page.wait_for_timeout(_FINAL_DWELL_MS)
        self.page.screencast.stop()
        return self.video_path
