"""Guard tests for liveSession.js, the clip chat.

The chat must go through our backend (Vertex, one bill, no Google
credentials in the browser; ADR 0130). These assert on source content
(same pattern as test_studio_uploads_js.py): the browser-direct Live
pieces must not come back.
"""

from pathlib import Path

JS = Path("backend/app/static/liveSession.js").read_text()


def test_chat_talks_only_to_our_backend():
    assert "/api/live/sessions" in JS
    assert "WebSocket" not in JS
    assert "googleapis.com" not in JS
    assert "access_token" not in JS


def test_no_voice_pipeline():
    assert "getUserMedia" not in JS
    assert "AudioContext" not in JS
    assert not Path("backend/app/static/liveAudio.js").exists()


def test_failures_go_to_a_toast():
    assert 'Alpine.store("toast").push' in JS


def test_one_question_at_a_time():
    # Send and Send frame stay disabled while an answer is pending, so the
    # server never sees two turns of one chat at once.
    can_send = JS.split("canSend()")[1].split("},")[0]
    assert "!this.busy" in can_send


def test_answers_stream_with_a_status_line():
    # The page reads the server's NDJSON stream so the bubble can say what
    # Gemini is doing ("Thinking: …") before the whole answer is in.
    assert "getReader()" in JS
    assert "Thinking: " in JS
