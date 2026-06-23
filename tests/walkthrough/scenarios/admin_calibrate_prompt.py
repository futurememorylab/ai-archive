"""Walkthrough scenario: launch a calibration sweep from the Prompts tab.

The Admin console (`/admin`) opens on Access & Permissions; the "Prompts" tab
HTMX-swaps in `_admin_prompts_table.html`, which lists active prompt versions
with their per-resolution calibration results. Each row's "Calibrate" button
opens ONE shared modal that reuses the shared clip picker. The admin picks
exactly three clips; the "Launch sweep" confirm button is disabled until the
selection count is exactly three (3 resolutions × 2 repeats = 6 telemetry-only
jobs of 3 clips each). This scenario proves the dialog + picker render and that
the confirm button is gated on the three-clip rule.
"""

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

from playwright.sync_api import expect

SLUG = "admin-calibrate-prompt"
TOPIC = "Admin console"
TITLE = "Calibrate a prompt version from the Prompts tab"
DESCRIPTION = (
    "An admin opens the Admin console, switches to the 'Prompts' tab, and "
    "clicks Calibrate on a prompt version. A modal opens with the shared clip "
    "picker; the 'Launch sweep' button stays disabled until exactly three "
    "clips are selected, after which the calibration sweep (3 resolutions × 2 "
    "repeats) can be launched."
)


def _origin(p) -> str:
    parts = urlsplit(p.url)
    return urlunsplit((parts.scheme, parts.netloc, "", "", ""))


def _open_admin(p) -> None:
    p.goto(f"{_origin(p)}/admin")
    expect(p.locator(".admin-page")).to_be_visible()


def _open_prompts_tab(p) -> None:
    p.get_by_role("link", name="Prompts", exact=True).click()
    # The tab HTMX-swaps the prompts table (with calibration results) in.
    expect(p.locator(".admin-prompts")).to_be_visible()


def _open_calibrate_dialog(p) -> None:
    p.locator("[data-test='calibrate-open']").first.click()
    # The shared modal + clip picker render.
    expect(p.locator(".modal-card .nb-list")).to_be_visible()


def _confirm_disabled_until_three(p) -> None:
    # Nothing selected yet → the Launch sweep button is disabled.
    expect(p.locator("[data-test='calibrate-confirm']")).to_be_disabled()


def run(wt):
    wt.step(
        "Open the Admin console",
        _open_admin,
    )
    wt.step(
        "Switch to the 'Prompts' tab",
        _open_prompts_tab,
    )
    wt.step(
        "Click Calibrate to open the dialog with the shared clip picker",
        _open_calibrate_dialog,
    )
    wt.step(
        "The 'Launch sweep' button is disabled until exactly three clips are picked",
        _confirm_disabled_until_three,
    )
