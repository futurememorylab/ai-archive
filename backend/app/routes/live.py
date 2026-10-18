"""Clip chat API: start a chat, ask a question, end it; plus summary and history.

Each turn is answered here through Vertex Gemini (see services/live_sessions.py
and ADR 0130). The routes keep their Live-era `/api/live` prefix.
"""

import base64
import binascii
import json
import time
import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
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
    clip = await load_clip_for_live(ctx, clip_id)
    draft = await load_draft_for_live(ctx, clip_id)
    return build_system_instruction(prompt_body=version.body, clip=clip, draft=draft)


async def _answer(
    ctx: Any, session: LiveSession, turns: list[ChatTurn], *, call: str, google_search: bool = True
) -> str:
    """Ask Gemini, record the call's cost, and return the reply text. A
    failed call is a 502 carrying the reason the toast shows."""
    model = ctx.settings.gemini_chat_model
    system_instruction = await _system_instruction(ctx, session.clip_id, session.prompt_version)
    started = time.monotonic()
    try:
        result = await ctx.gemini.chat(
            model=model,
            system_instruction=system_instruction,
            turns=turns,
            google_search=google_search,
        )
    except GeminiError as exc:
        raise HTTPException(502, detail=f"Gemini didn't answer: {humanise(exc)}") from exc
    await record_live_usage(
        ctx.db,
        ctx.run_telemetry_repo,
        ctx.telemetry_ctx,
        session=session,
        call=call,
        model=model,
        raw=result["raw"],
        duration_s=time.monotonic() - started,
        attrs={"with_frame": bool(turns[-1].get("image_jpeg"))},
    )
    if not result["text"]:
        raise HTTPException(502, detail="Gemini returned an empty answer. Try asking again.")
    return result["text"]


def _now_ms() -> int:
    return int(datetime.now(UTC).timestamp() * 1000)


class StartPayload(BaseModel):
    clip_id: int
    frame_b64: str | None = None


@router.post("/sessions")
async def start_session(request: Request, body: StartPayload) -> dict:
    """Start a chat: Gemini greets and describes the frame on screen."""
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
        reply = await _answer(ctx, session, opening, call="chat_open", google_search=False)
    except HTTPException:
        await repo.mark_ended(ctx.db, session_id, end_reason="error")
        raise
    transcript = [{"role": "model", "text": reply, "ts": _now_ms()}]
    await repo.save_transcript(
        ctx.db,
        session_id,
        transcript_json=json.dumps(transcript, ensure_ascii=False),
        frame_count=1 if frame else 0,
    )
    return {"session_id": session_id, "reply": reply}


class TurnPayload(BaseModel):
    kind: Literal["typed", "frame"] = "typed"
    # What the transcript shows: the question, or "Frame at 0:12".
    text: str
    frame_b64: str | None = None


@router.post("/sessions/{session_id}/turns")
async def post_turn(request: Request, session_id: str, body: TurnPayload) -> dict:
    """One question (typed, or a sent frame) and Gemini's answer."""
    require_permission(request, "run")
    ctx = get_live_ctx(request)
    repo = LiveSessionsRepo()
    session = await _get_session(ctx, session_id)
    if session.state != "active":
        raise HTTPException(409, detail="This chat has ended. Start a new one.")
    text = body.text.strip()
    if not text:
        raise HTTPException(422, detail="question is empty")
    frame = _decode_frame(body.frame_b64)
    transcript = json.loads(session.transcript_json or "[]")
    transcript.append({"role": "user", "text": text, "ts": _now_ms(), "kind": body.kind})
    turns = conversation_turns(transcript, image_jpeg=frame)
    reply = await _answer(ctx, session, turns, call="chat_turn")
    transcript.append({"role": "model", "text": reply, "ts": _now_ms()})
    await repo.save_transcript(
        ctx.db,
        session_id,
        transcript_json=json.dumps(transcript, ensure_ascii=False),
        frame_count=session.frame_count + (1 if frame else 0),
    )
    return {"reply": reply}


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
    try:
        await summarize(
            ctx.db,
            session_id=session_id,
            gemini=ctx.gemini,
            model=ctx.settings.gemini_model,
            run_telemetry_repo=ctx.run_telemetry_repo,
            telemetry_ctx=ctx.telemetry_ctx,
        )
    except GeminiError as exc:
        raise HTTPException(502, detail=f"Summary failed: {humanise(exc)}") from exc
    session = await _get_session(ctx, session_id)
    return {"summary_cs": session.summary_cs}


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
    repo = LiveSessionsRepo()
    try:
        s = await repo.get(ctx.db, session_id)
    except LookupError:
        raise HTTPException(404, detail="session not found") from None
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
