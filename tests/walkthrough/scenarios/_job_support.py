"""Shared actions + assertions for the job-start / cancel scenarios.

Underscore prefix → the scenario loader skips this module (it is not itself a
scenario). These drive the clips-list bulk "Annotate selected" flow
(`bulkAnnotate.js`) and the Batches-page cancel flow (`batchesPage.cancelBatch`).
"""

from __future__ import annotations

from playwright.sync_api import expect

from tests.walkthrough.fakes import PRODUCTION_PROMPT_NAME, FakeGemini

_ANNO_MODAL = '[role="dialog"][aria-label="Annotate selected"]'


def select_clip(p, name: str) -> None:
    """Tick the select checkbox on the clip row whose name matches `name`."""
    row = p.locator("tr.vrow").filter(has_text=name)
    row.locator("input.row-check").check()


def open_annotate_modal(p) -> None:
    """Open the Actions menu and choose 'Annotate selected →'."""
    p.get_by_role("button", name="Actions").click()
    p.get_by_role("button", name="Annotate selected").click()
    expect(p.locator(_ANNO_MODAL)).to_be_visible()


def assign_production_prompt(p) -> None:
    """Pick the seeded production prompt for the (single video) media group."""
    modal = p.locator(_ANNO_MODAL)
    select = modal.locator("select.ba-select").first
    expect(select).to_be_visible()
    select.select_option(label=PRODUCTION_PROMPT_NAME)


def run_annotate(p) -> None:
    """Click the modal's primary 'Annotate N clip(s)' button to kick the job."""
    modal = p.locator(_ANNO_MODAL)
    modal.get_by_role("button", name="Annotate").click()


def expect_toast(p, text: str) -> None:
    """Assert a toast carrying `text` is shown (auto-waits for it to appear)."""
    expect(p.locator("#toast-root .toast-msg").filter(has_text=text)).to_be_visible()


def open_batches(p) -> None:
    """Navigate to the Batches page via the left-rail link."""
    p.locator('a[title="Batches"]').click()
    expect(p.locator("table.batch-tbl")).to_be_visible()


def wait_until_prompting(gemini: FakeGemini) -> None:
    """Block until the held annotate() has started — i.e. the batch is actually
    'prompting'/'running' — so the caller asserts 'Running' without a race."""
    assert gemini.wait_until_prompting(timeout=15.0), (
        "held FakeGemini.annotate never started — the batch never reached "
        "'prompting'; check the job auto-started and the AI-store fast path hit"
    )
