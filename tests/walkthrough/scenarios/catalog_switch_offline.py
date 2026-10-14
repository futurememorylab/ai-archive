"""Walkthrough scenario: the catalog picker keeps working while CatDV is offline."""

from __future__ import annotations

from tests.walkthrough.fakes import (
    CATALOG_ID,
    SECOND_CATALOG_CLIP_NAMES,
    SECOND_CATALOG_ID,
    monitor_fake,
)
from tests.walkthrough.scenarios._catalog_support import (
    expect_active_catalog,
    expect_option,
    open_picker,
    switch_to,
)
from tests.walkthrough.scenarios._search_support import expect_clip_visible

SLUG = "catalog-switch-offline"
TOPIC = "Catalogs"
TITLE = "Switch catalogs while CatDV is offline"
DESCRIPTION = (
    "CatDV drops offline (VPN down, seat taken). The catalog menu still lists "
    "every catalog from the local cache, and switching shows the cached clips "
    "of the chosen catalog."
)


def _go_offline(p):
    monitor_fake().set_online(False)
    p.reload()


def _go_online(p):
    monitor_fake().set_online(True)
    p.reload()


def run(wt):
    wt.step("CatDV goes offline", _go_offline)
    wt.step(
        "The page shows the cached-clips banner",
        lambda p: p.locator(".banner--offline").wait_for(state="visible"),
    )
    wt.step("Open the catalog menu", open_picker)
    wt.step(
        "Both catalogs are still listed from the local cache",
        lambda p: (expect_option(p, CATALOG_ID, current=True), expect_option(p, SECOND_CATALOG_ID, current=False)),
    )
    wt.step("Switch to 'vyber pro AI'", lambda p: (p.keyboard.press("Escape"), switch_to(p, SECOND_CATALOG_ID)))
    wt.step("The topbar shows the new catalog", lambda p: expect_active_catalog(p, "vyber pro AI"))
    wt.step(
        "Its cached clips are listed",
        lambda p: expect_clip_visible(p, SECOND_CATALOG_CLIP_NAMES[1]),
    )
    wt.step("CatDV comes back online", _go_online)
