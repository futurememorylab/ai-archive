"""Walkthrough scenario: review and edit an AI annotation, then publish."""

from __future__ import annotations

SLUG = "review-edit-annotation"
TOPIC = "Clip page"
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
    # The draft opens on the Description tab (renamed from "Markers"): it shows a
    # read-only Summary echo of the notes first, then each proposal badged as a
    # "Shot" (single timestamp) or "Scene" (in + out). Visiting it documents the
    # relabelled tab and the Shot/Scene badges.
    wt.step(
        "Review the Shot/Scene proposals in the Description tab",
        lambda p: p.locator("#draft-aside")
        .get_by_role("button", name="Description")
        .click(),
    )
    # From here on every locator is scoped to the draft panel (#draft-aside),
    # the Alpine-driven review surface rendered by _anno_draft.html. The draft
    # panel has its own tabs / edit / save controls (distinct from the
    # published _anno_panels.html); scoping avoids matching the hidden
    # published panel's same-named elements.
    wt.step(
        "Open the Fields tab",
        lambda p: p.locator("#draft-aside")
        .get_by_role("button", name="Fields")
        .click(),
    )
    # The field card is a plain .ri-card (marker cards carry .ri-marker and live
    # on the hidden Description tab, so exclude them); :visible pins us to the
    # field actually on screen under the Fields tab.
    wt.step(
        "Edit the proposed Decade field",
        lambda p: p.locator(
            "#draft-aside .ri-card:not(.ri-marker):visible"
        )
        .first.get_by_role("button", name="Edit")
        .click(),
    )
    wt.step(
        "Correct the value to 40.léta",
        lambda p: p.locator(
            "#draft-aside .ri-card.editing:not(.ri-marker) input.txt"
        )
        .first.fill("40.léta"),
    )
    wt.step(
        "Save the corrected value",
        lambda p: p.locator(
            "#draft-aside .ri-card.editing:not(.ri-marker)"
        )
        .first.get_by_role("button", name="Save")
        .click(),
    )
    wt.step(
        "Accept & apply (publish) the draft",
        lambda p: p.locator('[data-test="apply-draft"]').click(),
    )
