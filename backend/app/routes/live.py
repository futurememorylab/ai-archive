"""Clip chat API: start a chat, ask a question, end it; plus summary and history.

Each turn is answered here through Vertex Gemini (see services/live_sessions.py
and ADR 0130). The routes keep their Live-era `/api/live` prefix.
"""

import asyncio
import base64
import binascii
import json
import logging
import time
import uuid
from collections.abc import AsyncGenerator, AsyncIterator, Callable
from contextlib import aclosing
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from backend.app.auth.guards import require_permission
from backend.app.deps import get_core_ctx, get_live_ctx
from backend.app.models.live_session import LiveSession
from backend.app.models.prompt import CHAT_SYSTEM_PROMPT_NAME
from backend.app.repositories.live_sessions import LiveSessionsRepo
from backend.app.repositories.prompts import PromptsRepo
from backend.app.services.errors import humanise
from backend.app.services.gemini import ChatTurn, GeminiError
from backend.app.services.live_sessions import (
    OPENING_CUE_CS,
    build_system_instruction,
    conversation_turns,
    record_live_usage,
    summarize,
)

router = APIRouter(prefix="/api/live", tags=["live"])
log = logging.getLogger(__name__)

# A 1280x720 JPEG frame is ~150 KB; anything far bigger is not a frame.
_MAX_FRAME_BYTES = 4_000_000


# Indirection points so tests can monkeypatch without touching pages.py internals.
async def load_clip_for_live(ctx: Any, clip_id: int) -> dict:
    from backend.app.routes.pages.clips import _build_clip_view_model_for_live

    return await _build_clip_view_model_for_live(ctx, clip_id)


async def load_draft_for_live(ctx: Any, clip_id: int) -> dict:
    from backend.app.routes.pages.clips import _build_draft_view_model_for_live

    return await _build_draft_view_model_for_live(ctx, clip_id)


