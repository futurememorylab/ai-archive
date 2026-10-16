"""Walkthrough scenario: a draft marker seeks right after an annotation run.

Regression cover for "clicking a fresh draft annotation doesn't jump the video
until a reload". Annotating is what caches an uncached clip, so if the operator
pressed play before the run, the <video> already tried /api/media, got a 404
and stayed in its error state: every later seek/play was silently ignored and
made no new request. player.js now re-runs resource selection (load()) on a
seek or play when the element is errored.

The 404 is simulated by routing /api/media/<clip> in the browser; the clip's
proxy is served normally once the route is lifted. The assertion is on the
fresh media request, which holds even where the headless Chromium can't decode
the proxy.
"""

from __future__ import annotations

from playwright.sync_api import expect

from tests.walkthrough.fakes import ANNOTATE_SAFE_NAMES, PRODUCTION_PROMPT_NAME
from tests.walkthrough.scenarios._job_support import expect_toast

SLUG = "clip-annotate-seek-draft"
TOPIC = "Clip page"
TITLE = "Jump to a fresh draft marker right after annotating"
DESCRIPTION = (
    "An operator opens a clip that isn't cached yet and presses play, which "
    "fails. They annotate it (which caches it), then click the new draft "
    "marker: the player fetches the proxy again and jumps to the marker "
    "without a page reload."
)

CLIP_NAME = ANNOTATE_SAFE_NAMES[1]  # clip 106
MEDIA_GLOB = "**/api/media/106"
PLAY = '[data-test="player-play"]'


def _not_cached_yet(p) -> None:
    p.route(MEDIA_GLOB, lambda route: route.fulfill(status=404, body="not cached"))


def _open_clip(p) -> None:
    p.get_by_text(CLIP_NAME).first.click()
    expect(p.locator(PLAY)).to_be_visible()


def _play_fails(p) -> None:
    p.locator(PLAY).click()
    p.wait_for_function("document.querySelector('video.video').error !== null")


def _annotate(p) -> None:
    p.unroute(MEDIA_GLOB)  # the run caches the proxy: media is servable from now on
    p.get_by_role("button", name="Annotate").click()
    p.locator(".popover-panel .menu-item").filter(has_text=PRODUCTION_PROMPT_NAME).first.click()
    # Earlier scenarios (bulk annotate) may have left a draft on this clip; the
    # server renders that into data-rerun-confirm, so branch on it, not on a
    # dialog that may not have painted yet.
    if p.locator(".detail").get_attribute("data-rerun-confirm") == "true":
        p.get_by_role("button", name="Re-run").click()
    expect_toast(p, "Annotation complete.")


def _click_draft_marker(p) -> None:
    card = p.locator("#draft-aside .ri-marker").first
    expect(card).to_be_visible()
    with p.expect_request(lambda r: r.url.endswith("/api/media/106")):
        card.click()


def run(wt):
    wt.step("The clip's proxy isn't cached yet", _not_cached_yet)
    wt.step("Open the clip from the list", _open_clip)
    wt.step("Press play — the proxy can't load yet", _play_fails)
    wt.step("Annotate the clip (this caches it)", _annotate)
    wt.step("Click the new draft marker — the player fetches and seeks", _click_draft_marker)
