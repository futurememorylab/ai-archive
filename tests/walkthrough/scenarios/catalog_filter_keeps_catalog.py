"""Walkthrough scenario: list filters stay inside the chosen catalog.

The cache / annotation-status / batch filters resolve against SQLite tables
that have no catalog column. They must still only show clips of the catalog
picked in the topbar, and never change that choice.
"""

from __future__ import annotations

from tests.walkthrough.fakes import (
    REVIEW_FIXTURE_CLIP_NAME,
    SECOND_CATALOG_CLIP_NAMES,
    SECOND_CATALOG_ID,
)
from tests.walkthrough.scenarios._catalog_support import (
    expect_active_catalog,
    expect_breadcrumb,
    switch_to,
)
from tests.walkthrough.scenarios._search_support import (
    expect_clip_absent,
    expect_clip_visible,
    expect_empty_state,
    filter_anno,
)

SLUG = "catalog-filter-keeps-catalog"
TOPIC = "Catalogs"
TITLE = "Filters stay inside the chosen catalog"
DESCRIPTION = (
    "An operator works in 'vyber pro AI' and filters by annotation status. "
    "The list only ever shows that catalog's clips, and the catalog stays "
    "selected until the operator picks another one from the catalog menu."
)


def run(wt):
    wt.step(
        "Choose 'vyber pro AI' from the catalog menu", lambda p: switch_to(p, SECOND_CATALOG_ID)
    )
    wt.step(
        "Set the annotation-status filter to 'To review'",
        lambda p: filter_anno(p, "for_review"),
    )
    wt.step(
        "The other catalog's clip awaiting review is not shown",
        lambda p: expect_clip_absent(p, REVIEW_FIXTURE_CLIP_NAME),
    )
    wt.step("Nothing in this catalog is waiting for review", expect_empty_state)
    wt.step(
        "The topbar still shows 'vyber pro AI'",
        lambda p: expect_active_catalog(p, "vyber pro AI"),
    )
    wt.step(
        "Set the annotation-status filter to 'Not annotated'",
        lambda p: filter_anno(p, "none"),
    )
    wt.step(
        "This catalog's clips are listed",
        lambda p: expect_clip_visible(p, SECOND_CATALOG_CLIP_NAMES[0]),
    )
    wt.step(
        "The breadcrumb still names 'vyber pro AI'",
        lambda p: expect_breadcrumb(p, f"vyber pro AI · #{SECOND_CATALOG_ID}"),
    )
    wt.step("Reload the filtered page", lambda p: p.reload())
    wt.step(
        "The catalog is still 'vyber pro AI'",
        lambda p: expect_active_catalog(p, "vyber pro AI"),
    )
