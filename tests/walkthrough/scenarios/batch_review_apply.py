"""Walkthrough scenario: a batch moves from "1 clip to review" to "All in archive".

Runs a real (offline) annotation on a dedicated clip, reviews it from the
Batches page and publishes it. The draft carries an AI marker (written) and a
shared decade field (a copy-only suggestion that is never written, #123); the
batch must still settle to "All in archive" once the marker is published. Before
the fix the leftover suggestion kept the batch waiting for review forever.
"""

from __future__ import annotations

from playwright.sync_api import expect

from tests.walkthrough.fakes import BATCH_REVIEW_CLIP_NAME
from tests.walkthrough.scenarios._job_support import (
    assign_production_prompt,
    batch_row,
    expect_toast,
    open_annotate_modal,
    open_batches,
    run_annotate,
    select_clip,
)

SLUG = "batch-review-apply"
TOPIC = "Batches page"
TITLE = "Review a batch and see it done"
DESCRIPTION = (
    "An operator annotates a clip, opens the Batches page where the run waits "
    "under Needs you, clicks Review, publishes the draft, and returns to see the "
    "batch settle to All in archive under Done, even though the draft's shared "
    "decade field is only a copy-me suggestion that is never written."
)


def run(wt):
    wt.step(
        "Select a clip to annotate",
        lambda p: select_clip(p, BATCH_REVIEW_CLIP_NAME),
    )
    wt.step("Open 'Annotate selected' from the Actions menu", open_annotate_modal)
    wt.step("Assign the production prompt", assign_production_prompt)
    wt.step("Run the annotation", run_annotate)
    wt.step(
        "A toast confirms the job started",
        lambda p: expect_toast(p, "Annotation started — 1 clip(s)."),
    )
    wt.step("Open the Batches page", open_batches)
    wt.step(
        "The finished batch waits for review under Needs you",
        lambda p: _expect_status(p, "1 clip to review", section="attention"),
    )
    wt.step(
        "Click Review on the batch",
        lambda p: _row(p).get_by_role("button", name="Review 1 clip").click(),
    )
    wt.step(
        "The review walk opens the clip's draft",
        lambda p: expect(p.locator('[data-test="apply-draft"]')).to_be_visible(),
    )
    wt.step(
        "Accept & apply (publish) the draft",
        lambda p: p.locator('[data-test="apply-draft"]').click(),
    )
    # Wait on the clip page until the write-back has landed (the published
    # panel refreshes itself), so the Batches page is opened once, settled.
    wt.step(
        "Switch to the In archive view",
        lambda p: p.locator('button[data-scope="published"]', has_text="In archive").click(),
    )
    wt.step(
        "The AI marker is published to the archive",
        lambda p: (
            p.locator("#published-panels")
            .locator(".pill.accent", has_text="AI")
            .first.wait_for(state="visible", timeout=15000)
        ),
    )
    wt.step("Back to the Batches page", open_batches)
    wt.step(
        "The batch is done: All in archive",
        lambda p: _expect_status(p, "All in archive", section="done"),
    )


def _row(p):
    # The Annotate modal names a one-clip batch after its clip.
    return batch_row(p, BATCH_REVIEW_CLIP_NAME)


def _expect_status(p, label: str, *, section: str) -> None:
    """The batch's headline reads `label`, in `section`. The page live-refreshes
    on job events, so this waits for the run to finish without reloading."""
    expect(_row(p).locator('[data-test="batch-headline"]')).to_have_text(label, timeout=15000)
    expect(p.locator(f'[data-test="batch-section-{section}"]')).to_contain_text(
        BATCH_REVIEW_CLIP_NAME
    )
