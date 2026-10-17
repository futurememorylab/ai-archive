"""Walkthrough scenario: cancel a running batch, then restart it.

Starts a batch whose Gemini call is held open by the slow-Gemini hook so it
stays 'running' long enough to observe progress and click Cancel. The cancel
flips the batch to 'cancelled' (ADR 0115: cancel actually cancels), which the
Batches page shows as Stopped; releasing the hook lets the run end cleanly.
Restart then runs the stopped clip to the end.

While the batch runs, the page refreshes on a timer and on job events; the
row must be updated in place (its thumbnail element survives), not rebuilt.
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
TITLE = "Cancel a running batch and restart it"
DESCRIPTION = (
    "An operator starts an annotation batch, opens the Batches page while it is "
    "still running, and cancels it. The row shows a Cancel button while running "
    "and settles to Stopped under Done once cancelled. Restart runs the stopped "
    "clip, and the batch moves to To review with the clip to review."
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
            "Open 'Annotate…' from the Actions menu",
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
            "The row updates in place while it runs, without flashing",
            _expect_row_kept_across_refreshes,
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
            "The batch row settles to Stopped",
            _expect_settled_cancelled,
        )
        wt.step(
            "Restart the stopped clip",
            lambda p: p.get_by_role("button", name="Restart 1 stopped").click(),
        )
        wt.step(
            "A toast confirms the restart",
            lambda p: expect_toast(p, "Restarting the stopped clips…"),
        )
        wt.step(
            "The clip runs again and waits for review",
            lambda p: expect(p.locator('[data-test="batch-section-attention"]')).to_contain_text(
                "1 clip to review", timeout=15000
            ),
        )
    finally:
        # Safety net: never leave the shared fake held if a step above fails,
        # or later scenarios' jobs would block.
        gemini.release()


def _expect_running_with_cancel(p) -> None:
    running = p.locator('[data-test="batch-section-running"]')
    expect(running).to_contain_text("Running · 0 of 1 done")
    expect(running.get_by_role("button", name="Cancel")).to_be_visible()


def _expect_row_kept_across_refreshes(p) -> None:
    # Tag the live thumbnail, let the 3s poll refresh the table at least once,
    # and check the same element is still there: a rebuilt row would reload it.
    thumb = p.locator('[data-test="batch-section-running"] .bstack .thumb').first
    thumb.evaluate("el => { el.dataset.kept = '1'; }")
    p.wait_for_timeout(4000)
    kept = p.locator('[data-test="batch-section-running"] .bstack .thumb[data-kept="1"]')
    expect(kept).to_have_count(1)


def _expect_settled_cancelled(p) -> None:
    # The Cancel button disappears once the row is no longer running, and the
    # batch reads "Stopped" under Done.
    expect(p.get_by_role("button", name="Cancel")).to_have_count(0)
    expect(p.locator('[data-test="batch-section-done"]')).to_contain_text("Stopped")
