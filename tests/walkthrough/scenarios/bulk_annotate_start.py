"""Walkthrough scenario: bulk "Annotate selected" → start-confirmation toast.

From the clips list, multi-select clips, assign the seeded production prompt,
and Run. The success toast fires on the POST 201 (independent of completion),
so this is deterministic without the slow-Gemini hook.
"""

from __future__ import annotations

from tests.walkthrough.fakes import ANNOTATE_SAFE_NAMES
from tests.walkthrough.scenarios._job_support import (
    assign_production_prompt,
    expect_toast,
    open_annotate_modal,
    run_annotate,
    select_clip,
)

SLUG = "bulk-annotate-start"
TOPIC = "Clips list"
TITLE = "Start a bulk annotation from the clip list"
DESCRIPTION = (
    "An operator multi-selects clips, opens 'Annotate selected', assigns the "
    "production prompt for the video media kind, and runs it. A toast confirms "
    "the annotation job has started."
)


def run(wt):
    wt.step(
        "Select the first clip to annotate",
        lambda p: select_clip(p, ANNOTATE_SAFE_NAMES[0]),
    )
    wt.step(
        "Select a second clip to annotate",
        lambda p: select_clip(p, ANNOTATE_SAFE_NAMES[1]),
    )
    wt.step(
        "Open 'Annotate selected' from the Actions menu",
        open_annotate_modal,
    )
    wt.step(
        "Assign the production prompt to the video clips",
        assign_production_prompt,
    )
    wt.step(
        "Run the annotation",
        run_annotate,
    )
    wt.step(
        "A toast confirms the job started for 2 clips",
        lambda p: expect_toast(p, "Annotation started — 2 clip(s)."),
    )
