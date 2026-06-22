"""Guard tests for liveSession.js stability invariants.

These assert on source content (same pattern as test_studio_uploads_js.py)
because the Gemini Live pipeline can't be exercised headless — it needs a
mic, a real WSS endpoint, and audio hardware. The invariants below encode
the fixes for the "sometimes works, sometimes not" flakiness:

  1. The client MUST wait for the server's `setupComplete` before sending
     any clientContent / realtimeInput. Sending early is a race that Gemini
     closes with 1007/1008 (see docs/decisions.md).
  2. Mic audio chunks must be gated on that same handshake, not merely on
     the socket being open.
  3. Barge-in (`serverContent.interrupted`) must flush queued playback so
     the model stops talking when the operator interrupts.
"""

from pathlib import Path

JS = Path("backend/app/static/liveSession.js").read_text()


def test_waits_for_setup_complete_before_going_active():
    # setupComplete is routed and is the trigger for active state.
    assert "setupComplete" in JS
    # The initial content turn is no longer fired in onopen alongside setup;
    # it goes out only after the handshake completes.
    assert "_onSetupComplete" in JS


def test_onopen_does_not_immediately_send_content_or_go_active():
    # The pre-fix bug: onopen sent setup, then content, then state="active"
    # in the same tick. The initial-turn send must not sit in onopen anymore.
    onopen = JS.split("ws.onopen")[1].split("ws.onmessage")[0]
    assert "_sendInitialClientContent" not in onopen
    assert 'this.state = "active"' not in onopen


def test_audio_capture_gated_on_setup_complete():
    # _onCaptureChunk must not stream realtimeInput until setupComplete.
    # Slice from the method *definition* (not the callback registration in
    # _openMic, which mentions the name first).
    chunk = JS.split("_onCaptureChunk(arrayBuffer)")[1].split("_b64FromBuffer")[0]
    assert "_setupComplete" in chunk


def test_barge_in_flushes_playback():
    assert "interrupted" in JS
    assert "_flushPlayback" in JS


def test_output_audio_context_is_resumed():
    # Lazily-created output context can start suspended under autoplay policy.
    assert "resume()" in JS


def test_no_initial_context_turn_delete_dance():
    # The route now returns initial_context_turn as a separate field, so the
    # frontend must not strip it out of the setup payload anymore.
    assert "delete setup.initial_context_turn" not in JS
    assert "config.initial_context_turn" in JS
