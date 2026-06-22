"""Walkthrough scenario: filter the clip list by searching a clip name."""

from __future__ import annotations

from tests.walkthrough.fakes import (
    SEARCH_MATCH_NAMES,
    SEARCH_NONMATCH_NAME,
    SEARCH_TERM,
)
from tests.walkthrough.scenarios._search_support import (
    expect_clip_absent,
    expect_clip_visible,
    search_for,
)

SLUG = "search-filter-by-name"
TOPIC = "Search page"
TITLE = "Search the clip list by name"
DESCRIPTION = (
    "An operator searches the clip list by typing part of a clip name. The list "
    "narrows to the matching clips and hides everything else."
)


def run(wt):
    wt.step(
        "Confirm a non-matching clip is present in the full list",
        lambda p: expect_clip_visible(p, SEARCH_NONMATCH_NAME),
    )
    wt.step(
        f"Search for '{SEARCH_TERM}'",
        lambda p: search_for(p, SEARCH_TERM),
    )
    wt.step(
        "The first matching clip is shown",
        lambda p: expect_clip_visible(p, SEARCH_MATCH_NAMES[0]),
    )
    wt.step(
        "The second matching clip is shown",
        lambda p: expect_clip_visible(p, SEARCH_MATCH_NAMES[1]),
    )
    wt.step(
        "The non-matching clip is filtered out",
        lambda p: expect_clip_absent(p, SEARCH_NONMATCH_NAME),
    )
