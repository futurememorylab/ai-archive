"""Live-session service — payload assembly, token minting, summarization.

The browser receives the assembled setup payload (which is the literal
contents of the WSS `setup` message it will send to Gemini Live) and the
ephemeral token to authenticate the WSS connection. Audio bytes never
flow through this process — see docs/decisions.md 2026-05-23.
"""

from __future__ import annotations

import json as _json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import aiosqlite
import httpx

from backend.app.models.live_session import LiveSession
from backend.app.models.telemetry import RunTelemetryRecord, TelemetryCtx
from backend.app.repositories.live_sessions import LiveSessionsRepo
from backend.app.repositories.run_telemetry import RunTelemetryRepo
from backend.app.services.live_context import build_context_text
from backend.app.services.pricing import compute_cost
from backend.app.services.telemetry_capture import extract_finish_reason, extract_usage

log = logging.getLogger(__name__)

# v1beta: Google now documents ephemeral tokens as v1beta-only (the v1alpha
# surface this used until 2026-10 stopped working). See ADR 0130.
AUTH_TOKENS_URL = "https://generativelanguage.googleapis.com/v1beta/auth_tokens"
# How long the minted token may keep an open session sending messages.
TOKEN_TTL_MINUTES = 30
# How long the browser has to OPEN the session after the token is minted.
NEW_SESSION_WINDOW_MINUTES = 2

SUMMARY_PROMPT_CS = (
    "Shrň následující konverzaci o archivním filmovém záběru "
    "ve 2–4 stručných větách česky. Zaměř se na popis scény, "
    "lokaci, dataci a zjištěné historické souvislosti. "
    "Vrať pouze samotné shrnutí, žádný úvod ani závěr.\n\n"
    "PŘEPIS:\n"
)


def _speech_config(settings: Any) -> dict:
    """The setup's speechConfig: the voice, plus a language code only when
    configured (native-audio models reject one; see ADR 0130)."""
    config: dict[str, Any] = {
        "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": settings.gemini_live_voice}},
    }
    language_code = getattr(settings, "gemini_live_language_code", None)
    if language_code:
        config["languageCode"] = language_code
    return config


def assemble_setup_payload(
    *,
    clip: dict,
    draft: dict,
    prompt_body: str,
    settings: Any,
) -> dict:
    """Return the dict the browser sends as the WSS `setup` message + a
    pre-built initial user turn carrying the Czech context.

    `settings` is duck-typed to anything with `gemini_live_model` /
    `gemini_live_voice` attributes (the real `Settings` object, or a
    test stub).
    """
    context_text = build_context_text(clip, draft)
    # NOTE: BidiGenerateContentSetup is FLAT — fields like systemInstruction,
    # tools, outputAudioTranscription, inputAudioTranscription sit at the
    # top level alongside `model` and `generationConfig`. The earlier shape
    # nested everything under `config` (the REST `generateContent` style),
    # which Google's authTokens.create rejected with:
    #   "Unknown name \"config\" at 'auth_token.bidi_generate_content_setup'"
    # Only `responseModalities` + `speechConfig` belong inside generationConfig.
    return {
        "model": f"models/{settings.gemini_live_model}",
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": _speech_config(settings),
        },
        # Without compression an audio+video session is cut off after 2
        # minutes (15 for audio only); the sliding window lifts that cap.
        "contextWindowCompression": {"slidingWindow": {}},
        # Google drops every connection after ~10 minutes. Resumption makes
        # it send handles the browser reconnects with (liveSession.js).
        "sessionResumption": {},
        "outputAudioTranscription": {},
        "inputAudioTranscription": {},
        "systemInstruction": {"parts": [{"text": prompt_body}]},
        "tools": [
            {"googleSearch": {}},
            {
                "functionDeclarations": [
                    {
                        "name": "end_session",
                        "description": "Ukončit aktuální živou relaci na žádost uživatele.",
                        "parameters": {
                            "type": "object",
                            "properties": {"reason": {"type": "string"}},
                            "required": ["reason"],
                        },
                    },
                ]
            },
        ],
        "initial_context_turn": {
            "role": "user",
            "parts": [{"text": context_text}],
        },
    }


