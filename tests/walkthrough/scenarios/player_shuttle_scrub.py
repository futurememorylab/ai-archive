"""Walkthrough scenario: J/K/L shuttle speeds and dragging the playhead.

Tester feedback (2026-11-04): the shortcut strip promised a "shuttle" but L
only played at normal speed and J stepped one frame. Now each press of L or J
doubles the speed in that direction (shown in the SHUTTLE tag), K stops, and
the playhead can be dragged along the timeline.

The assertions read the SHUTTLE tag and the timecode readout, both driven by
the player's own state, so they hold even where headless Chromium can't
decode the proxy.
"""

from __future__ import annotations

from playwright.sync_api import Page, expect

from tests.walkthrough.scenarios._player_support import TC, ZERO, open_clip

SLUG = "player-shuttle-scrub"
TOPIC = "Player"
TITLE = "Shuttle with J/K/L and drag the playhead"
DESCRIPTION = (
    "An operator drags the playhead to the middle of the timeline, presses "
    "L twice to play at double speed, J to go backwards, and K to stop."
)

SHUTTLE = '[data-test="player-shuttle"]'


def _expect_shuttle(p: Page, label: str) -> None:
    expect(p.locator(SHUTTLE)).to_have_text(label)


def _drag_playhead(p: Page) -> None:
    box = p.locator(".timeline").bounding_box()
    assert box, "timeline not rendered"
    y = box["y"] + box["height"] / 2
    p.mouse.move(box["x"] + 4, y)
    p.mouse.down()
    p.mouse.move(box["x"] + box["width"] * 0.3, y, steps=5)
    p.mouse.move(box["x"] + box["width"] * 0.5, y, steps=5)
    p.mouse.up()


def run(wt):
    wt.step("Open the clip from the list", open_clip)
    wt.step(
        "The shortcut strip explains the shuttle",
        lambda p: expect(p.locator(".kbdbar")).to_contain_text("press again: faster"),
    )
    wt.step(
        "Drag the playhead to the middle of the timeline",
        lambda p: (_drag_playhead(p), expect(p.locator(TC)).not_to_have_text(ZERO)),
    )
    wt.step(
        "L plays forward at normal speed",
        lambda p: (p.keyboard.press("l"), _expect_shuttle(p, "1×")),
    )
    wt.step(
        "L again doubles the speed",
        lambda p: (p.keyboard.press("l"), _expect_shuttle(p, "2×")),
    )
    wt.step(
        "J switches to backwards at normal speed",
        lambda p: (p.keyboard.press("j"), _expect_shuttle(p, "−1×")),
    )
    wt.step(
        "J again goes backwards twice as fast",
        lambda p: (p.keyboard.press("j"), _expect_shuttle(p, "−2×")),
    )
    wt.step(
        "K stops and resets the speed",
        lambda p: (p.keyboard.press("k"), _expect_shuttle(p, "1×")),
    )
