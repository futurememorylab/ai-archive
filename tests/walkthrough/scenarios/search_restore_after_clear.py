"""Walkthrough scenario: clearing the search box restores the full list."""

from __future__ import annotations

from tests.walkthrough.fakes import SEARCH_NONMATCH_NAME, SEARCH_TERM
from tests.walkthrough.scenarios._search_support import (
    clear_search,
    expect_clip_absent,
    expect_clip_visible,
    search_for,
)

SLUG = "search-restore-after-clear"
TOPIC = "Search page"
TITLE = "Clear the search to restore the full list"
DESCRIPTION = (
    "After narrowing the list with a search, the operator clears the search box "
    "and the full, unfiltered clip list returns."
)


def run(wt):
    wt.step(
        f"Search for '{SEARCH_TERM}' to narrow the list",
        lambda p: search_for(p, SEARCH_TERM),
    )
    wt.step(
        "The non-matching clip is hidden while filtered",
        lambda p: expect_clip_absent(p, SEARCH_NONMATCH_NAME),
    )
    wt.step(
        "Clear the search box",
        lambda p: clear_search(p),
    )
    wt.step(
        "The non-matching clip is back in the restored list",
        lambda p: expect_clip_visible(p, SEARCH_NONMATCH_NAME),
    )
