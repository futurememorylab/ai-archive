"""Walkthrough scenario: review and edit an AI annotation, then publish."""

from __future__ import annotations

SLUG = "review-edit-annotation"
TITLE = "Review and edit an AI annotation"
DESCRIPTION = (
    "An operator opens a clip with a pending AI draft, plays the proxy, reviews "
    "the suggested decade field, corrects it, and publishes the accepted draft."
)


def run(wt):
    wt.step(
        "Open the clip from the list",
        lambda p: p.get_by_text("archive_30s").first.click(),
    )
    wt.step(
        "Play the proxy to spot-check",
        lambda p: p.locator('[data-test="player-play"]').click(),
    )
    wt.step(
        "Switch to the draft view",
        lambda p: p.locator('button[data-scope="draft"]').click(),
    )
    wt.step(
        "Open the Fields tab",
        lambda p: p.locator('[data-test="tab-fields"]').click(),
    )
    wt.step(
        "Edit the proposed Decade field",
        lambda p: p.locator('[data-test="ri-edit-toggle"]').first.click(),
    )
    wt.step(
        "Correct the value to 40.léta",
        lambda p: p.locator("input[data-item-id]").first.fill("40.léta"),
    )
    wt.step(
        "Accept & apply (publish) the draft",
        lambda p: p.locator('[data-test="apply-draft"]').click(),
    )
