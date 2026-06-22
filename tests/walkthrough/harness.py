"""Walkthrough: wraps a Playwright Page with screencast annotation + step counter.

Recording uses the v1.59 screencast API (page.screencast.start / show_chapter /
show_actions / show_overlay). In assert mode (record=False) every screencast
call is skipped so headless runs do no recording work.
"""

from __future__ import annotations

import html
from typing import Callable


def _patch_screencast_native_size() -> None:
    """Let page.screencast.start() record at the page's native viewport size.

    Playwright caps screencast video to 800px on the longest edge (driver
    Screencast._startScreencast: scale = min(1, 800 / max(w, h))) whenever the
    start call omits an explicit size — which the public Python wrapper always
    does. The wire protocol *does* accept an optional `size` {width,height}
    (PageScreencastStartParams), so patch the impl start() to forward a
    per-instance `_forced_size`. With no `_forced_size` set it delegates to the
    original, so this is a no-op for any other caller. Verified against
    playwright 1.60.
    """
    from playwright._impl import _screencast

    if getattr(_screencast.Screencast, "_native_size_patch", False):
        return

    from playwright._impl._connection import from_nullable_channel
    from playwright._impl._disposable import DisposableStub
    from playwright._impl._errors import Error

    _orig_start = _screencast.Screencast.start

    async def start(self, onFrame=None, path=None, quality=None):  # type: ignore[no-untyped-def]
        size = getattr(self, "_forced_size", None)
        if size is None:
            return await _orig_start(self, onFrame=onFrame, path=path, quality=quality)
        if self._started:
            raise Error("Screencast is already started")
        self._started = True
        self._on_frame = onFrame
        result = await self._page._channel.send_return_as_dict(
            "screencastStart",
            None,
            {
                "quality": quality,
                "sendFrames": bool(onFrame),
                "record": bool(path),
                "size": {"width": int(size[0]), "height": int(size[1])},
            },
        )
        artifact_channel = (result or {}).get("artifact")
        if artifact_channel:
            self._artifact = from_nullable_channel(artifact_channel)
            self._save_path = path
        return DisposableStub(lambda: self.stop(), self._page)

    _screencast.Screencast.start = start
    _screencast.Screencast._native_size_patch = True


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
        self._record_at_native_size()
        self.page.screencast.start(path=self.video_path)
        self.page.screencast.show_chapter(title=title, description=description)
        self.page.screencast.show_actions(position="bottom")

    def _record_at_native_size(self) -> None:
        """Record at the page's viewport resolution (defeats the 800px cap)."""
        viewport = self.page.viewport_size
        if not viewport:
            return
        _patch_screencast_native_size()
        impl = getattr(self.page.screencast, "_impl_obj", None)
        if impl is not None:
            impl._forced_size = (viewport["width"], viewport["height"])

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
