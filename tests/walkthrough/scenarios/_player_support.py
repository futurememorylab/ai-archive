"""Shared helpers for the player-transport walkthrough scenarios.

Every player scenario opens the canonical clip (archive_30s, clip 101) and then
asserts against the transport's timecode readout (`[data-test="player-tc"]`,
HH:MM:SS:FF). The clip is seeded with four markers on whole-second frame
boundaries (see fakes.build_clip) — 0s, 2s, 4s, 6s — so prev/next-marker
navigation lands on exact, assertable timecodes.
"""

from __future__ import annotations

from playwright.sync_api import Page, expect

from tests.walkthrough.fakes import CLIP_NAME

TC = '[data-test="player-tc"]'
PLAY = '[data-test="player-play"]'
PREV = '[data-test="player-prev"]'
NEXT = '[data-test="player-next"]'
STEP_BACK = '[data-test="player-step-back"]'
STEP_FWD = '[data-test="player-step-fwd"]'
ZERO = "00:00:00:00"


def open_clip(p: Page) -> None:
    """Open the canonical clip from the list and wait for the transport."""
    p.get_by_text(CLIP_NAME).first.click()
    expect(p.locator(TC)).to_be_visible()
    expect(p.locator(TC)).to_have_text(ZERO)


def prime(p: Page) -> None:
    """Load the proxy and park the playhead at 0, stopped.

    The <video> uses preload="none", so a programmatic seek (step / marker jump)
    is a no-op until the media has actually loaded. A brief play does the load;
    pausing then pressing « snaps back to the 0s marker without autoplaying.
    """
    p.locator(PLAY).click()
    expect_moving_off(p, ZERO)  # playing → media is loaded
    p.locator(PLAY).click()  # pause
    p.locator(PREV).click()  # prev-marker → snaps to the 0s marker, stays paused
    expect_tc(p, ZERO)


def expect_tc(p: Page, value: str) -> None:
    """Playhead sits at `value` (auto-retries while a seek settles)."""
    expect(p.locator(TC)).to_have_text(value)


def expect_moving_off(p: Page, value: str) -> None:
    """Playhead has advanced past `value` — i.e. the player is playing."""
    expect(p.locator(TC)).not_to_have_text(value)


def assert_held(p: Page, settle_ms: int = 500) -> None:
    """Playhead is stopped — not advancing at playback rate.

    Samples three times. A pause/seek can emit one trailing timeupdate, so the
    first→second pair may differ by a frame; by the second window the readout
    has settled, so a stopped player gives second == third. A playing one keeps
    changing across every window.
    """
    p.locator(TC).inner_text()
    p.wait_for_timeout(settle_ms)
    second = p.locator(TC).inner_text()
    p.wait_for_timeout(settle_ms)
    third = p.locator(TC).inner_text()
    assert second == third, f"expected a stopped playhead, but it kept moving {second} -> {third}"
