"""Walkthrough scenario: a marker jump preserves play state.

Regression cover for the second half of the prev/next-marker fix: jumping while
stopped must NOT autoplay, and jumping while playing must KEEP playing.
"""

from __future__ import annotations

from tests.walkthrough.scenarios._player_support import (
    NEXT,
    PLAY,
    assert_held,
    expect_moving_off,
    expect_tc,
    open_clip,
    prime,
)

SLUG = "player-marker-playstate"
TOPIC = "Player"
TITLE = "Marker jumps keep the play state"
DESCRIPTION = (
    "Jumping to a marker while the clip is stopped lands there without "
    "autoplaying; jumping again while it's playing keeps it playing."
)


def run(wt):
    wt.step("Open the clip from the list", open_clip)
    wt.step("Load the proxy and park at the start", prime)
    wt.step(
        "Next marker while stopped → lands at 2s and stays paused (no autoplay)",
        lambda p: (
            p.locator(NEXT).click(),
            expect_tc(p, "00:00:02:00"),
            assert_held(p),
        ),
    )
    wt.step(
        "Now press play — the playhead advances past 2s",
        lambda p: (p.locator(PLAY).click(), expect_moving_off(p, "00:00:02:00")),
    )
    wt.step(
        "Next marker while playing → jumps to 4s and keeps playing",
        lambda p: (p.locator(NEXT).click(), expect_moving_off(p, "00:00:04:00")),
    )
