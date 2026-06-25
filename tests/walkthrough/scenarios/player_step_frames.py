"""Walkthrough scenario: frame-accurate stepping with the ‹ › transport buttons."""

from __future__ import annotations

from tests.walkthrough.scenarios._player_support import (
    STEP_BACK,
    STEP_FWD,
    expect_tc,
    open_clip,
    prime,
)

SLUG = "player-step-frames"
TOPIC = "Player"
TITLE = "Step the playhead frame by frame"
DESCRIPTION = (
    "An operator nudges the playhead one frame at a time with the step buttons "
    "to land on an exact frame — the timecode's frame field ticks 1, 2, then "
    "back to 1."
)


def run(wt):
    wt.step("Open the clip from the list", open_clip)
    wt.step("Load the proxy and park at the start", prime)
    wt.step(
        "Step forward one frame",
        lambda p: (p.locator(STEP_FWD).click(), expect_tc(p, "00:00:00:01")),
    )
    wt.step(
        "Step forward another frame",
        lambda p: (p.locator(STEP_FWD).click(), expect_tc(p, "00:00:00:02")),
    )
    wt.step(
        "Step back one frame",
        lambda p: (p.locator(STEP_BACK).click(), expect_tc(p, "00:00:00:01")),
    )
