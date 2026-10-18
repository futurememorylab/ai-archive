"""Walkthrough scenario: a Live session without a microphone.

Live talks to Gemini over a WebSocket straight from the browser. The
walkthrough is offline, so the scenario stands in for both ends: it answers
`/api/live/*` itself and plays Gemini on a routed WebSocket (setupComplete,
then a transcribed reply per turn). The page has no microphone, so the
session starts in typing mode. The dialog sits under the video with Send
frame, End chat (with a confirmation) and the text box; a connection Google
ends (goAway) resumes with the latest handle; the transcript stays after the
chat ends.
"""

from __future__ import annotations

import json

from playwright.sync_api import expect

from tests.walkthrough.fakes import CLIP_NAME

SLUG = "live-typed-dialog"
TOPIC = "Clip page"
TITLE = "Live without a microphone: type questions under the video"
DESCRIPTION = (
    "An operator without a microphone starts Live. The dialog opens under the "
    "video, says to type instead, and answers a typed question. Send frame "
    "and End chat sit next to the text box; End chat asks first, and the "
    "transcript stays after the chat ends."
)

MORE = '[data-test="clip-more"]'
DIALOG = '[data-test="live-dialog"]'
WS_URL = "wss://live.walkthrough.test/ws"
GREETING = "Dobrý den, vidím zahradu."
ANSWER = "Podle oblečení spíš třicátá léta."
AFTER_RESUME = "Ano, pořád vás slyším."

# One entry per connection: the setup frame the page opened it with.
_setups: list[dict] = []
_sockets: list = []


def _fake_gemini(ws) -> None:
    """Gemini's side of the socket: ACK setup, answer each complete turn."""
    _sockets.append(ws)

    def reply(text: str) -> None:
        ws.send(json.dumps({"serverContent": {"outputTranscription": {"text": text}}}))
        ws.send(json.dumps({"serverContent": {"turnComplete": True}}))

    def on_message(raw) -> None:
        msg = json.loads(raw)
        if "setup" in msg:
            _setups.append(msg["setup"])
            ws.send(json.dumps({"setupComplete": {}}))
            ws.send(json.dumps({"sessionResumptionUpdate": {"newHandle": "h1", "resumable": True}}))
        elif "clientContent" in msg:
            parts = msg["clientContent"]["turns"][0]["parts"]
            if len(parts) > 1:
                reply(GREETING)
            else:
                reply(AFTER_RESUME if len(_setups) > 1 else ANSWER)

    ws.on_message(on_message)


def _stand_in_for_google(p) -> None:
    _setups.clear()
    _sockets.clear()
    # This operator has no microphone.
    p.add_init_script(
        "navigator.mediaDevices.getUserMedia = () =>"
        " Promise.reject(new DOMException('no microphone', 'NotFoundError'));"
    )
    config = {
        "session_id": "wt-live",
        "ws_url": WS_URL,
        "setup_payload": {"model": "models/gemini-3.8-live"},
        "initial_context_turn": {"role": "user", "parts": [{"text": "kontext"}]},
    }
    p.route("**/api/live/session-config*", lambda r: r.fulfill(json=config))
    p.route("**/api/live/sessions/**", lambda r: r.fulfill(json={"ok": True, "summary_cs": None}))
    p.route_web_socket(WS_URL, _fake_gemini)


def _open_clip(p) -> None:
    p.get_by_text(CLIP_NAME).first.click()
    expect(p.locator(MORE)).to_be_visible()
    expect(p.locator(DIALOG)).to_be_hidden()


def _start_live(p) -> None:
    p.locator(MORE).get_by_role("button", name="More actions").click()
    p.locator(MORE).get_by_role("button", name="Live").click()
    dialog = p.locator(DIALOG)
    expect(dialog).to_contain_text("No microphone available")
    expect(dialog).to_contain_text(GREETING)
    expect(p.locator(".live-bar")).to_contain_text("Typing only")


def _controls_in_dialog(p) -> None:
    expect(p.locator(f"{DIALOG} [data-test='live-send-frame']")).to_be_visible()
    expect(p.locator(f"{DIALOG} [data-test='live-end']")).to_have_text("End chat")
    expect(p.locator(".detail-hdr").get_by_text("Send frame")).to_have_count(0)
    expect(p.locator(".detail-hdr").get_by_role("button")).to_have_count(0)


def _type_question(p) -> None:
    p.locator(f"{DIALOG} [data-test='live-text']").fill("Z kterého je to desetiletí?")
    p.locator(f"{DIALOG} [data-test='live-send-text']").click()
    dialog = p.locator(DIALOG)
    expect(dialog.locator(".chat-user")).to_contain_text("Z kterého je to desetiletí?")
    expect(dialog.locator(".chat-model").last).to_contain_text(ANSWER)
    expect(p.locator(f"{DIALOG} [data-test='live-text']")).to_have_value("")


def _resume_after_go_away(p) -> None:
    _sockets[-1].send(json.dumps({"goAway": {"timeLeft": "1s"}}))
    # Send stays disabled until the new connection is set up; click() waits.
    p.locator(f"{DIALOG} [data-test='live-text']").fill("Slyšíte mě?")
    p.locator(f"{DIALOG} [data-test='live-send-text']").click()
    expect(p.locator(DIALOG).locator(".chat-model").last).to_contain_text(AFTER_RESUME)
    assert _setups[-1].get("sessionResumption") == {"handle": "h1"}, _setups


def _end_chat_asks_first(p) -> None:
    p.locator(f"{DIALOG} [data-test='live-end']").click()
    p.get_by_role("dialog", name="End chat?").get_by_role("button", name="Keep talking").click()
    expect(p.get_by_role("dialog", name="End chat?")).to_be_hidden()
    expect(p.locator(f"{DIALOG} .live-dialog-input")).to_be_visible()


def _end_chat_keeps_transcript(p) -> None:
    p.locator(f"{DIALOG} [data-test='live-end']").click()
    p.locator("[data-test='live-end-confirm']").click()
    dialog = p.locator(DIALOG)
    expect(dialog).to_contain_text(ANSWER)
    expect(dialog.locator(".live-dialog-input")).to_be_hidden()


def run(wt):
    wt.step("Stand in for Gemini; this operator has no microphone", _stand_in_for_google)
    wt.step("Open the clip from the list", _open_clip)
    wt.step("Start Live from the ⋯ menu: it opens in typing mode", _start_live)
    wt.step("Send frame and End chat sit in the dialog, not the header", _controls_in_dialog)
    wt.step("Type a question and get the answer in the dialog", _type_question)
    wt.step("Google ends the connection: Live resumes and keeps going", _resume_after_go_away)
    wt.step("End chat asks first; Keep talking leaves the session running", _end_chat_asks_first)
    wt.step("End chat, confirmed: the transcript stays under the video", _end_chat_keeps_transcript)
