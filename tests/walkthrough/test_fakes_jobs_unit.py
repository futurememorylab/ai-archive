"""Unit tests for the offline AI store + the releasable Gemini fake that drive
the job-start / cancel walkthroughs."""

from __future__ import annotations

import threading

from tests.walkthrough.fakes import FakeAIStore, FakeGemini, gemini_fake


async def test_ai_store_reports_clip_already_uploaded():
    """status() must return non-None so the annotator takes its fast path and
    never needs the local-proxy resolve/upload (offline-safe)."""
    store = FakeAIStore()
    key = ("catdv", "105")
    upload = await store.status(key)
    assert upload is not None
    ref = await store.reference_for_gemini(upload)
    assert ref["uri"].startswith("fake://")


def test_gemini_returns_immediately_when_released():
    """Default (released) gate → annotate returns a deterministic payload with
    no blocking."""
    g = FakeGemini()
    result = g.annotate(file_ref={}, prompt="p", schema={}, model="m")
    assert "text" in result and "raw" in result


def test_gemini_blocks_while_held_then_releases():
    """hold() makes annotate block on its own thread until release() is called;
    wait_until_prompting() observes the held call from another thread."""
    g = FakeGemini()
    g.hold()
    done = threading.Event()
    result: dict = {}

    def _call():
        result["r"] = g.annotate(file_ref={}, prompt="p", schema={}, model="m")
        done.set()

    worker = threading.Thread(target=_call, daemon=True)
    worker.start()

    # The held call has started (reached the gate) but has NOT returned.
    assert g.wait_until_prompting(timeout=5.0)
    assert not done.wait(timeout=0.2)

    # Releasing unblocks it.
    g.release()
    assert done.wait(timeout=5.0)
    assert "text" in result["r"]
    worker.join(timeout=5.0)


def test_gemini_fake_singleton_is_stable():
    """app_server injects the singleton; scenarios reach the SAME instance."""
    assert gemini_fake() is gemini_fake()


async def test_gemini_chat_answers_by_kind_of_turn():
    """chat() greets the opening cue, describes a sent frame, answers a typed
    question, and records every call for the clip-chat walkthrough."""
    from backend.app.services.live_sessions import FRAME_CUE_CS, OPENING_CUE_CS
    from tests.walkthrough.fakes import CHAT_ANSWER, CHAT_FRAME_ANSWER, CHAT_GREETING

    g = FakeGemini()
    opening = [{"role": "user", "text": OPENING_CUE_CS}]
    greeted = [*opening, {"role": "model", "text": CHAT_GREETING}]
    frame = [*greeted, {"role": "user", "text": FRAME_CUE_CS}]
    typed = [*greeted, {"role": "user", "text": "Kdy?"}]

    async def ask(turns):
        return (await g.chat(model="m", system_instruction="s", turns=turns))["text"]

    assert await ask(opening) == CHAT_GREETING
    assert await ask(frame) == CHAT_FRAME_ANSWER
    assert await ask(typed) == CHAT_ANSWER
    assert len(g.chat_calls) == 3
