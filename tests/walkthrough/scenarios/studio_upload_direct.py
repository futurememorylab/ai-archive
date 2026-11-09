"""Walkthrough scenario: upload a video into a Prompt Studio set.

On Cloud Run the app can't take a request over 32 MB, so the browser sends
the file straight to the media bucket on a signed URL and the app only
handles the start and the finish. Here the bucket is a fake: the browser's
PUT is routed to it in Playwright. A failed transfer says why in a toast and
frees the clip it had reserved.
"""

from __future__ import annotations

from urllib.parse import urljoin

from playwright.sync_api import expect

from tests.walkthrough.fakes import FakeBucket, bucket_fake
from tests.walkthrough.scenarios._job_support import expect_toast

SLUG = "studio-upload-direct"
TOPIC = "Prompt Studio"
TITLE = "Upload a video into a Studio set"
DESCRIPTION = (
    "A researcher drops a video into an uploaded set. It goes straight to "
    "storage, not through the app, so large files work on the cloud app; the "
    "clip card appears in the set. A transfer that fails says why."
)

SET_NAME = "Workshop clips"
GOOD = "duck-and-cover.mp4"
BAD = "broken-transfer.mp4"
SET = f'.studio-set:has(.name:text-is("{SET_NAME}"))'


def _open_uploaded_set(p) -> None:
    p.goto(urljoin(p.url, "/studio"))
    r = p.request.post(urljoin(p.url, "/api/studio/sets?source=uploaded"), data={"name": SET_NAME})
    assert r.ok, r.text()
    p.reload()
    p.locator('[data-nav-source="uploaded"]').click()
    p.locator(SET).locator(".name").click()
    expect(p.locator(SET).locator(".studio-dropzone")).to_be_visible()


def _storage(p, *, refuse: bool) -> None:
    def handle(route):
        req = route.request
        assert req.method == "PUT", req.method
        if refuse:
            route.fulfill(status=403, body="denied")
            return
        clip_id = int(req.url.rsplit("/", 1)[1])
        body = req.post_data_buffer or b""
        bucket_fake().receive(clip_id, len(body), req.headers.get("content-type", ""))
        route.fulfill(status=200, body="")

    p.unroute(f"{FakeBucket.UPLOAD_HOST}/**")
    p.route(f"{FakeBucket.UPLOAD_HOST}/**", handle)


def _pick(p, name: str) -> None:
    p.locator(SET).locator('input[type="file"]').set_input_files(
        {"name": name, "mimeType": "video/mp4", "buffer": b"\x00" * 4096}
    )


def _upload(p) -> None:
    _storage(p, refuse=False)
    _pick(p, GOOD)
    expect_toast(p, f"Uploaded {GOOD}.")
    expect(p.locator(SET).locator(".studio-clip-card").filter(has_text=GOOD)).to_be_visible()


def _failed_transfer(p) -> None:
    before = len(bucket_fake().deleted)
    _storage(p, refuse=True)
    # The browser frees the clip it reserved; the app deletes any partial file.
    with p.expect_response(
        lambda r: r.request.method == "DELETE" and "/api/studio/uploads/direct/" in r.url
    ) as freed:
        _pick(p, BAD)
    assert freed.value.status == 204
    assert len(bucket_fake().deleted) == before + 1
    expect_toast(p, f"Upload failed for {BAD}: storage refused the file (HTTP 403)")
    expect(p.locator(SET).locator(".studio-clip-card").filter(has_text=BAD)).to_have_count(0)


def run(wt):
    wt.step(f'Open Prompt Studio → Uploaded → "{SET_NAME}"', _open_uploaded_set)
    wt.step("Drop a video: it goes straight to storage and joins the set", _upload)
    wt.step("A transfer that fails says why and adds nothing", _failed_transfer)
