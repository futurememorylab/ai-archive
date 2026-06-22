"""Walkthrough scenario: a search with no matches shows the empty state."""

from __future__ import annotations

from tests.walkthrough.fakes import NO_RESULTS_TERM
from tests.walkthrough.scenarios._search_support import (
    expect_empty_state,
    search_for,
)

SLUG = "search-no-results"
TOPIC = "Search page"
TITLE = "Search with no matches"
DESCRIPTION = (
    "An operator searches for a term no clip name contains. The list shows a "
    "clear empty-state message instead of stale results."
)


def run(wt):
    wt.step(
        f"Search for '{NO_RESULTS_TERM}', which matches nothing",
        lambda p: search_for(p, NO_RESULTS_TERM),
    )
    wt.step(
        "The list shows the empty-state message",
        lambda p: expect_empty_state(p),
    )
