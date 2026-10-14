"""Walkthrough scenario: switch the active catalog and see the clip list follow."""

from __future__ import annotations

from tests.walkthrough.fakes import (
    CATALOG_ID,
    CLIP_NAME,
    SECOND_CATALOG_CLIP_NAMES,
    SECOND_CATALOG_ID,
)
from tests.walkthrough.scenarios._catalog_support import (
    expect_active_catalog,
    expect_breadcrumb,
    switch_to,
)
from tests.walkthrough.scenarios._search_support import expect_clip_absent, expect_clip_visible

SLUG = "catalog-switch"
TOPIC = "Catalogs"
TITLE = "Switch to another catalog"
DESCRIPTION = (
    "An operator switches from 'AI katalog' to 'vyber pro AI'. The clip list "
    "shows only the new catalog's clips, the choice survives a reload, and "
    "switching back restores the original list."
)


def run(wt):
    wt.step("Start in the default catalog", lambda p: expect_clip_visible(p, CLIP_NAME))
    wt.step(
        "Choose 'vyber pro AI' from the catalog menu", lambda p: switch_to(p, SECOND_CATALOG_ID)
    )
    wt.step("The topbar shows the new catalog", lambda p: expect_active_catalog(p, "vyber pro AI"))
    wt.step(
        "The breadcrumb names the new catalog",
        lambda p: expect_breadcrumb(p, f"vyber pro AI · #{SECOND_CATALOG_ID}"),
    )
    wt.step(
        "The list shows the new catalog's clips",
        lambda p: expect_clip_visible(p, SECOND_CATALOG_CLIP_NAMES[0]),
    )
    wt.step("Clips of the other catalog are gone", lambda p: expect_clip_absent(p, CLIP_NAME))
    wt.step("Reload the page", lambda p: p.reload())
    wt.step(
        "The choice is remembered by this browser",
        lambda p: expect_active_catalog(p, "vyber pro AI"),
    )
    wt.step("Switch back to 'AI katalog'", lambda p: switch_to(p, CATALOG_ID))
    wt.step("The original clips are back", lambda p: expect_clip_visible(p, CLIP_NAME))
