"""Walkthrough scenario: the Usage tab + always-present spend pill (#30).

The topbar carries an always-present "spend this month" pill (`#usage-pill`) on
every page. The Admin console's "Usage" tab HTMX-swaps in `_admin_usage.html`,
which shows current-month spend vs the monthly budget (a soft cap — it colours
the indicator and warns on launch, but never blocks a run), a by-model and
by-day breakdown, and a budget editor. This scenario proves the topbar pill is
present and the Usage tab + budget editor render.
"""

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

from playwright.sync_api import expect

SLUG = "admin-usage-budget"
TOPIC = "Admin console"
TITLE = "View spend and set a monthly budget (Usage tab)"
DESCRIPTION = (
    "An admin sees the always-present spend pill in the topbar, opens the "
    "Admin console, switches to the 'Usage' tab, and sees current-month spend "
    "vs the monthly budget (a soft cap) with by-model / by-day breakdowns and "
    "a budget editor."
)


def _origin(p) -> str:
    parts = urlsplit(p.url)
    return urlunsplit((parts.scheme, parts.netloc, "", "", ""))


def _open_admin(p) -> None:
    p.goto(f"{_origin(p)}/admin")
    expect(p.locator(".admin-page")).to_be_visible()


def _topbar_usage_pill_present(p) -> None:
    # The spend pill renders on every page (it lives in the topbar pillset).
    expect(p.locator("#usage-pill")).to_be_visible()


def _open_usage_tab(p) -> None:
    p.get_by_role("link", name="Usage", exact=True).click()
    # The tab HTMX-swaps the usage overview in.
    expect(p.locator(".admin-usage")).to_be_visible()


def _budget_editor_present(p) -> None:
    # The budget editor form posts to /admin/usage/budget.
    expect(p.locator("form[hx-post='/admin/usage/budget']")).to_be_visible()


def run(wt):
    wt.step(
        "The always-present spend pill is in the topbar",
        _topbar_usage_pill_present,
    )
    wt.step(
        "Open the Admin console",
        _open_admin,
    )
    wt.step(
        "Switch to the 'Usage' tab to see month spend vs budget",
        _open_usage_tab,
    )
    wt.step(
        "The monthly-budget editor is present (soft cap)",
        _budget_editor_present,
    )
