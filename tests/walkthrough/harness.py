"""Walkthrough: wraps a Playwright Page with screencast annotation + step counter.

Recording uses the v1.59 screencast API (page.screencast.start / show_chapter /
show_actions / show_overlay). In assert mode (record=False) every screencast
call is skipped so headless runs do no recording work.
"""

from __future__ import annotations

import html
from typing import Callable


def step_overlay_html(n: int, label: str) -> str:
    """Pure builder for the on-screen step badge (also the doc narration)."""
    safe = html.escape(label)
    return (
        '<div style="position:fixed;top:16px;left:16px;z-index:2147483647;'
        "font:600 18px/1.3 system-ui,sans-serif;color:#fff;background:rgba(20,20,28,.86);"
        'padding:10px 16px;border-radius:10px;box-shadow:0 2px 12px rgba(0,0,0,.4)">'
        f'<span style="color:#7dd3fc">Step {n}</span> — {safe}'
        "</div>"
    )


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
        self.page.screencast.start(path=self.video_path)
        self.page.screencast.show_chapter(title=title, description=description)
        self.page.screencast.show_actions(position="bottom")

    def step(self, label: str, action: Callable) -> None:
        self.step_count += 1
        if self.record:
            self._overlay = self.page.screencast.show_overlay(
                html=step_overlay_html(self.step_count, label)
            )
        action(self.page)

    def finish(self) -> str | None:
        if not self.record:
            return None
        self.page.screencast.stop()
        return self.video_path
