"""Walkthrough scenario: the merged "Gemini models" Admin tab.

The Admin console (`/admin`) opens on Access & Permissions; the single
"Gemini models" tab HTMX-swaps in `_admin_models_table.html`. That tab unifies
two things that used to be separate tabs: the Gemini model *catalog* (the
editable enum) and per-model *pricing* (the `model_config` rate cards). The
catalog is the spine — every catalog model is listed, joined to its rate card
when one exists. A catalog model with no rate card is shown with a "no rate
card" pill (Save then creates the card). This scenario proves an admin can
reach the tab, sees a priced seed model with its Save control, and sees a
newly-added model flagged as cardless — the visible payoff of the merge.

NOTE: all seed catalog models are now priced (Gemini 3.x/3.5 cards added in
PR8). The "no rate card" UX is demonstrated by adding a new model via the
admin form first — that is an authentic admin workflow and proves the pill
still fires on any cardless entry.
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
    "a priced seed model with editable rate inputs and a Save control, plus a "
    "newly-added model flagged with a 'no rate card' pill."
)

# A model that is always present with a seed rate card (PricingService seeds).
SEED_MODEL = "gemini-2.5-flash-lite"
# A synthetic model id added during the scenario — it has no rate card,
# so the 'no rate card' pill appears. This avoids relying on a real catalog
# model being unpriced (all seed models now have cards).
UNPRICED_MODEL = "gemini-walkthrough-unpriced"


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


def _add_unpriced_model(p) -> None:
    # Add a new catalog model that has no rate card yet, so the 'no rate card'
    # pill is visible in the table.
    p.locator("input[name='model']").fill(UNPRICED_MODEL)
    # The submit button is labelled "Add" — "Add model" is the field LABEL
    # (ui.field('Add model', 'model', ...)), not the button text, so
    # get_by_role("button", name="Add model") matches nothing. The merged tab
    # has more than one .admin-add-row form, so scope to the one that POSTs to
    # /admin/models (the add-model form) and click its submit.
    p.locator("form[hx-post='/admin/models'] button[type='submit']").click()
    expect(p.locator("td.mono-cell").filter(has_text=UNPRICED_MODEL)).to_have_count(1)


def _expect_model_row(p, model: str) -> None:
    expect(p.locator("td.mono-cell").filter(has_text=model)).to_have_count(1)


def _expect_save_control(p) -> None:
    expect(p.get_by_role("button", name="Save").first).to_be_visible()


def _expect_resolution_select(p) -> None:
    # A priced model row exposes its default media resolution as an editable
    # <select> (not a read-only cell); changing it POSTs the new resolution.
    expect(
        p.locator(".admin-models select[name='media_resolution']").first
    ).to_be_visible()


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
        "The default media resolution is an editable dropdown",
        _expect_resolution_select,
    )
    wt.step(
        f"Add a new catalog model '{UNPRICED_MODEL}' (no rate card yet)",
        _add_unpriced_model,
    )
    wt.step(
        f"The new model '{UNPRICED_MODEL}' shows a 'no rate card' pill",
        _expect_unpriced_flagged,
    )
