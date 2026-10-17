"""Walkthrough scenario: the decluttered clip header and top bar.

The top bar no longer shows the spend pill, the "N to review" pill or an idle
"✓ Synced" chip. The clip header drops its duplicate timecode readout and puts
its actions on the right: Annotate, plus a ⋯ menu holding the cache control
(Cache / Delete) and Live.
"""

from __future__ import annotations

from playwright.sync_api import expect

from tests.walkthrough.fakes import CLIP_NAME

SLUG = "clip-header-actions"
TOPIC = "Clip page"
TITLE = "Clip actions on the right: Annotate and the ⋯ menu"
DESCRIPTION = (
    "An operator opens a clip: the top bar carries no spend, review or synced "
    "pills, and the header shows Annotate plus a ⋯ menu on the right. The menu "
    "holds the cache control and Live."
)

MORE = '[data-test="clip-more"]'


def _open_clip(p) -> None:
    p.get_by_text(CLIP_NAME).first.click()
    expect(p.locator(MORE)).to_be_visible()


def _quiet_topbar(p) -> None:
    bar = p.locator("header.topbar")
    expect(bar.locator("#usage-pill")).to_have_count(0)
    expect(bar.locator("#review-pill")).to_have_count(0)
    expect(bar.get_by_text("Synced")).to_have_count(0)


def _no_header_timecode(p) -> None:
    expect(p.locator(".detail-hdr .tc-readout")).to_have_count(0)


def _annotate_on_the_right(p) -> None:
    expect(p.locator(".detail-hdr").get_by_role("button", name="Annotate")).to_be_visible()


def _open_more(p) -> None:
    p.locator(MORE).get_by_role("button", name="More actions").click()
    panel = p.locator(f"{MORE} .clip-more-panel")
    expect(panel).to_be_visible()
    expect(panel.locator(".cache-ctl .menu-header")).to_have_text("Video file")
    expect(panel.locator('[data-test="cache-status"]')).to_be_visible()
    # Every action is a menu row, not a page button.
    expect(panel.locator(".btn")).to_have_count(0)
    expect(panel.get_by_role("button", name="Live")).to_be_visible()


def _close_more(p) -> None:
    p.keyboard.press("Escape")
    expect(p.locator(f"{MORE} .clip-more-panel")).to_be_hidden()


def run(wt):
    wt.step("Open the clip from the list", _open_clip)
    wt.step("The top bar has no spend, review or synced pills", _quiet_topbar)
    wt.step("The header has no duplicate timecode readout", _no_header_timecode)
    wt.step("Annotate sits on the right of the header", _annotate_on_the_right)
    wt.step("The ⋯ menu lists a Video file section and Live as menu rows", _open_more)
    wt.step("Escape closes the menu", _close_more)
