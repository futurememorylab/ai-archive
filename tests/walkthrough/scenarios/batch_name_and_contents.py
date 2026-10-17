"""Walkthrough scenario: name a batch, see what's in it, rename it.

The batches redesign (docs/specs/2026-10-17-batches-redesign-design.md): a
batch is named after its contents (the Annotate modal pre-fills the name), its
row shows a thumbnail stack, Show clips opens the batch's clips in the search
page's clip table, and the name follows the batch into the search results.
"""

from __future__ import annotations

import re

from playwright.sync_api import expect

from tests.walkthrough.fakes import ANNOTATE_SAFE_NAMES
from tests.walkthrough.scenarios._job_support import (
    _ANNO_MODAL,
    assign_production_prompt,
    batch_row,
    expect_toast,
    open_annotate_modal,
    open_batches,
    run_annotate,
    select_clip,
)

SLUG = "batch-name-and-contents"
TOPIC = "Batches page"
TITLE = "Name a batch, see its clips, rename it"
DESCRIPTION = (
    "An operator selects two clips and opens Annotate selected; the batch name "
    "starts as the first clip's name 'and 1 more', and they type their own. On "
    "the Batches page the batch shows under its name with a thumbnail per clip. "
    "They rename it, open Show clips to see both clips with their status, and "
    "find the new name in the search results' Batch column."
)

NAME = "Newsreels and interviews"
RENAMED = "Newsreels 1940s"


def run(wt):
    wt.step("Select the first clip", lambda p: select_clip(p, ANNOTATE_SAFE_NAMES[0]))
    wt.step("Select a second clip", lambda p: select_clip(p, ANNOTATE_SAFE_NAMES[1]))
    wt.step("Open 'Annotate…' from the Actions menu", open_annotate_modal)
    wt.step(
        "The batch name starts as the first clip's name and 1 more",
        lambda p: expect(_name_input(p)).to_have_value(re.compile(r".+ and 1 more$")),
    )
    wt.step("Type a name for the batch", lambda p: _name_input(p).fill(NAME))
    wt.step("Assign the production prompt", assign_production_prompt)
    wt.step("Run the annotation", run_annotate)
    wt.step(
        "A toast confirms the job started for 2 clips",
        lambda p: expect_toast(p, "Annotation started: 2 clips."),
    )
    wt.step("Open the Batches page", open_batches)
    wt.step("The batch shows under its name, one thumbnail per clip", _expect_named_row)
    wt.step(
        "When the AI is done, both clips wait for review",
        lambda p: expect(batch_row(p, NAME).locator('[data-test="batch-headline"]')).to_have_text(
            "2 clips to review", timeout=15000
        ),
    )
    wt.step("Click the pencil to rename the batch", _start_rename)
    wt.step("Type the new name and press Enter", _type_new_name)
    wt.step(
        "The row carries the new name",
        lambda p: expect(batch_row(p, RENAMED)).to_be_visible(),
    )
    wt.step(
        "Show clips",
        lambda p: batch_row(p, RENAMED).locator('[data-test="batch-toggle"]').click(),
    )
    wt.step("Both clips are listed with their status", _expect_clip_list)
    wt.step("Back to the search page", lambda p: p.locator("#rail-clips").click())
    wt.step(
        "The Batch column names the batch",
        lambda p: expect(
            p.locator("tr.vrow")
            .filter(has=p.locator(".name").get_by_text(ANNOTATE_SAFE_NAMES[0], exact=True))
            .locator(".col-batch")
        ).to_have_text(RENAMED),
    )


def _name_input(p):
    return p.locator(_ANNO_MODAL).locator('[data-test="batch-name-input"]')


def _expect_named_row(p) -> None:
    row = batch_row(p, NAME)
    expect(row).to_be_visible()
    expect(row.locator(".bstack .thumb")).to_have_count(2)
    expect(row).to_contain_text("2 clips")


def _start_rename(p) -> None:
    batch_row(p, NAME).get_by_role("button", name="Rename batch").click()


def _type_new_name(p) -> None:
    field = p.locator('[data-test="batch-rename-input"]:visible')
    field.fill(RENAMED)
    field.press("Enter")


def _expect_clip_list(p) -> None:
    clips = batch_row(p, RENAMED).locator(".br-clips .vlist")
    expect(clips).to_be_visible()
    for name in ANNOTATE_SAFE_NAMES:
        expect(clips).to_contain_text(name)
    expect(clips.locator(".pill", has_text="To review")).to_have_count(2)
