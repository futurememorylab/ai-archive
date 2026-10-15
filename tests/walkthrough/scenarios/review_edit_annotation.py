"""Walkthrough scenario: review an AI draft and publish it, watching provenance.

Exercises the #123 writeback redesign end to end: the draft proposals read as
AI · draft, a shared field (decade) reads as an AI · suggestion with a Copy
button and no keep checkbox, and after Accept & apply the published panel shows
the new marker as AI (orange pill) (orange) while the human and legacy markers
survive untouched (grey).
"""

from __future__ import annotations

SLUG = "review-edit-annotation"
TOPIC = "Clip page"
TITLE = "Review an AI draft and publish it"
DESCRIPTION = (
    "An operator opens a clip with a pending AI draft, sees the proposals badged "
    "AI · draft and the shared decade field offered as a copy-only suggestion, "
    "then publishes — the new marker turns orange (AI (orange pill)) while the "
    "human and legacy markers stay grey and untouched."
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
    # A pending draft is flagged by a blue dot on the Draft button.
    wt.step(
        "See the blue dot on the Draft button",
        lambda p: p.locator('[data-test="draft-dot"]').wait_for(state="visible"),
    )
    wt.step(
        "Switch to the draft view",
        lambda p: p.locator('button[data-scope="draft"]').click(),
    )
    # The Description tab lists the AI proposals, each badged AI · draft (blue).
    wt.step(
        "Open the Description tab",
        lambda p: p.locator("#draft-aside").get_by_role("button", name="Description").click(),
    )
    wt.step(
        "See the proposal badged AI · draft",
        lambda p: (
            p.locator("#draft-aside").get_by_text("AI · draft").first.wait_for(state="visible")
        ),
    )
    wt.step(
        "Open the Fields tab",
        lambda p: p.locator("#draft-aside").get_by_role("button", name="Fields").click(),
    )
    # The decade field is a SHARED field the app does not own → a suggestion:
    # AI · suggestion, a Copy button, and no keep checkbox / edit pencil.
    wt.step(
        "See the decade field as an AI · suggestion",
        lambda p: (
            p.locator("#draft-aside").get_by_text("AI · suggestion").first.wait_for(state="visible")
        ),
    )
    wt.step(
        "The suggestion offers a Copy button (not a write)",
        lambda p: p.locator("#draft-aside .ri-suggestion [data-copy]").first.wait_for(
            state="visible"
        ),
    )
    wt.step(
        "Accept & apply (publish) the draft",
        lambda p: p.locator('[data-test="apply-draft"]').click(),
    )
    # After the write-queue drains (SyncEngine → the fake applies the ownership
    # rule), the published panel refreshes without a reload.
    wt.step(
        "Switch to the published view",
        lambda p: p.locator('button[data-scope="published"]').click(),
    )
    wt.step(
        "The Draft dot is gone once nothing is left to review",
        lambda p: p.locator('[data-test="draft-dot"]').wait_for(state="hidden", timeout=15000),
    )
    wt.step(
        "Open the published Description tab",
        lambda p: p.locator("#published-panels").get_by_role("tab", name="Description").click(),
    )
    wt.step(
        "The published marker now reads AI",
        lambda p: (
            p.locator("#published-panels")
            .locator(".pill.accent", has_text="AI")
            .first.wait_for(state="visible", timeout=15000)
        ),
    )
    wt.step(
        "The human marker survived the publish",
        lambda p: (
            p.locator("#published-panels")
            .get_by_text("Opening titles")
            .first.wait_for(state="visible")
        ),
    )
    wt.step(
        "The legacy marker survived the publish",
        lambda p: (
            p.locator("#published-panels").get_by_text("Interview").first.wait_for(state="visible")
        ),
    )
    # The timeline splits by provenance: a human band carries .origin-human
    # (grey) and the published AI band does not (orange).
    wt.step(
        "A human band is grey on the timeline",
        lambda p: p.locator(".timeline .ranges.range-cur .range.origin-human").first.wait_for(
            state="visible"
        ),
    )
    wt.step(
        "An AI band is orange on the timeline",
        lambda p: p.locator(".timeline .ranges.range-cur .range:not(.origin-human)").first.wait_for(
            state="visible"
        ),
    )
