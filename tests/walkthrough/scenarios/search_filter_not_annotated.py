"""Walkthrough scenario: filter the list to clips with no annotations (DB-backed).

The "Not annotated" filter is resolved as the clip universe (seeded into
clip_list_cache) minus the clips that have annotations in SQLite — so it only
works because the database is seeded with both the catalog and the drafts.
"""

from __future__ import annotations

from tests.walkthrough.fakes import NOT_ANNOTATED_CLIP_NAME, REVIEW_FIXTURE_CLIP_NAME
from tests.walkthrough.scenarios._search_support import (
    expect_clip_absent,
    expect_clip_visible,
    filter_anno,
)

SLUG = "search-filter-not-annotated"
TITLE = "Filter to clips with no annotations"
DESCRIPTION = (
    "An operator filters the clip list by annotation status to find clips that "
    "have no AI annotations yet. Clips that already have a draft drop out."
)


def run(wt):
    wt.step(
        "Set the annotation-status filter to 'Not annotated'",
        lambda p: filter_anno(p, "none"),
    )
    wt.step(
        "A clip with no annotations is shown",
        lambda p: expect_clip_visible(p, NOT_ANNOTATED_CLIP_NAME),
    )
    wt.step(
        "The clip awaiting review is filtered out",
        lambda p: expect_clip_absent(p, REVIEW_FIXTURE_CLIP_NAME),
    )
