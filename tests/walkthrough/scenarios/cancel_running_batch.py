"""Walkthrough scenario: cancel a running batch.

Starts a batch whose Gemini call is held open by the slow-Gemini hook so it
stays 'running' long enough to observe progress and click Cancel. The cancel
flips the batch to 'cancelled' (ADR 0115: cancel actually cancels); releasing
the hook lets the run end cleanly.
"""

from __future__ import annotations

from playwright.sync_api import expect

from tests.walkthrough.fakes import ANNOTATE_SAFE_NAMES, gemini_fake
from tests.walkthrough.scenarios._job_support import (
    assign_production_prompt,
    expect_toast,
    open_annotate_modal,
    open_batches,
    run_annotate,
    select_clip,
    wait_until_prompting,
)

SLUG = "cancel-running-batch"
TOPIC = "Batches page"
TITLE = "Cancel a running batch"
DESCRIPTION = (
    "An operator starts an annotation batch, opens the Batches page while it is "
    "still running, and cancels it. The row shows a Cancel button while running "
    "and settles to 'Cancelled' once stopped."
)


def run(wt):
    gemini = gemini_fake()
    # Hold the next Gemini call so the batch stays 'running' long enough to
    # observe progress and click Cancel.
    gemini.hold()
    try:
        wt.step(
            "Select a clip to annotate",
            lambda p: select_clip(p, ANNOTATE_SAFE_NAMES[0]),
        )
        wt.step(
            "Open 'Annotate selected' from the Actions menu",
            open_annotate_modal,
        )
        wt.step(
            "Assign the production prompt",
            assign_production_prompt,
        )
        wt.step(
            "Run the annotation (the model call is held open)",
            run_annotate,
        )
        wt.step(
            "Wait until the batch is actively prompting",
            lambda p: wait_until_prompting(gemini),
        )
        wt.step(
            "Open the Batches page",
            open_batches,
        )
        wt.step(
            "The batch shows as Running with a Cancel button",
            _expect_running_with_cancel,
        )
        wt.step(
            "Cancel the running batch",
            lambda p: p.get_by_role("button", name="Cancel").click(),
        )
        wt.step(
            "A toast confirms the batch is being cancelled",
            lambda p: expect_toast(p, "Cancelling batch…"),
        )
        # Release the held model call so the run ends cleanly (the cancel route
        # already flipped the DB; this just unblocks the background task).
        wt.step(
            "Release the held model call",
            lambda p: gemini.release(),
        )
        wt.step(
            "The batch row settles to Cancelled",
            _expect_settled_cancelled,
        )
    finally:
        # Safety net: never leave the shared fake held if a step above fails,
        # or later scenarios' jobs would block.
        gemini.release()


def _expect_running_with_cancel(p) -> None:
    expect(p.locator("table.batch-tbl")).to_contain_text("Running")
    expect(p.get_by_role("button", name="Cancel")).to_be_visible()


def _expect_settled_cancelled(p) -> None:
    # The Cancel button disappears once the row is no longer running, and the
    # status pill reads "Cancelled".
    expect(p.get_by_role("button", name="Cancel")).to_have_count(0)
    expect(p.locator("table.batch-tbl")).to_contain_text("Cancelled")
