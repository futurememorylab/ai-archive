"""Walkthrough scenario: chat with Gemini about a clip.

The chat goes through our server's `/api/live/sessions*` routes, which the
walkthrough answers with the offline FakeGemini (no network, no archive
seat). The dialog sits under the video with Send frame, End chat (with a
confirmation) and the text box; it stays on screen once a full-size video
has loaded. Answers stream in: "Thinking: …" first, then the text, then the
web searches Gemini ran. Ending the chat closes the dialog and lists it in
the Chats tab.
"""

from __future__ import annotations

from playwright.sync_api import expect

from tests.walkthrough.fakes import (
    CHAT_ANSWER,
    CHAT_FRAME_ANSWER,
    CHAT_GREETING,
    CHAT_SEARCH,
    CHAT_SOURCE_TITLE,
    CHAT_THINKING,
    CLIP_NAME,
    gemini_fake,
)

SLUG = "clip-chat"
TOPIC = "Clip page"
TITLE = "Chat with AI about a clip, under the video"
DESCRIPTION = (
    "An operator starts a chat from the ⋯ menu. Gemini greets and describes "
    "the frame, then answers a typed question (showing what it is thinking "
    "and what it searched) and a sent frame. Send frame "
    "and End chat sit next to the text box; End chat asks first, then closes "
    "the dialog, and the chat is listed in the Chats tab."
)

MORE = '[data-test="clip-more"]'
DIALOG = '[data-test="live-dialog"]'
QUESTION = "Z kterého je to desetiletí?"

# A 1920x1080 poster gives the <video> a full-HD natural size, which once
# pushed the dialog below the window (fixed by the grid's minmax(0, 1fr) row).
_FULL_HD_POSTER = """() => {
  const c = document.createElement("canvas");
  c.width = 1920; c.height = 1080;
  const v = document.querySelector("video.video");
  v.poster = c.toDataURL("image/png");
  return new Promise((ok) => { const i = new Image(); i.onload = ok; i.src = v.poster; });
}"""


def _open_clip(p) -> None:
    gemini_fake().chat_calls.clear()
    p.get_by_text(CLIP_NAME).first.click()
    expect(p.locator(MORE)).to_be_visible()
    expect(p.locator(DIALOG)).to_be_hidden()
    p.evaluate(_FULL_HD_POSTER)


def _start_chat(p) -> None:
    p.locator(MORE).get_by_role("button", name="More actions").click()
    p.locator(MORE).get_by_role("button", name="Chat with AI").click()
    expect(p.locator(DIALOG)).to_contain_text(CHAT_GREETING)
    assert gemini_fake().chat_calls[-1]["google_search"] is False


def _dialog_on_screen(p) -> None:
    box = p.locator(f"{DIALOG} .live-dialog-input").bounding_box()
    height = p.viewport_size["height"] if p.viewport_size else 800
    assert box and box["y"] + box["height"] <= height, (box, height)
    expect(p.locator(f"{DIALOG} [data-test='live-send-frame']")).to_be_visible()
    expect(p.locator(f"{DIALOG} [data-test='live-end']")).to_have_text("End chat")
    expect(p.locator(".detail-hdr").get_by_text("Send frame")).to_have_count(0)


def _drag_chat_taller(p) -> None:
    dialog = p.locator(DIALOG)
    before = dialog.bounding_box()
    grip = p.locator("[data-test='live-resizer']").bounding_box()
    assert before and grip, (before, grip)
    x, y = grip["x"] + grip["width"] / 2, grip["y"] + grip["height"] / 2
    p.mouse.move(x, y)
    p.mouse.down()
    p.mouse.move(x, y - 120, steps=5)
    p.mouse.up()
    after = dialog.bounding_box()
    assert after and after["height"] > before["height"] + 100, (before, after)
    saved = p.evaluate("JSON.parse(localStorage.getItem('catdv:detailLayout') || '{}')")
    assert saved.get("chatH"), saved


def _type_question(p) -> None:
    p.locator(f"{DIALOG} [data-test='live-text']").fill(QUESTION)
    p.locator(f"{DIALOG} [data-test='live-send-text']").click()
    dialog = p.locator(DIALOG)
    expect(dialog.locator(".chat-user")).to_contain_text(QUESTION)
    # While Gemini thinks, its answer bubble says what it is thinking about.
    expect(dialog.locator(".chat-status").last).to_have_text(f"Thinking: {CHAT_THINKING}…")
    expect(dialog.locator(".chat-model").last).to_contain_text(CHAT_ANSWER)
    # The bubble shows just the answer: no blank lines or indent around it.
    assert dialog.locator(".chat-model .chat-bubble").last.inner_text() == CHAT_ANSWER
    sources = dialog.locator(".chat-sources").last
    expect(sources).to_contain_text(f"Searched: {CHAT_SEARCH}")
    expect(sources.get_by_role("link", name=CHAT_SOURCE_TITLE)).to_be_visible()
    expect(p.locator(f"{DIALOG} [data-test='live-text']")).to_have_value("")
    # The server rebuilt the whole conversation: cue, greeting, the question.
    texts = [t["text"] for t in gemini_fake().chat_calls[-1]["turns"]]
    assert texts[1:] == [CHAT_GREETING, QUESTION], texts


def _send_frame(p) -> None:
    p.locator(f"{DIALOG} [data-test='live-send-frame']").click()
    dialog = p.locator(DIALOG)
    expect(dialog.locator(".chat-user").last).to_contain_text("Frame at")
    expect(dialog.locator(".chat-model").last).to_contain_text(CHAT_FRAME_ANSWER)


def _end_chat_asks_first(p) -> None:
    p.locator(f"{DIALOG} [data-test='live-end']").click()
    p.get_by_role("dialog", name="End chat?").get_by_role("button", name="Keep chatting").click()
    expect(p.get_by_role("dialog", name="End chat?")).to_be_hidden()
    expect(p.locator(f"{DIALOG} .live-dialog-input")).to_be_visible()


def _end_chat_closes_the_dialog(p) -> None:
    p.locator(f"{DIALOG} [data-test='live-end']").click()
    p.locator("[data-test='live-end-confirm']").click()
    expect(p.locator(DIALOG)).to_be_hidden()
    expect(p.locator("#toast-root")).to_contain_text("Chats tab")


def _listed_in_chats_tab(p) -> None:
    p.get_by_role("tab", name="Chats").click()
    row = p.locator(".live-history-row").first
    expect(row.locator(".ts")).to_have_text("just now")
    row.click()
    detail = p.locator(".live-history-detail").first
    expect(detail).to_contain_text(QUESTION)
    expect(detail).to_contain_text(CHAT_FRAME_ANSWER)
    expect(detail.locator(".chat-sources", has_text=CHAT_SEARCH)).to_be_visible()


def run(wt):
    wt.step("Open the clip from the list", _open_clip)
    wt.step("Start a chat from the ⋯ menu: Gemini greets", _start_chat)
    wt.step("The dialog stays on screen, with Send frame and End chat in it", _dialog_on_screen)
    wt.step("Drag the border above the chat to make it taller", _drag_chat_taller)
    wt.step(
        "Type a question: Gemini shows what it's thinking, answers, and lists its searches",
        _type_question,
    )
    wt.step("Send frame: Gemini describes the frame", _send_frame)
    wt.step("End chat asks first; Keep chatting leaves the chat open", _end_chat_asks_first)
    wt.step("End chat, confirmed: the dialog closes", _end_chat_closes_the_dialog)
    wt.step("The chat is listed in the Chats tab as just now", _listed_in_chats_tab)
