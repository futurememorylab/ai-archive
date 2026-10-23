"""Clip chat service: the conversation with Gemini about one clip.

Every turn goes browser → this server → Vertex Gemini (the same client and
billing as annotation); credentials never reach the browser. The server
keeps the transcript in `live_sessions.transcript_json` and rebuilds the
conversation from it on each turn. The table keeps its Live-era name; see
ADR 0130.
"""

from __future__ import annotations

import asyncio
import json as _json
import logging
from datetime import UTC, datetime
from typing import Any

import aiosqlite

from backend.app.models.live_session import LiveSession
from backend.app.models.telemetry import RunTelemetryRecord, TelemetryCtx
from backend.app.repositories.live_sessions import LiveSessionsRepo
from backend.app.repositories.run_telemetry import RunTelemetryRepo
from backend.app.services.gemini import ChatTurn
from backend.app.services.live_context import build_context_text
from backend.app.services.pricing import compute_cost
from backend.app.services.telemetry_capture import extract_finish_reason, extract_usage

log = logging.getLogger(__name__)

# The summary is one call with no stream; without a cap it once ran into
# Cloud Run's 300 s request limit while Vertex never answered.
SUMMARY_TIMEOUT_S = 60

# The first user turn of every chat: greet and describe the opening frame.
# The page may have no picture to send (video not loaded, or the browser
# won't read it), so the cue only asks for a description when there is one.
OPENING_CUE_CS = (
    'Pozdrav mě prosím krátce česky („Dobrý den"). Pokud máš snímek, popiš '
    "v jedné větě, co na něm vidíš. Pak počkej na moji další otázku."
)
# What a "Send frame" turn asks; the transcript shows "Frame at 0:12" instead.
FRAME_CUE_CS = "Popiš, co vidíš na tomto snímku."

SUMMARY_PROMPT_CS = (
    "Shrň následující konverzaci o archivním filmovém záběru "
    "ve 2–4 stručných větách česky. Zaměř se na popis scény, "
    "lokaci, dataci a zjištěné historické souvislosti. "
    "Vrať pouze samotné shrnutí, žádný úvod ani závěr."
)


def build_system_instruction(*, prompt_body: str, clip: dict, draft: dict) -> str:
    """The system prompt plus the clip's Czech context (published + draft)."""
    return f"{prompt_body}\n\n{build_context_text(clip, draft)}"


def conversation_turns(transcript: list[dict], *, image_jpeg: bytes | None) -> list[ChatTurn]:
    """What Gemini sees, rebuilt from the transcript: the opening cue, then
    every line, ending with the new question. A sent frame reads as the frame
    question. Earlier frames are not re-sent: only the last turn has an image."""
    turns: list[ChatTurn] = [{"role": "user", "text": OPENING_CUE_CS}]
    for entry in transcript:
        if entry.get("role") == "model":
            turns.append({"role": "model", "text": entry.get("text", "")})
        elif entry.get("role") == "user":
            text = FRAME_CUE_CS if entry.get("kind") == "frame" else entry.get("text", "")
            turns.append({"role": "user", "text": text})
    turns[-1]["image_jpeg"] = image_jpeg
    return turns


async def record_live_usage(
    conn: aiosqlite.Connection,
    repo: RunTelemetryRepo,
    tctx: TelemetryCtx,
    *,
    session: LiveSession,
    call: str,
    model: str,
    raw: dict[str, Any] | None,
    duration_s: float | None = None,
    attrs: dict[str, Any] | None = None,
) -> None:
    """Write one kind='live' run_telemetry row for a chat turn or the
    session summary.

    `raw` is a response-shaped dict carrying `usageMetadata`. media_kind and
    prompt_version_id stay NULL on purpose: the estimator keys on both, so
    chat rows count towards spend totals but never skew annotation estimates.
    Like the annotator's telemetry, this never raises."""
    try:
        usage = extract_usage(raw or {})
        cost_usd, pricing_version = compute_cost(usage, model)
        await repo.insert(
            conn,
            RunTelemetryRecord(
                occurred_at=datetime.now(UTC).isoformat(),
                install_id=tctx.install_id,
                app_version=tctx.app_version,
                kind="live",
                archive_id=tctx.archive_id,
                clip_id=session.clip_id,
                prompt_version_id=None,
                model=model,
                status="ok",
                finish_reason=extract_finish_reason(raw or {}),
                attempt_count=1,
                duration_s=duration_s,
                tokens_in=usage.tokens_in,
                tokens_in_text=usage.tokens_in_text,
                tokens_in_video=usage.tokens_in_video,
                tokens_in_audio=usage.tokens_in_audio,
                tokens_in_image=usage.tokens_in_image,
                tokens_cached=usage.tokens_cached,
                tokens_out=usage.tokens_out,
                tokens_thinking=usage.tokens_thinking,
                cost_usd=cost_usd,
                pricing_version=pricing_version,
                attrs={"call": call, "live_session_id": session.id, **(attrs or {})},
            ),
        )
    except Exception:  # noqa: BLE001 — telemetry must never fail the request
        log.exception("live run_telemetry insert failed (session %s)", session.id)


async def summarize(
    conn: aiosqlite.Connection,
    *,
    session_id: str,
    gemini: Any,
    model: str,
    run_telemetry_repo: RunTelemetryRepo | None = None,
    telemetry_ctx: TelemetryCtx | None = None,
) -> bool:
    """Generate + store the Czech summary for a finished session.

    `gemini` is anything with GeminiService's `chat` (the real service, or a
    test fake).

    Idempotent: returns False (no-op) if summary already set or transcript
    is empty. Returns True when a new summary was written. When the
    telemetry repo + ctx are given, the call's token usage is recorded as a
    kind='live' run_telemetry row (call='summary').
    """
    repo = LiveSessionsRepo()
    session = await repo.get(conn, session_id)
    if session.summary_cs is not None:
        return False
    transcript = _json.loads(session.transcript_json or "[]")
    lines = [f"{t.get('role', '?')}: {t.get('text', '')}" for t in transcript if t.get("text")]
    if not lines:
        return False
    # Only the Gemini call is capped (TimeoutError): the writes below must
    # not be cut off halfway on the shared connection.
    async with asyncio.timeout(SUMMARY_TIMEOUT_S):
        result = await gemini.chat(
            model=model,
            system_instruction=SUMMARY_PROMPT_CS,
            turns=[{"role": "user", "text": "PŘEPIS:\n" + "\n".join(lines)}],
            google_search=False,
        )
    if run_telemetry_repo is not None and telemetry_ctx is not None:
        await record_live_usage(
            conn,
            run_telemetry_repo,
            telemetry_ctx,
            session=session,
            call="summary",
            model=model,
            raw=result["raw"],
        )
    if not result["text"]:
        return False
    return await repo.set_summary(conn, session_id, result["text"])
