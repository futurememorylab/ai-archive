"""Walkthrough scenario: "All clips" in the topbar on every screen."""

from __future__ import annotations

from urllib.parse import urljoin

from playwright.sync_api import expect

from tests.walkthrough.fakes import CATALOG_ID, CLIP_ID, CLIP_NAME
from tests.walkthrough.scenarios._catalog_support import expect_breadcrumb
from tests.walkthrough.scenarios._search_support import expect_clip_visible

SLUG = "topbar-all-clips"
TOPIC = "Catalogs"
TITLE = "Get back to the clip list from any screen"
DESCRIPTION = (
    "Every screen's topbar starts with 'All clips'. One click from a clip, "
    "Batches, Prompts or Studio lands on the clip list of the active catalog."
)

ALL_CLIPS = '[data-test="crumb-all-clips"]'


def _back_to_list(p) -> None:
    with p.expect_navigation():
        p.locator(ALL_CLIPS).click()
    expect_breadcrumb(p, f"AI katalog · #{CATALOG_ID}")
    expect_clip_visible(p, CLIP_NAME)


def _visit(path: str):
    def go(p) -> None:
        p.goto(urljoin(p.url, path))
        expect(p.locator(ALL_CLIPS)).to_be_visible()

    return go


def run(wt):
    wt.step(
        "The clip list shows 'All clips' in the topbar",
        lambda p: expect(p.locator(ALL_CLIPS)).to_be_visible(),
    )
    for label, path in (
        ("Open a clip", f"/clips/{CLIP_ID}"),
        ("Open Batches", "/batches"),
        ("Open Prompts", "/prompts"),
        ("Open Studio", "/studio"),
    ):
        wt.step(f"{label}: 'All clips' is in the topbar", _visit(path))
        wt.step("Click 'All clips' to return to the clip list", _back_to_list)
