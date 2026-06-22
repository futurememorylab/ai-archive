"""Walkthrough scenario: edit per-model Gemini rates in the Admin Models tab.

The Admin console (`/admin`) opens on Access & Permissions; the Models tab
HTMX-swaps in `_admin_models_table.html`, a row per seeded rate card (PR1:
pricing moved to the `model_config` table, materialised at boot by
`PricingService.reconcile_seeds`). This scenario proves an admin can reach the
tab and that a known model row + its Save control render.
"""

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

from playwright.sync_api import expect

SLUG = "admin-models-rates"
TOPIC = "Admin console"
TITLE = "Edit per-model Gemini rates"
DESCRIPTION = (
    "An admin opens the Admin console, switches to the Models tab, and sees the "
    "per-model Gemini rate cards — each with editable rate inputs and a Save "
    "control."
)

# A model that is always present in the seed rate cards (PricingService seeds).
SEED_MODEL = "gemini-2.5-flash-lite"


def _origin(p) -> str:
    parts = urlsplit(p.url)
    return urlunsplit((parts.scheme, parts.netloc, "", "", ""))


def _open_admin(p) -> None:
    p.goto(f"{_origin(p)}/admin")
    expect(p.locator(".admin-page")).to_be_visible()


def _open_models_tab(p) -> None:
    p.get_by_role("link", name="Models").click()
    # The Models tab HTMX-swaps the rate-card table into #admin-enum-region.
    expect(p.locator(".admin-models")).to_be_visible()


def _expect_model_row(p, model: str) -> None:
    expect(p.locator("td.mono-cell").filter(has_text=model)).to_have_count(1)


def _expect_save_control(p) -> None:
    expect(p.get_by_role("button", name="Save").first).to_be_visible()


def run(wt):
    wt.step(
        "Open the Admin console",
        _open_admin,
    )
    wt.step(
        "Switch to the Models tab",
        _open_models_tab,
    )
    wt.step(
        f"The '{SEED_MODEL}' rate-card row is shown",
        lambda p: _expect_model_row(p, SEED_MODEL),
    )
    wt.step(
        "Each rate card has a Save control",
        _expect_save_control,
    )
