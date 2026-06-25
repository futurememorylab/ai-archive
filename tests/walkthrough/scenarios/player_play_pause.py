"""Walkthrough scenario: play and pause the proxy from the transport bar."""

from __future__ import annotations

from tests.walkthrough.scenarios._player_support import (
    ZERO,
    assert_held,
    expect_moving_off,
    open_clip,
)

SLUG = "player-play-pause"
TOPIC = "Player"
TITLE = "Play and pause the proxy"
DESCRIPTION = (
    "An operator opens a clip and uses the transport's play button to start the "
    "proxy, watches the timecode advance, then pauses it — the playhead holds."
)


def run(wt):
    wt.step("Open the clip from the list", open_clip)
    wt.step(
        "Press play — the timecode starts advancing",
        lambda p: (
            p.locator('[data-test="player-play"]').click(),
            expect_moving_off(p, ZERO),
        ),
    )
    wt.step(
        "Press pause — the playhead holds its position",
        lambda p: (
            p.locator('[data-test="player-play"]').click(),
            assert_held(p),
        ),
    )
