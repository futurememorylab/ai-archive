"""Walkthrough scenario: filter the list to clips awaiting review (DB-backed).

Unlike the text-search scenarios, the annotation-status filter resolves against
SQLite state (annotations + review_items + the clip universe in clip_list_cache),
not the archive — so it exercises the seeded database.
"""

from __future__ import annotations

from tests.walkthrough.fakes import NOT_ANNOTATED_CLIP_NAME, REVIEW_FIXTURE_CLIP_NAME
from tests.walkthrough.scenarios._search_support import (
    expect_clip_absent,
    expect_clip_visible,
    filter_anno,
)

SLUG = "search-filter-awaiting-review"
TOPIC = "Search page"
TITLE = "Filter to clips awaiting review"
DESCRIPTION = (
    "An operator filters the clip list by annotation status to see only clips "
    "with a pending AI draft awaiting human review. Clips with no annotations "
    "drop out of the list."
)


def run(wt):
    wt.step(
        "Confirm an un-annotated clip is present in the full list",
        lambda p: expect_clip_visible(p, NOT_ANNOTATED_CLIP_NAME),
    )
    wt.step(
        "Set the annotation-status filter to 'Awaiting review'",
        lambda p: filter_anno(p, "for_review"),
    )
    wt.step(
        "A clip with a pending draft is shown",
        lambda p: expect_clip_visible(p, REVIEW_FIXTURE_CLIP_NAME),
    )
    wt.step(
        "The un-annotated clip is filtered out",
        lambda p: expect_clip_absent(p, NOT_ANNOTATED_CLIP_NAME),
    )