def _decode_frame(frame_b64: str | None) -> bytes | None:
    if not frame_b64:
        return None
    try:
        data = base64.b64decode(frame_b64, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(422, detail="frame is not valid base64") from None
    if len(data) > _MAX_FRAME_BYTES:
        raise HTTPException(413, detail="frame is too large")
    return data


async def _system_instruction(ctx: Any, clip_id: int, prompt_version: int | None) -> str:
    """The chat's system prompt version plus the clip's context as it is now
    (rebuilt every turn, so draft edits made mid-chat are seen)."""
    try:
        version = await PromptsRepo().get_version(ctx.db, prompt_version or 0)
    except LookupError:
        raise HTTPException(500, detail="chat system instruction missing") from None
    clip, draft = await asyncio.gather(
        load_clip_for_live(ctx, clip_id), load_draft_for_live(ctx, clip_id)
    )
    return build_system_instruction(prompt_body=version.body, clip=clip, draft=draft)


# An answer streams for as long as Gemini thinks, searches and writes (one
# took 27 s). The stream ends with the answer, so it can't idle like the job
# event streams that #131 capped; this only stops a call that never ends.
_ANSWER_TIMEOUT_S = 120
# Vertex sometimes goes silent mid-stream and never finishes (staging,
# 2026-10-23); a second try usually answers in seconds. Thoughts arrive every
# few seconds while it works and a web search sends nothing, so this is well
# past the slowest whole answer seen (27 s).
_IDLE_TIMEOUT_S = 45

# What an answer adds to its transcript entry when Gemini searched the web.
_GROUNDING_KEYS = ("searches", "sources")


def _line(event: dict[str, Any]) -> bytes:
    return (json.dumps(event, ensure_ascii=False) + "\n").encode()


async def _stream_answer(
    ctx: Any,
    session: LiveSession,
    turns: list[ChatTurn],
    *,
    call: str,
    transcript: list[dict[str, Any]],
    frame_count: int,
    done_extra: dict[str, Any] | None = None,
    end_on_error: bool = False,
    google_search: bool = True,
) -> StreamingResponse:
    """Stream Gemini's answer to the page as NDJSON lines: "thinking" and
    "text" events as they come, then "done" or "error" (the reason the toast
    shows). Before "done" the answer is appended to `transcript` and saved,
    and its cost recorded. `end_on_error` ends the chat when there is no
    answer (the opening turn: the page has no chat to go back to)."""
    model = ctx.settings.gemini_chat_model
    system_instruction = await _system_instruction(ctx, session.clip_id, session.prompt_version)
    repo = LiveSessionsRepo()

    async def error(detail: str) -> bytes:
        if end_on_error:
            await repo.mark_ended(ctx.db, session.id, end_reason="error")
        return _line({"type": "error", "detail": detail})

    async def body() -> AsyncIterator[bytes]:
        started = time.monotonic()
        final: dict[str, Any] = {}
        events = _answer_events(
            lambda: ctx.gemini.chat_stream(
                model=model,
                system_instruction=system_instruction,
                turns=turns,
                google_search=google_search,
            ),
            label=f"chat {call} (session {session.id})",
        )
        try:
            # aclosing: a page that goes away closes the Gemini stream now.
            async with aclosing(events):
                async for event in events:
                    if event["type"] == "done":
                        final = event
                    else:
                        yield _line(event)
        except (GeminiError, TimeoutError) as exc:
            # A TimeoutError from _answer_events says why: silence or the cap.
            reason = humanise(exc) if isinstance(exc, GeminiError) else str(exc)
            log.warning(
                "chat %s failed after %.1fs (session %s): %s",
                call,
                time.monotonic() - started,
                session.id,
                reason,
            )
            yield await error(f"Gemini didn't answer: {reason}")
            return
        await record_live_usage(
            ctx.db,
            ctx.run_telemetry_repo,
            ctx.telemetry_ctx,
            session=session,
            call=call,
            model=model,
            raw=final.get("raw"),
            duration_s=time.monotonic() - started,
            attrs={"with_frame": bool(turns[-1].get("image_jpeg"))},
        )
        if not final.get("text"):
            yield await error("Gemini returned an empty answer. Try asking again.")
            return
        entry = {"role": "model", "text": final["text"], "ts": _now_ms()}
        entry.update({k: final[k] for k in _GROUNDING_KEYS if final.get(k)})
        await repo.save_transcript(
            ctx.db,
            session.id,
            transcript_json=json.dumps([*transcript, entry], ensure_ascii=False),
            frame_count=frame_count,
        )
        yield _line({"type": "done", "reply": entry, **(done_extra or {})})

    return StreamingResponse(body(), media_type="application/x-ndjson")


async def _answer_events(
    open_stream: Callable[[], AsyncGenerator[dict[str, Any]]], *, label: str
) -> AsyncGenerator[dict[str, Any]]:
    """The events of `open_stream()` (a Gemini chat_stream), opening it again
    once when it goes silent for _IDLE_TIMEOUT_S before any answer text was
    sent (the page shows only thoughts until then, so nothing is written
    twice) and there is time left for a second try. Otherwise a silence, or
    reaching _ANSWER_TIMEOUT_S, raises TimeoutError carrying the reason. The
    retry's lost thinking is not in the cost: Vertex reports usage only at
    the end of a stream."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + _ANSWER_TIMEOUT_S
    for attempt in range(2):
        wrote = False
        async with aclosing(open_stream()) as events:
            try:
                while True:
                    async with asyncio.timeout_at(min(deadline, loop.time() + _IDLE_TIMEOUT_S)):
                        event = await anext(events, None)
                    if event is None:
                        return
                    wrote = wrote or event["type"] == "text"
                    yield event
            except TimeoutError:
                left = deadline - loop.time()
                if left <= 0:
                    raise TimeoutError("it took too long") from None
                if wrote or attempt or left < _IDLE_TIMEOUT_S:
                    raise TimeoutError("it went silent") from None
        log.warning("%s went silent; asking Gemini again", label)


def _now_ms() -> int:
    return int(datetime.now(UTC).timestamp() * 1000)


class StartPayload(BaseModel):
    clip_id: int
    frame_b64: str | None = None


@router.post("/sessions")
async def start_session(request: Request, body: StartPayload) -> StreamingResponse:
    """Start a chat: Gemini greets and describes the frame on screen,
    streamed; the "done" line carries the new chat's session_id."""
    require_permission(request, "run")
    ctx = get_live_ctx(request)
    frame = _decode_frame(body.frame_b64)
    prompts = PromptsRepo()
    prompt = await prompts.get_by_name(ctx.db, CHAT_SYSTEM_PROMPT_NAME)
    version = await prompts.get_production_version(ctx.db, prompt.id) if prompt else None
    if version is None:
        raise HTTPException(500, detail="chat system instruction has no production version")

    repo = LiveSessionsRepo()
    session_id = uuid.uuid4().hex
    await repo.insert_pending(
        ctx.db, id=session_id, clip_id=body.clip_id, prompt_version=version.id
    )
    await repo.mark_active(ctx.db, session_id)
    session = await repo.get(ctx.db, session_id)
    opening: list[ChatTurn] = [{"role": "user", "text": OPENING_CUE_CS, "image_jpeg": frame}]
    try:
        # The greeting only describes the frame; it needs no web search.
        return await _stream_answer(
            ctx,
            session,
            opening,
            call="chat_open",
            transcript=[],
            frame_count=1 if frame else 0,
            done_extra={"session_id": session_id},
            end_on_error=True,
            google_search=False,
        )
    except Exception:
        # The stream never started (e.g. the archive didn't answer): the
        # browser has no session id to end, so end it here.
        await repo.mark_ended(ctx.db, session_id, end_reason="error")
        raise


class TurnPayload(BaseModel):
    kind: Literal["typed", "frame"] = "typed"
    # What the transcript shows: the question, or "Frame at 0:12".
    text: str
    frame_b64: str | None = None


@router.post("/sessions/{session_id}/turns")
async def post_turn(request: Request, session_id: str, body: TurnPayload) -> StreamingResponse:
    """One question (typed, or a sent frame) and Gemini's answer, streamed.
    A failed answer leaves the question unsaved; the chat stays usable."""
    require_permission(request, "run")
    ctx = get_live_ctx(request)
    session = await _get_session(ctx, session_id)
    if session.state != "active":
        raise HTTPException(409, detail="This chat has ended. Start a new one.")
    text = body.text.strip()
    if not text:
        raise HTTPException(422, detail="question is empty")
    frame = _decode_frame(body.frame_b64)
    if body.kind == "frame" and frame is None:
        raise HTTPException(
            422, detail="The video has no picture yet. Play it, then send the frame."
        )
    transcript = json.loads(session.transcript_json or "[]")
    transcript.append({"role": "user", "text": text, "ts": _now_ms(), "kind": body.kind})
    turns = conversation_turns(transcript, image_jpeg=frame)
    return await _stream_answer(
        ctx,
        session,
        turns,
        call="chat_turn",
        transcript=transcript,
        frame_count=session.frame_count + (1 if frame else 0),
    )


class EndPayload(BaseModel):
    end_reason: Literal["user_stop", "navigate", "error"] = "user_stop"


@router.post("/sessions/{session_id}/end")
async def end_session(request: Request, session_id: str, body: EndPayload) -> dict:
    """End the chat. The transcript was saved turn by turn, so this only
    closes the session (a navigate beacon after End chat changes nothing)."""
    ctx = get_core_ctx(request)
    session = await _get_session(ctx, session_id)
    if session.state != "ended":
        await LiveSessionsRepo().mark_ended(ctx.db, session_id, end_reason=body.end_reason)
    return {"ok": True}


async def _get_session(ctx: Any, session_id: str) -> LiveSession:
    try:
        return await LiveSessionsRepo().get(ctx.db, session_id)
    except LookupError:
        raise HTTPException(404, detail="session not found") from None


@router.post("/sessions/{session_id}/summarize")
async def post_summarize(request: Request, session_id: str) -> dict:
    ctx = get_live_ctx(request)
    await _get_session(ctx, session_id)
    await _summarize(ctx, session_id)
    session = await _get_session(ctx, session_id)
    return {"summary_cs": session.summary_cs}


async def _summarize(ctx: Any, session_id: str) -> None:
    """`summarize` with the chat's own model (it is what's being summarised);
    a failure, or Gemini taking too long, is the request's error."""
    try:
        await summarize(
            ctx.db,
            session_id=session_id,
            gemini=ctx.gemini,
            model=ctx.settings.gemini_chat_model,
            run_telemetry_repo=ctx.run_telemetry_repo,
            telemetry_ctx=ctx.telemetry_ctx,
        )
    except GeminiError as exc:
        raise HTTPException(502, detail=f"Summary failed: {humanise(exc)}") from exc
    except TimeoutError:
        log.warning("chat summary timed out (session %s)", session_id)
        raise HTTPException(504, detail="Summary failed: Gemini took too long.") from None


@router.get("/sessions")
async def list_sessions(request: Request, clip_id: int) -> list[dict]:
    ctx = get_core_ctx(request)
    repo = LiveSessionsRepo()
    rows = await repo.list_by_clip(ctx.db, clip_id)
    out = []
    for s in rows:
        duration_s = None
        if s.started_at and s.ended_at:
            from datetime import datetime

            try:
                duration_s = (
                    datetime.fromisoformat(s.ended_at) - datetime.fromisoformat(s.started_at)
                ).total_seconds()
            except ValueError:
                duration_s = None
        out.append(
            {
                "id": s.id,
                "started_at": s.started_at,
                "ended_at": s.ended_at,
                "duration_s": duration_s,
                "end_reason": s.end_reason,
                "state": s.state,
                "has_summary": s.summary_cs is not None,
                "frame_count": s.frame_count,
            }
        )
    return out


@router.get("/sessions/{session_id}")
async def get_session(request: Request, session_id: str) -> dict:
    ctx = get_core_ctx(request)
    s = await _get_session(ctx, session_id)
    return {
        "id": s.id,
        "clip_id": s.clip_id,
        "state": s.state,
        "started_at": s.started_at,
        "ended_at": s.ended_at,
        "end_reason": s.end_reason,
        "transcript": json.loads(s.transcript_json or "[]"),
        "summary_cs": s.summary_cs,
        "frame_count": s.frame_count,
    }
