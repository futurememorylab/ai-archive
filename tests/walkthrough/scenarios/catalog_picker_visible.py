"""Walkthrough scenario: the active catalog is visible and the picker lists all catalogs."""

from __future__ import annotations

from tests.walkthrough.fakes import CATALOG_ID, SECOND_CATALOG_ID
from tests.walkthrough.scenarios._catalog_support import (
    expect_active_catalog,
    expect_breadcrumb,
    expect_option,
    open_picker,
)

SLUG = "catalog-picker-visible"
TOPIC = "Catalogs"
TITLE = "See the active catalog and the catalogs you can switch to"
DESCRIPTION = (
    "The topbar shows which CatDV catalog the operator is working in. Opening "
    "the catalog menu lists every catalog the archive account can see, with the "
    "active one marked."
)


def run(wt):
    wt.step("The topbar shows the active catalog", lambda p: expect_active_catalog(p, "AI katalog"))
    wt.step(
        "The clip list breadcrumb names the same catalog",
        lambda p: expect_breadcrumb(p, f"AI katalog · #{CATALOG_ID}"),
    )
    wt.step("Open the catalog menu", open_picker)
    wt.step(
        "The active catalog is marked as current",
        lambda p: expect_option(p, CATALOG_ID, current=True),
    )
    wt.step(
        "The second catalog is offered as a choice",
        lambda p: expect_option(p, SECOND_CATALOG_ID, current=False),
    )
    wt.step("Press Esc to close the menu", lambda p: p.keyboard.press("Escape"))
