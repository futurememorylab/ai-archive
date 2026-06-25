"""Walkthrough scenario: drive the transport entirely from the keyboard."""

from __future__ import annotations

from tests.walkthrough.scenarios._player_support import (
    ZERO,
    assert_held,
    expect_moving_off,
    expect_tc,
    open_clip,
)

SLUG = "player-keyboard-shortcuts"
TOPIC = "Player"
TITLE = "Drive the player from the keyboard"
DESCRIPTION = (
    "An operator works hands-on-keyboard: Space toggles play/pause, ↓/↑ step "
    "between markers, and End jumps to the tail — the same transport, no mouse."
)


def run(wt):
    wt.step("Open the clip from the list", open_clip)
    wt.step(
        "Space starts playback",
        lambda p: (p.keyboard.press("Space"), expect_moving_off(p, ZERO)),
    )
    wt.step(
        "Space pauses again",
        lambda p: (p.keyboard.press("Space"), assert_held(p)),
    )
    wt.step(
        "↓ jumps to the next marker (2s) without autoplaying",
        lambda p: (
            p.keyboard.press("ArrowDown"),
            expect_tc(p, "00:00:02:00"),
            assert_held(p),
        ),
    )
    wt.step(
        "↑ steps back to the opening marker (0s)",
        lambda p: (p.keyboard.press("ArrowUp"), expect_tc(p, ZERO)),
    )
    wt.step(
        "End jumps to the tail of the clip (8s)",
        lambda p: (p.keyboard.press("End"), expect_tc(p, "00:00:08:00")),
    )
