"""Walkthrough scenario: try two prompt versions in the lab and compare them.

A researcher opens the lab (Prompt Studio), sees which prompt is loaded
("Prompt: <name>" with an outlined picker), makes a new draft right there,
runs it and the production version on the same clip, and compares the two.
The version menu marks the versions that have results with a dot, "+ Compare"
opens the other version that was just run, and the Diff toggle works straight
away (it used to need a reload after a version switch, because the swapped
cards reacted to every click twice).
"""

from __future__ import annotations

from urllib.parse import urljoin

from playwright.sync_api import expect

from tests.walkthrough.fakes import CLIP_ID, CLIP_NAME, PRODUCTION_PROMPT_NAME

SLUG = "studio-lab-compare"
TOPIC = "Prompt lab"
TITLE = "Make a draft, run two versions and compare them"
DESCRIPTION = (
    "A researcher makes a new draft in the lab, runs it and the production "
    "version on one clip, sees dots on the versions that have results, and "
    "compares the two without reloading."
)

CUR = '.studio-prompt-card[data-side="cur"]'
CMP = ".cmp-card"
RUN = ".studio-hdr .btn.primary"


def _open_lab(p) -> None:
    prompts = p.request.get(urljoin(p.url, "/api/prompts")).json()
    pid = next(x["id"] for x in prompts if x["name"] == PRODUCTION_PROMPT_NAME)
    r = p.request.post(urljoin(p.url, "/api/studio/sets?source=archive"), data={"name": "Lab"})
    set_id = r.json()["id"]
    p.goto(
        urljoin(
            p.url,
            f"/studio?prompt_id={pid}&clip_id={CLIP_ID}&source=archive&open_set_id={set_id}",
        )
    )
    hdr = p.locator(".studio-hdr")
    expect(hdr.locator(".hdr-label")).to_have_text("Prompt:")
    expect(hdr.locator(".hdr-title-trigger")).to_contain_text(PRODUCTION_PROMPT_NAME)
    expect(p.locator(CUR).locator(".pc-vlbl")).to_have_text("v1")


def _add_clip_from_archive(p) -> None:
    lab_url = p.url
    p.locator(".studio-add-from-archive").click()
    modal = p.locator(".modal-card")
    row = modal.locator("tr.vrow").filter(has=p.locator(".name").get_by_text(CLIP_NAME, exact=True))
    # The ↗ link opens the clip in a new tab; the picker stays.
    expect(row.locator("a.open-tab")).to_have_attribute("target", "_blank")
    # Clicking the row ticks it instead of leaving the lab.
    row.locator(".name").click()
    expect(row.locator("input.row-check")).to_be_checked()
    assert p.url == lab_url
    modal.get_by_role("button", name="Add", exact=True).click()
    card = p.locator(f'.studio-clip-card[data-clip-id="{CLIP_ID}"]')
    expect(card).to_be_visible()
    card.click()


def _new_draft(p) -> None:
    p.locator(CUR).locator("[data-new-draft]").click()
    expect(p.locator(CUR).locator(".pc-vlbl")).to_have_text("v2")
    expect(p.locator(CUR).locator("textarea.pc-editor")).to_be_visible()
    expect(p.locator("#toast-root .toast-msg").filter(has_text="New draft v2")).to_be_visible()


def _run(p) -> None:
    p.locator(CUR).locator(".pc-tab", has_text="Output").click()
    btn = p.locator(RUN)
    expect(btn).to_contain_text("Run on this clip")
    btn.click()
    expect(btn).to_contain_text("Done", timeout=20000)
    expect(btn).to_contain_text("Run on this clip", timeout=5000)
    expect(p.locator(CUR).locator(".run-slot")).to_contain_text("Harbour wide")
    # The timeline under the player shows the new scenes without a reload.
    expect(p.locator(".studio-player .range").first).to_contain_text("Harbour wide")


def _pick_version(p, card: str, num: int) -> None:
    p.locator(card).locator(".pc-vchip > button").click()
    p.locator(card).locator("[data-version-pick]").filter(has_text=f"v{num}").click()
    expect(p.locator(card).locator(".pc-vlbl")).to_have_text(f"v{num}")


def _dot(p, num: int):
    item = p.locator(CUR).locator("[data-version-pick]").filter(has_text=f"v{num}")
    return item.locator("[data-run-dot]")


def _dots_show_results(p) -> None:
    p.locator(CUR).locator(".pc-vchip > button").click()
    expect(_dot(p, 2)).to_have_class("pc-run-dot on")
    expect(_dot(p, 1)).to_have_class("pc-run-dot on")
    p.keyboard.press("Escape")


def _run_production(p) -> None:
    _pick_version(p, CUR, 1)
    _run(p)


def _compare(p) -> None:
    p.locator(CUR).get_by_role("button", name="+ Compare").click()
    expect(p.locator(CMP)).to_have_count(1)
    expect(p.locator(CMP).locator(".pc-vlbl")).to_have_text("v2")
    # The compare card names its model too (read only, no menu).
    model = p.locator(CUR).locator('.model-picker [x-text="model"]').inner_text().strip()
    expect(p.locator(CMP).locator("[data-cmp-model]")).to_contain_text(model)
    expect(p.locator(CMP).locator(".run-slot")).to_contain_text("Harbour wide")
    # Both versions' scenes sit on the timeline, one row each.
    expect(p.locator(".studio-player .range")).to_have_count(2)


def _diff_after_switch(p) -> None:
    # Re-pick the compare version (an HTMX swap of the card), then Diff must
    # still switch on with a single click.
    _pick_version(p, CMP, 2)
    p.locator(CMP).get_by_role("button", name="Diff vs v1").click()
    expect(p.locator(".studio-compare-output")).to_be_visible()
    expect(p.locator(".studio-compare-output")).to_contain_text("Harbour wide")
    p.locator(CMP).get_by_role("button", name="Hide diff").click()
    expect(p.locator(".studio-compare-output")).to_be_hidden()


def run(wt):
    wt.step("Open the lab: the header says which prompt is loaded", _open_lab)
    wt.step("Add a clip from the archive: clicking a row ticks it", _add_clip_from_archive)
    wt.step("+ New draft makes an editable copy without leaving the lab", _new_draft)
    wt.step("Run the new draft on the clip", _run)
    wt.step("Switch to the production version and run it too", _run_production)
    wt.step("Dots in the version menu mark versions with results", _dots_show_results)
    wt.step("+ Compare opens the other version that was just run", _compare)
    wt.step("Diff works straight away, even after switching versions", _diff_after_switch)
