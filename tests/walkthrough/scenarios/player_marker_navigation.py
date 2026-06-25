"""Walkthrough scenario: step through markers and back — the previous marker is
always reachable.

Regression cover for the prev-marker fix: after jumping forward across markers,
pressing « walks back through every one (6s → 4s → 2s → 0s) instead of sticking
on the nearest. The clip is stopped throughout, so each landing is an exact
timecode.
"""

from __future__ import annotations

from tests.walkthrough.scenarios._player_support import (
    NEXT,
    PREV,
    expect_tc,
    open_clip,
    prime,
)

SLUG = "player-marker-navigation"
TOPIC = "Player"
TITLE = "Jump between markers and back"
DESCRIPTION = (
    "An operator skips forward through the clip's markers with », then walks all "
    "the way back with « — every previous marker is reachable, none get skipped."
)


def run(wt):
    wt.step("Open the clip from the list", open_clip)
    wt.step("Load the proxy and park at the start", prime)
    wt.step(
        "Next marker → the establishing shot at 2s",
        lambda p: (p.locator(NEXT).click(), expect_tc(p, "00:00:02:00")),
    )
    wt.step(
        "Next marker → the interview scene at 4s",
        lambda p: (p.locator(NEXT).click(), expect_tc(p, "00:00:04:00")),
    )
    wt.step(
        "Next marker → the closing shot at 6s",
        lambda p: (p.locator(NEXT).click(), expect_tc(p, "00:00:06:00")),
    )
    wt.step(
        "Previous marker steps back to 4s (not stuck on 6s)",
        lambda p: (p.locator(PREV).click(), expect_tc(p, "00:00:04:00")),
    )
    wt.step(
        "Previous marker again → 2s",
        lambda p: (p.locator(PREV).click(), expect_tc(p, "00:00:02:00")),
    )
    wt.step(
        "Previous marker reaches the opening at 0s",
        lambda p: (p.locator(PREV).click(), expect_tc(p, "00:00:00:00")),
    )