def _rfc3339_z(dt: datetime) -> str:
    """Format a UTC datetime as RFC 3339 with a trailing Z, e.g.
    2026-06-22T10:00:00.000Z — the shape Google's auth_tokens API expects."""
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")


async def mint_ephemeral_token(*, setup: dict, settings: Any) -> str:
    """Mint a short-lived, config-bound ephemeral token for the browser's
    WSS handshake — so the raw GEMINI_API_KEY never leaves the backend.

    The real key authenticates THIS request (server→Google) only. The token
    is bound (`bidiGenerateContentSetup`) to the full `setup` — model,
    generationConfig, systemInstruction and tools — so the browser cannot
    change the model, voice, or system prompt, and only needs to send a
    minimal `setup` frame. The returned `name` is presented by the browser
    as `?access_token=` against the BidiGenerateContentConstrained
    endpoint. `uses=1` makes it single-session; Google still accepts the
    same token for session-resumption reconnects within `expireTime`.

    The token goes to the v1beta `...Constrained` endpoint as
    `?access_token=` (ADR 0112 found the triad; ADR 0130 moved it from
    v1alpha to v1beta when Google retired the v1alpha path).
    """
    if not getattr(settings, "gemini_api_key", None):
        raise RuntimeError("GEMINI_API_KEY is not configured; Live audio cannot connect")
    now = datetime.now(tz=UTC)
    body = {
        "uses": 1,
        "expireTime": _rfc3339_z(now + timedelta(minutes=TOKEN_TTL_MINUTES)),
        "newSessionExpireTime": _rfc3339_z(now + timedelta(minutes=NEW_SESSION_WINDOW_MINUTES)),
        "bidiGenerateContentSetup": setup,
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(
            AUTH_TOKENS_URL,
            headers={"x-goog-api-key": settings.gemini_api_key},
            json=body,
        )
    if r.status_code != 200:
        raise RuntimeError(f"auth_tokens.create failed: {r.status_code} {r.text}")
    name = (r.json() or {}).get("name")
    if not name:
        raise RuntimeError(f"auth_tokens.create returned no token name: {r.text[:200]}")
    return name


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
    """Write one kind='live' run_telemetry row for a Gemini Live audio session
    or its summary call — the Gemini spend that used to have no row at all.

    `raw` is a response-shaped dict carrying `usageMetadata`. media_kind and
    prompt_version_id stay NULL on purpose: the estimator keys on both, so
    live rows count towards spend totals but never skew annotation estimates.
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


def _generate_content_url(model: str) -> str:
    return f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


async def summarize(
    conn: aiosqlite.Connection,
    *,
    session_id: str,
    settings: Any,
    run_telemetry_repo: RunTelemetryRepo | None = None,
    telemetry_ctx: TelemetryCtx | None = None,
) -> bool:
    """Generate + store the Czech summary for a finished session.

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
    if not transcript:
        return False
    lines = [f"{t.get('role', '?')}: {t.get('text', '')}" for t in transcript if t.get("text")]
    full_prompt = SUMMARY_PROMPT_CS + "\n".join(lines)
    body = {"contents": [{"role": "user", "parts": [{"text": full_prompt}]}]}
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(
            _generate_content_url(settings.gemini_model),
            params={"key": settings.gemini_api_key},
            json=body,
        )
    if r.status_code != 200:
        raise RuntimeError(f"generateContent failed: {r.status_code} {r.text}")
    payload = r.json() or {}
    if run_telemetry_repo is not None and telemetry_ctx is not None:
        await record_live_usage(
            conn,
            run_telemetry_repo,
            telemetry_ctx,
            session=session,
            call="summary",
            model=settings.gemini_model,
            raw=payload,
        )
    candidates = payload.get("candidates") or []
    text = ""
    if candidates:
        parts = candidates[0].get("content", {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts).strip()
    if not text:
        return False
    return await repo.set_summary(conn, session_id, text)
