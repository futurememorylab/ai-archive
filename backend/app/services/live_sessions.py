"""Live-session service — payload assembly, token minting, summarization.

The browser receives the assembled setup payload (which is the literal
contents of the WSS `setup` message it will send to Gemini Live) and the
ephemeral token to authenticate the WSS connection. Audio bytes never
flow through this process — see docs/decisions.md 2026-05-23.
"""

from __future__ import annotations

import json as _json
from datetime import UTC, datetime, timedelta
from typing import Any

import aiosqlite
import httpx

from backend.app.repositories.live_sessions import LiveSessionsRepo
from backend.app.services.live_context import build_context_text

AUTH_TOKENS_URL = "https://generativelanguage.googleapis.com/v1alpha/auth_tokens"
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
            "speechConfig": {
                "languageCode": "cs-CZ",
                "voiceConfig": {
                    "prebuiltVoiceConfig": {"voiceName": settings.gemini_live_voice},
                },
            },
        },
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
    as `?access_token=` against the v1alpha BidiGenerateContentConstrained
    endpoint. `uses=1` makes it single-session.

    ADR 0043 wrongly concluded ephemeral tokens were unworkable: it sent the
    token to the v1beta `BidiGenerateContent` endpoint via `?key=` (which
    reads it as an API key → close code 1007 "API key not valid"). The
    working combination is v1alpha + `...Constrained` + `?access_token=`,
    verified empirically. See ADR 0111 (supersedes 0043).
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
            params={"key": settings.gemini_api_key},
            json=body,
        )
    if r.status_code != 200:
        raise RuntimeError(f"auth_tokens.create failed: {r.status_code} {r.text}")
    name = (r.json() or {}).get("name")
    if not name:
        raise RuntimeError(f"auth_tokens.create returned no token name: {r.text[:200]}")
    return name


def _generate_content_url(model: str) -> str:
    return f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


async def summarize(
    conn: aiosqlite.Connection,
    *,
    session_id: str,
    settings: Any,
) -> bool:
    """Generate + store the Czech summary for a finished session.

    Idempotent: returns False (no-op) if summary already set or transcript
    is empty. Returns True when a new summary was written.
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
    candidates = r.json().get("candidates") or []
    text = ""
    if candidates:
        parts = candidates[0].get("content", {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts).strip()
    if not text:
        return False
    return await repo.set_summary(conn, session_id, text)
