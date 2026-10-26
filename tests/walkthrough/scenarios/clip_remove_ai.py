"""Walkthrough scenario: remove a clip's AI annotations from the archive.

The clip ⋯ menu offers "Remove AI annotations" when the archive holds AI
markers or the AI description. A confirm dialog names what goes and what
stays; confirming deletes only the AI markers and the AI description. The
human marker and the human description stay, and the clip reads "Not
annotated" again so it can be annotated from scratch.
"""

from __future__ import annotations

from urllib.parse import urljoin

from playwright.sync_api import expect

from tests.walkthrough.fakes import (
    REMOVE_AI_CLIP_ID,
    REMOVE_AI_CLIP_NAME,
    REMOVE_AI_HUMAN_MARKER,
    REMOVE_AI_MARKERS,
)
from tests.walkthrough.scenarios._search_support import expect_clip_visible, filter_anno

SLUG = "clip-remove-ai"
TOPIC = "Clip page"
TITLE = "Remove a clip's AI annotations to start again"
DESCRIPTION = (
    "An operator removes the published AI markers and AI description from a "
    "clip. The confirm dialog says what goes and what stays; the human marker "
    "and description are untouched and the clip is back to Not annotated."
)

MORE = '[data-test="clip-more"]'
PUBLISHED = "#published-panels"
DIALOG = '[role="dialog"][aria-label="Remove AI annotations?"]'


def _open_clip(p) -> None:
    p.goto(urljoin(p.url, f"/clips/{REMOVE_AI_CLIP_ID}"))
    panel = p.locator(PUBLISHED)
    for name in (REMOVE_AI_HUMAN_MARKER, *REMOVE_AI_MARKERS):
        expect(panel.get_by_text(name).first).to_be_attached()


def _open_dialog(p) -> None:
    p.locator(MORE).get_by_role("button", name="More actions").click()
    p.locator('[data-test="remove-ai"]').click()
    dialog = p.locator(DIALOG)
    expect(dialog).to_be_visible()
    expect(dialog).to_contain_text("This deletes 2 AI markers and the AI description")
    expect(dialog).to_contain_text("Human markers, the human description and all other metadata")


def _cancel(p) -> None:
    p.locator(DIALOG).get_by_role("button", name="Cancel").click()
    expect(p.locator(DIALOG)).to_be_hidden()
    expect(p.locator(PUBLISHED).get_by_text(REMOVE_AI_MARKERS[0]).first).to_be_attached()


def _confirm(p) -> None:
    p.locator('[data-test="remove-ai-confirm"]').click()
    expect(p.locator(DIALOG)).to_be_hidden()
    panel = p.locator(PUBLISHED)
    for name in REMOVE_AI_MARKERS:
        expect(panel.get_by_text(name)).to_have_count(0, timeout=15000)


def _human_data_stays(p) -> None:
    panel = p.locator(PUBLISHED)
    expect(panel.get_by_text(REMOVE_AI_HUMAN_MARKER).first).to_be_attached()
    expect(panel.get_by_text("popis archiváře").first).to_be_attached()
    expect(panel.get_by_text("večer ohňostroj")).to_have_count(0)
    # Nothing of ours is left to remove, so the action is gone.
    expect(p.locator('[data-test="remove-ai"]')).to_be_hidden()


def _not_annotated(p) -> None:
    p.goto(urljoin(p.url, "/"))
    filter_anno(p, "none")
    expect_clip_visible(p, REMOVE_AI_CLIP_NAME)


def run(wt):
    wt.step("Open a clip with AI markers and an AI description", _open_clip)
    wt.step("⋯ → Remove AI annotations asks first, naming what goes", _open_dialog)
    wt.step("Cancel changes nothing", _cancel)
    wt.step("Confirm: the AI markers leave the archive", lambda p: (_open_dialog(p), _confirm(p)))
    wt.step("The human marker and human description stay", _human_data_stays)
    wt.step("The clip is listed under Not annotated again", _not_annotated)
