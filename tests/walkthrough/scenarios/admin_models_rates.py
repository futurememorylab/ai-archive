"""Walkthrough scenario: the merged "Gemini models" Admin tab.

The Admin console (`/admin`) opens on Access & Permissions; the single
"Gemini models" tab HTMX-swaps in `_admin_models_table.html`. That tab unifies
two things that used to be separate tabs: the Gemini model *catalog* (the
editable enum) and per-model *pricing* (the `model_config` rate cards). The
catalog is the spine — every catalog model is listed, joined to its rate card
when one exists. A catalog model with no rate card is shown with a "no rate
card" pill (Save then creates the card). This scenario proves an admin can
reach the tab, sees a priced seed model with its Save control, and sees an
unpriced catalog model flagged as cardless — the visible payoff of the merge.
"""

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

from playwright.sync_api import expect

SLUG = "admin-models-rates"
TOPIC = "Admin console"
TITLE = "Manage Gemini models and rates in one tab"
DESCRIPTION = (
    "An admin opens the Admin console and switches to the unified 'Gemini "
    "models' tab, where the model catalog and per-model pricing live together: "
    "a priced seed model with editable rate inputs and a Save control, plus an "
    "unpriced catalog model flagged with a 'no rate card' pill."
)

# A model that is always present with a seed rate card (PricingService seeds).
SEED_MODEL = "gemini-2.5-flash-lite"
# A catalog model that is seeded WITHOUT a rate card — proves the merge: it
# still appears in the unified tab, flagged as cardless.
UNPRICED_MODEL = "gemini-3.5-flash"


def _origin(p) -> str:
    parts = urlsplit(p.url)
    return urlunsplit((parts.scheme, parts.netloc, "", "", ""))


def _open_admin(p) -> None:
    p.goto(f"{_origin(p)}/admin")
    expect(p.locator(".admin-page")).to_be_visible()


def _open_models_tab(p) -> None:
    p.get_by_role("link", name="Gemini models", exact=True).click()
    # The tab HTMX-swaps the merged catalog + rate-card table into the region.
    expect(p.locator(".admin-models")).to_be_visible()


def _expect_model_row(p, model: str) -> None:
    expect(p.locator("td.mono-cell").filter(has_text=model)).to_have_count(1)


def _expect_save_control(p) -> None:
    expect(p.get_by_role("button", name="Save").first).to_be_visible()


def _expect_unpriced_flagged(p) -> None:
    # The unpriced catalog model is listed AND flagged as having no rate card.
    expect(p.locator("td.mono-cell").filter(has_text=UNPRICED_MODEL)).to_have_count(1)
    expect(p.locator(".admin-models")).to_contain_text("no rate card")


def run(wt):
    wt.step(
        "Open the Admin console",
        _open_admin,
    )
    wt.step(
        "Switch to the 'Gemini models' tab",
        _open_models_tab,
    )
    wt.step(
        f"The priced seed model '{SEED_MODEL}' row is shown",
        lambda p: _expect_model_row(p, SEED_MODEL),
    )
    wt.step(
        "Each rate card has a Save control",
        _expect_save_control,
    )
    wt.step(
        f"The unpriced catalog model '{UNPRICED_MODEL}' shows a 'no rate card' pill",
        _expect_unpriced_flagged,
    )
