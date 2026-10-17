"""Walkthrough scenario: review an AI draft and publish it, watching provenance.

Exercises the #123 writeback redesign end to end: the draft proposals read as
AI · draft, a shared field (decade) reads as an AI · suggestion with a Copy
button and no keep checkbox, and after Accept & apply the published panel shows
the new marker as AI (orange pill) (orange) while the human and legacy markers
survive untouched (grey). The draft carries no how-to box, and the re-run
warning is armed only while proposals are still open.
"""

from __future__ import annotations

from playwright.sync_api import expect

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
    # Accept & apply sits beside the In archive/Draft switch, but only on Draft.
    wt.step(
        "No Accept & apply button on the In archive view",
        lambda p: p.locator('[data-test="apply-draft"]').wait_for(state="hidden"),
    )
    wt.step(
        "Switch to the draft view",
        lambda p: p.locator('button[data-scope="draft"]').click(),
    )
    wt.step(
        "Accept & apply appears next to the Draft switch",
        lambda p: p.locator('.anno-head [data-test="apply-draft"]').wait_for(state="visible"),
    )
    wt.step(
        "No how-to box above the proposals",
        lambda p: expect(p.locator("#draft-aside .edit-hint")).to_have_count(0),
    )
    # Re-running now would replace these open proposals, so Annotate would warn.
    wt.step(
        "Re-running would warn while proposals are open",
        lambda p: expect(p.locator(".detail")).to_have_attribute("data-rerun-confirm", "true"),
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
        "Scroll the proposals; Accept & apply stays in view",
        lambda p: (
            p.locator(".anno-body").evaluate("el => el.scrollTo(0, el.scrollHeight)"),
            expect(p.locator('[data-test="apply-draft"]')).to_be_in_viewport(),
        ),
    )
    wt.step(
        "Accept & apply (publish) the draft",
        lambda p: p.locator('[data-test="apply-draft"]').click(),
    )
    # After the write-queue drains (SyncEngine → the fake applies the ownership
    # rule), the published panel refreshes without a reload.
    wt.step(
        "Switch to the In archive view",
        lambda p: p.locator('button[data-scope="published"]', has_text="In archive").click(),
    )
    wt.step(
        "The Draft dot is gone once nothing is left to apply",
        lambda p: p.locator('[data-test="draft-dot"]').wait_for(state="hidden", timeout=15000),
    )
    wt.step(
        "Re-running no longer warns: nothing is left to overwrite",
        lambda p: expect(p.locator(".detail")).to_have_attribute("data-rerun-confirm", "false"),
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
