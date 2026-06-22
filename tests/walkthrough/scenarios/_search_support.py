"""Shared actions + assertions for the search scenarios.

Underscore prefix → the scenario loader skips this module (it is not itself a
scenario). The search page is the clip list at `/`: a server-rendered form whose
`q` input filters the result table via an HTMX partial swap of `#clips-region`.
"""

from __future__ import annotations

from playwright.sync_api import expect

SEARCH_INPUT = 'input[name="q"]'


def search_for(p, term: str) -> None:
    """Type a term into the search box and submit; results swap in place."""
    p.locator(SEARCH_INPUT).fill(term)
    p.get_by_role("button", name="Search").click()


def clear_search(p) -> None:
    """Empty the search box and submit, restoring the unfiltered list."""
    p.locator(SEARCH_INPUT).fill("")
    p.get_by_role("button", name="Search").click()


def filter_anno(p, value: str) -> None:
    """Set the annotation-status dropdown (any|for_review|applied|none|has_any).

    The select auto-submits on change, swapping the result table via HTMX.
    """
    p.locator('select[name="anno"]').select_option(value)


def expect_clip_visible(p, name: str) -> None:
    expect(p.locator("tr.vrow").filter(has_text=name)).to_have_count(1)


def expect_clip_absent(p, name: str) -> None:
    expect(p.locator("tr.vrow").filter(has_text=name)).to_have_count(0)


def expect_empty_state(p) -> None:
    expect(p.locator("td.empty")).to_be_visible()
