"""Walkthrough scenario: chat with Gemini about a clip.

The chat goes through our server to Gemini on Vertex. The walkthrough is
offline, so the scenario answers the `/api/live/sessions*` calls itself.
The dialog sits under the video with Send frame, End chat (with a
confirmation) and the text box; the transcript stays after the chat ends.
"""

from __future__ import annotations

import json

from playwright.sync_api import expect

from tests.walkthrough.fakes import CLIP_NAME

SLUG = "clip-chat"
TOPIC = "Clip page"
TITLE = "Chat with Gemini about a clip, under the video"
DESCRIPTION = (
    "An operator starts a chat from the ⋯ menu. Gemini greets and describes "
    "the frame, then answers a typed question and a sent frame. Send frame "
    "and End chat sit next to the text box; End chat asks first, and the "
    "transcript stays after the chat ends."
)

MORE = '[data-test="clip-more"]'
DIALOG = '[data-test="live-dialog"]'
GREETING = "Dobrý den, vidím zahradu."
ANSWER = "Podle oblečení spíš třicátá léta."
FRAME_ANSWER = "Na snímku jsou dvě děti u plotu."

# Each question the page sent, as the server received it.
_turns: list[dict] = []
_ended: list[str] = []


def _stand_in_for_the_server(p) -> None:
    _turns.clear()
    _ended.clear()
    p.route(
        "**/api/live/sessions",
        lambda r: r.fulfill(json={"session_id": "wt-chat", "reply": GREETING}),
    )

    def turn(route) -> None:
        body = json.loads(route.request.post_data or "{}")
        _turns.append(body)
        reply = FRAME_ANSWER if body.get("kind") == "frame" else ANSWER
        route.fulfill(json={"reply": reply})

    def end(route) -> None:
        _ended.append(json.loads(route.request.post_data or "{}").get("end_reason"))
        route.fulfill(json={"ok": True})

    p.route("**/api/live/sessions/wt-chat/turns", turn)
    p.route("**/api/live/sessions/wt-chat/end", end)
    p.route(
        "**/api/live/sessions/wt-chat/summarize",
        lambda r: r.fulfill(json={"summary_cs": None}),
    )


def _open_clip(p) -> None:
    p.get_by_text(CLIP_NAME).first.click()
    expect(p.locator(MORE)).to_be_visible()
    expect(p.locator(DIALOG)).to_be_hidden()


def _start_chat(p) -> None:
    p.locator(MORE).get_by_role("button", name="More actions").click()
    p.locator(MORE).get_by_role("button", name="Chat with Gemini").click()
    expect(p.locator(DIALOG)).to_contain_text(GREETING)


def _controls_in_dialog(p) -> None:
    expect(p.locator(f"{DIALOG} [data-test='live-send-frame']")).to_be_visible()
    expect(p.locator(f"{DIALOG} [data-test='live-end']")).to_have_text("End chat")
    expect(p.locator(".detail-hdr").get_by_text("Send frame")).to_have_count(0)


def _type_question(p) -> None:
    p.locator(f"{DIALOG} [data-test='live-text']").fill("Z kterého je to desetiletí?")
    p.locator(f"{DIALOG} [data-test='live-send-text']").click()
    dialog = p.locator(DIALOG)
    expect(dialog.locator(".chat-user")).to_contain_text("Z kterého je to desetiletí?")
    expect(dialog.locator(".chat-model").last).to_contain_text(ANSWER)
    expect(p.locator(f"{DIALOG} [data-test='live-text']")).to_have_value("")
    assert _turns[-1]["kind"] == "typed", _turns


def _send_frame(p) -> None:
    p.locator(f"{DIALOG} [data-test='live-send-frame']").click()
    dialog = p.locator(DIALOG)
    expect(dialog.locator(".chat-user").last).to_contain_text("Frame at")
    expect(dialog.locator(".chat-model").last).to_contain_text(FRAME_ANSWER)
    assert _turns[-1]["kind"] == "frame", _turns


def _end_chat_asks_first(p) -> None:
    p.locator(f"{DIALOG} [data-test='live-end']").click()
    p.get_by_role("dialog", name="End chat?").get_by_role("button", name="Keep chatting").click()
    expect(p.get_by_role("dialog", name="End chat?")).to_be_hidden()
    expect(p.locator(f"{DIALOG} .live-dialog-input")).to_be_visible()
    assert _ended == []


def _end_chat_keeps_transcript(p) -> None:
    p.locator(f"{DIALOG} [data-test='live-end']").click()
    p.locator("[data-test='live-end-confirm']").click()
    dialog = p.locator(DIALOG)
    expect(dialog.locator(".live-dialog-input")).to_be_hidden()
    expect(dialog).to_contain_text(ANSWER)
    assert _ended == ["user_stop"], _ended


def run(wt):
    wt.step("Stand in for the server's Gemini answers", _stand_in_for_the_server)
    wt.step("Open the clip from the list", _open_clip)
    wt.step("Start a chat from the ⋯ menu: Gemini greets", _start_chat)
    wt.step("Send frame and End chat sit in the dialog, not the header", _controls_in_dialog)
    wt.step("Type a question and get the answer in the dialog", _type_question)
    wt.step("Send frame: Gemini describes the frame", _send_frame)
    wt.step("End chat asks first; Keep chatting leaves the chat open", _end_chat_asks_first)
    wt.step("End chat, confirmed: the transcript stays under the video", _end_chat_keeps_transcript)
