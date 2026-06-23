"""Live session API — session-config, transcript persistence, summarize, history."""

import json
import uuid
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from backend.app.auth.guards import require_permission
from backend.app.deps import get_core_ctx, get_live_ctx
from backend.app.repositories.live_sessions import LiveSessionsRepo
from backend.app.repositories.prompts import PromptsRepo
from backend.app.services.live_sessions import (
    assemble_setup_payload,
    mint_ephemeral_token,
    summarize,
)

router = APIRouter(prefix="/api/live", tags=["live"])

WSS_URL_TEMPLATE = (
    # Browser-direct Live API authenticated by a short-lived EPHEMERAL TOKEN
    # (not the raw key): presented via `?access_token=` against the v1alpha
    # BidiGenerateContentConstrained endpoint, which is the endpoint bound
    # tokens authenticate against. Verified empirically with
    # gemini-3.1-flash-live-preview. See ADR 0112 (supersedes 0043).
    "wss://generativelanguage.googleapis.com/ws/"
    "google.ai.generativelanguage.v1alpha.GenerativeService.BidiGenerateContentConstrained"
    "?access_token={token}"
)

# Fields bound into the token server-side and deliberately withheld from the
# browser: the proprietary system prompt and the tool/function declarations.
# The browser sends only the remaining (non-secret) setup fields; the token
# enforces the full config regardless. See ADR 0112.
_TOKEN_ONLY_SETUP_FIELDS = ("systemInstruction", "tools")


# Indirection points so tests can monkeypatch without touching pages.py internals.
async def load_clip_for_live(ctx: Any, clip_id: int) -> dict:
    from backend.app.routes.pages.clips import _build_clip_view_model_for_live

    return await _build_clip_view_model_for_live(ctx, clip_id)


async def load_draft_for_live(ctx: Any, clip_id: int) -> dict:
    from backend.app.routes.pages.clips import _build_draft_view_model_for_live

    return await _build_draft_view_model_for_live(ctx, clip_id)


@router.get("/session-config")
async def session_config(request: Request, clip_id: int) -> dict:
    require_permission(request, "run")
    ctx = get_live_ctx(request)
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
    # Split the pre-built initial user turn out of the setup dict: the browser
    # must send `setup_payload` verbatim as the WSS `setup` frame, and an
    # unknown `initial_context_turn` field there is rejected. It travels as its
    # own response field and is sent only after `setupComplete` (see liveSession.js).
    initial_context_turn = setup_payload.pop("initial_context_turn")
    # Bind the FULL setup (incl. system prompt + tools) into the ephemeral
    # token, then strip the secret fields before sending setup to the browser.
    token = await mint_ephemeral_token(setup=setup_payload, settings=settings)
    client_setup = {
        k: v for k, v in setup_payload.items() if k not in _TOKEN_ONLY_SETUP_FIELDS
    }

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
        # Browser auths with the ephemeral token via ws_url's `?access_token=`;
        # the raw key never leaves the backend, and there is no bare duplicate.
        "ws_url": WSS_URL_TEMPLATE.format(token=token),
        "setup_payload": client_setup,
        "initial_context_turn": initial_context_turn,
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
    ctx = get_core_ctx(request)
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
    ctx = get_core_ctx(request)
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
        "search_calls": s.search_calls,
    }
