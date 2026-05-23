"""Live session API — session-config, transcript persistence, summarize, history."""

import json
import uuid
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from backend.app.repositories.live_sessions import LiveSessionsRepo
from backend.app.repositories.prompts import PromptsRepo
from backend.app.services.live_sessions import (
    assemble_setup_payload,
    mint_ephemeral_token,
    summarize,
)

router = APIRouter(prefix="/api/live", tags=["live"])

WSS_URL_TEMPLATE = (
    # Browser-direct Live API: the GEMINI_API_KEY is presented via `?key=`
    # against the v1beta BidiGenerateContent endpoint. The Live-capable
    # native-audio models (gemini-2.5-flash-native-audio-*) are only
    # surfaced on v1beta; v1alpha returns close code 1008 "model is not
    # found for API version v1alpha". See docs/decisions.md 2026-05-23.
    "wss://generativelanguage.googleapis.com/ws/"
    "google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent"
    "?key={token}"
)


# Indirection points so tests can monkeypatch without touching pages.py internals.
async def load_clip_for_live(ctx: Any, clip_id: int) -> dict:
    from backend.app.routes.pages import _build_clip_view_model_for_live

    return await _build_clip_view_model_for_live(ctx, clip_id)


async def load_draft_for_live(ctx: Any, clip_id: int) -> dict:
    from backend.app.routes.pages import _build_draft_view_model_for_live

    return await _build_draft_view_model_for_live(ctx, clip_id)


@router.get("/session-config")
async def session_config(request: Request, clip_id: int) -> dict:
    ctx = request.app.state.ctx
    settings = ctx.settings

    clip = await load_clip_for_live(ctx, clip_id)
    draft = await load_draft_for_live(ctx, clip_id)

    prompts = PromptsRepo()
    prompt = await prompts.get_by_name(ctx.db, "live.system_instruction.cs")
    if prompt is None:
        raise HTTPException(500, detail="live system instruction prompt missing")
    version = await prompts.get_production_version(ctx.db, prompt.id)
    if version is None:
        raise HTTPException(500, detail="live system instruction has no production version")

    setup_payload = assemble_setup_payload(
        clip=clip,
        draft=draft,
        prompt_body=version.body,
        settings=settings,
    )
    token = await mint_ephemeral_token(setup=setup_payload, settings=settings)

    session_id = uuid.uuid4().hex
    repo = LiveSessionsRepo()
    await repo.insert_pending(
        ctx.db,
        id=session_id,
        clip_id=clip_id,
        prompt_version=version.id,
    )

    return {
        "session_id": session_id,
        "token": token,
        "ws_url": WSS_URL_TEMPLATE.format(token=token),
        "setup_payload": setup_payload,
        "inactivity_s": settings.gemini_live_inactivity_s,
    }


class TranscriptEntry(BaseModel):
    role: str
    text: str
    ts: float | int | None = None
    kind: str | None = None


class TranscriptPayload(BaseModel):
    end_reason: Literal["user_stop", "voice_stop", "inactivity", "navigate", "error"]
    transcript: list[TranscriptEntry]
    frame_count: int = 0
    search_calls: int = 0


@router.post("/sessions/{session_id}/transcript")
async def post_transcript(
    request: Request,
    session_id: str,
    body: TranscriptPayload,
) -> dict:
    ctx = request.app.state.ctx
    repo = LiveSessionsRepo()
    try:
        await repo.get(ctx.db, session_id)
    except LookupError:
        raise HTTPException(404, detail="session not found") from None
    await repo.mark_ended(
        ctx.db,
        session_id,
        end_reason=body.end_reason,
        transcript_json=json.dumps(
            [t.model_dump() for t in body.transcript],
            ensure_ascii=False,
        ),
        frame_count=body.frame_count,
        search_calls=body.search_calls,
    )
    return {"ok": True}


@router.post("/sessions/{session_id}/summarize")
async def post_summarize(request: Request, session_id: str) -> dict:
    ctx = request.app.state.ctx
    repo = LiveSessionsRepo()
    try:
        await repo.get(ctx.db, session_id)
    except LookupError:
        raise HTTPException(404, detail="session not found") from None
    await summarize(ctx.db, session_id=session_id, settings=ctx.settings)
    session = await repo.get(ctx.db, session_id)
    return {"summary_cs": session.summary_cs}


@router.get("/sessions")
async def list_sessions(request: Request, clip_id: int) -> list[dict]:
    ctx = request.app.state.ctx
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
    ctx = request.app.state.ctx
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
        "search_calls": s.search_calls,
    }
