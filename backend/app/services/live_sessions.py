"""Live-session service — payload assembly, token minting, summarization.

The browser receives the assembled setup payload (which is the literal
contents of the WSS `setup` message it will send to Gemini Live) and the
ephemeral token to authenticate the WSS connection. Audio bytes never
flow through this process — see docs/decisions.md 2026-05-23.
"""
from __future__ import annotations

import json as _json
from datetime import datetime, timedelta, timezone
from typing import Any

import aiosqlite
import httpx

from backend.app.repositories.live_sessions import LiveSessionsRepo
from backend.app.services.live_context import build_context_text

AUTH_TOKENS_URL = "https://generativelanguage.googleapis.com/v1alpha/auth_tokens"
TOKEN_TTL_MINUTES = 30

SUMMARY_PROMPT_CS = (
    "Shrň následující konverzaci o archivním filmovém záběru "
    "ve 2–4 stručných větách česky. Zaměř se na popis scény, "
    "lokaci, dataci a zjištěné historické souvislosti. "
    "Vrať pouze samotné shrnutí, žádný úvod ani závěr.\n\n"
    "PŘEPIS:\n"
)


def assemble_setup_payload(
    *, clip: dict, draft: dict, prompt_body: str, settings: Any,
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
            {"functionDeclarations": [
                {
                    "name": "end_session",
                    "description": "Ukončit aktuální živou relaci na žádost uživatele.",
                    "parameters": {
                        "type": "object",
                        "properties": {"reason": {"type": "string"}},
                        "required": ["reason"],
                    },
                },
            ]},
        ],
        "initial_context_turn": {
            "role": "user",
            "parts": [{"text": context_text}],
        },
    }


async def mint_ephemeral_token(*, setup: dict, settings: Any) -> str:
    """Return the WSS-side key for browser→Gemini Live.

    Strategy: pass the raw `GEMINI_API_KEY` from settings directly. Ephemeral
    tokens minted via `authTokens.create` open the WSS handshake but Google
    closes the connection with code 1007 "API key not valid" the moment the
    client sends its `setup` frame — verified empirically in browser. The
    most likely cause is a binding mismatch between the setup bound at
    mint time and the setup sent over WSS, but we've spent enough cycles
    on it; the threat model for this single-operator local app over VPN
    does not justify continuing.

    Trade-off recorded in `docs/decisions.md`. If we ever harden auth,
    switch to ephemeral tokens (or short-lived OAuth bearers if Google
    fixes the Vertex AI Live story).
    """
    if not getattr(settings, "gemini_api_key", None):
        raise RuntimeError(
            "GEMINI_API_KEY is not configured; Live audio cannot connect"
        )
    # Touch the unused `setup` arg to keep call-sites stable while we
    # decide between this and a real ephemeral-token mint.
    _ = setup
    return settings.gemini_api_key


def _generate_content_url(model: str) -> str:
    return f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


async def summarize(
    conn: aiosqlite.Connection, *, session_id: str, settings: Any,
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
    lines = [
        f"{t.get('role','?')}: {t.get('text','')}"
        for t in transcript if t.get("text")
    ]
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
