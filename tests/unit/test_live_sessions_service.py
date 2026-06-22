import json

import pytest
import respx
from httpx import Response

from backend.app.services.live_sessions import (
    assemble_setup_payload,
    mint_ephemeral_token,
    summarize,
)


class _Settings:
    gemini_live_model = "gemini-2.5-flash-native-audio-latest"
    gemini_live_voice = "Aoede"


def _clip():
    return dict(
        id=42,
        name="P1010001",
        format="9,5 mm",
        fps=25,
        duration_secs=120.0,
        duration_smpte="00:02:00:00",
        notes="rodinný výlet",
        big_notes="",
        markers=[],
        fields={"pragafilm.dekáda.natočení": "20.léta"},
    )


def _draft():
    return dict(markers=[], fields={}, notes="myslím, že je to Praha")


def test_setup_payload_top_level_model_and_generation_config():
    # BidiGenerateContentSetup is FLAT: model + generationConfig + tools +
    # systemInstruction + transcription configs sit at the same level. Only
    # responseModalities/speechConfig nest inside generationConfig.
    p = assemble_setup_payload(
        clip=_clip(),
        draft=_draft(),
        prompt_body="SYSTÉM INSTRUKCE",
        settings=_Settings(),
    )
    assert p["model"] == "models/gemini-2.5-flash-native-audio-latest"
    gc = p["generationConfig"]
    assert gc["responseModalities"] == ["AUDIO"]
    assert gc["speechConfig"]["languageCode"] == "cs-CZ"
    assert gc["speechConfig"]["voiceConfig"]["prebuiltVoiceConfig"]["voiceName"] == "Aoede"
    assert p["outputAudioTranscription"] == {}
    assert p["inputAudioTranscription"] == {}
    # No legacy "config" wrapper — that was the shape Google rejected with
    # `Unknown name "config" at 'auth_token.bidi_generate_content_setup'`.
    assert "config" not in p


def test_setup_payload_has_system_instruction_text():
    p = assemble_setup_payload(
        clip=_clip(),
        draft=_draft(),
        prompt_body="MŮJ ČESKÝ SYSTÉM",
        settings=_Settings(),
    )
    parts = p["systemInstruction"]["parts"]
    assert parts == [{"text": "MŮJ ČESKÝ SYSTÉM"}]


def test_setup_payload_declares_google_search_and_end_session_tools():
    p = assemble_setup_payload(
        clip=_clip(),
        draft=_draft(),
        prompt_body="x",
        settings=_Settings(),
    )
    tools = p["tools"]
    assert {"googleSearch": {}} in tools
    fd = next(t for t in tools if "functionDeclarations" in t)["functionDeclarations"]
    assert any(d["name"] == "end_session" for d in fd)
    end = next(d for d in fd if d["name"] == "end_session")
    assert end["parameters"]["required"] == ["reason"]


def test_setup_payload_initial_context_turn_has_text_part():
    p = assemble_setup_payload(
        clip=_clip(),
        draft=_draft(),
        prompt_body="x",
        settings=_Settings(),
    )
    turn = p["initial_context_turn"]
    assert turn["role"] == "user"
    text_part = next(part for part in turn["parts"] if "text" in part)
    assert "Publikované anotace" in text_part["text"]
    assert "Rozpracované anotace" in text_part["text"]
    assert "P1010001" in text_part["text"]
    assert "myslím, že je to Praha" in text_part["text"]


class _SettingsWithKey(_Settings):
    gemini_api_key = "test-key-XYZ"


AUTH_TOKENS_ENDPOINT = "https://generativelanguage.googleapis.com/v1alpha/auth_tokens"


@pytest.mark.asyncio
@respx.mock
async def test_mint_ephemeral_token_mints_bound_single_use_token():
    """The raw GEMINI_API_KEY never goes to the browser. mint_ephemeral_token
    POSTs to v1alpha auth_tokens, binding the full `setup` into the token via
    `bidiGenerateContentSetup`, and returns the short-lived token name (which
    the browser presents as `?access_token=`). See ADR 0112 (supersedes 0043).
    """
    route = respx.post(AUTH_TOKENS_ENDPOINT).mock(
        return_value=Response(200, json={"name": "auth_tokens/ephemeral-abc123"})
    )
    setup = {
        "model": "models/gemini-2.5-flash-native-audio-latest",
        "generationConfig": {"responseModalities": ["AUDIO"]},
        "systemInstruction": {"parts": [{"text": "TAJNÝ SYSTÉM"}]},
        "tools": [{"googleSearch": {}}],
    }
    tok = await mint_ephemeral_token(setup=setup, settings=_SettingsWithKey())

    assert tok == "auth_tokens/ephemeral-abc123"
    assert route.called
    req = route.calls[0].request
    # The real key authenticates the MINT request (server→Google), not the browser.
    assert "key=test-key-XYZ" in str(req.url)
    body = json.loads(req.content)
    assert body["uses"] == 1
    assert body["expireTime"] and body["newSessionExpireTime"]
    # The full setup — model, system prompt, tools — is bound into the token.
    assert body["bidiGenerateContentSetup"] == setup


@pytest.mark.asyncio
@respx.mock
async def test_mint_ephemeral_token_raises_when_no_name_returned():
    respx.post(AUTH_TOKENS_ENDPOINT).mock(return_value=Response(200, json={}))
    with pytest.raises(RuntimeError, match="token"):
        await mint_ephemeral_token(setup={"model": "x"}, settings=_SettingsWithKey())


@pytest.mark.asyncio
@respx.mock
async def test_mint_ephemeral_token_raises_on_http_error():
    respx.post(AUTH_TOKENS_ENDPOINT).mock(
        return_value=Response(403, json={"error": {"message": "bad key"}})
    )
    with pytest.raises(RuntimeError):
        await mint_ephemeral_token(setup={"model": "x"}, settings=_SettingsWithKey())


@pytest.mark.asyncio
async def test_mint_ephemeral_token_requires_api_key():
    s = _Settings()
    s.gemini_api_key = None  # type: ignore[attr-defined]
    with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
        await mint_ephemeral_token(
            setup={"model": "x"},
            settings=s,
        )


class _SettingsForSummary(_Settings):
    gemini_api_key = "test-key"
    gemini_model = "gemini-2.5-flash-lite"


@pytest.mark.asyncio
@respx.mock
async def test_summarize_calls_generate_content_with_czech_prompt(tmp_path):
    import json as _j
    from pathlib import Path

    import aiosqlite

    from backend.app.migrations_runner import apply_migrations
    from backend.app.repositories.live_sessions import LiveSessionsRepo

    MIGRATIONS = Path(__file__).resolve().parents[2] / "backend" / "migrations"

    db = tmp_path / "t.db"
    async with aiosqlite.connect(db) as conn:
        await apply_migrations(conn, MIGRATIONS)
        repo = LiveSessionsRepo()
        await repo.insert_pending(conn, id="abc", clip_id=1, prompt_version=None)
        await repo.mark_active(conn, "abc")
        transcript = [
            {"role": "user", "text": "co to je za auto?", "ts": 1},
            {"role": "model", "text": "Vypadá to jako Škoda z 30. let.", "ts": 2},
        ]
        await repo.mark_ended(
            conn,
            "abc",
            end_reason="user_stop",
            transcript_json=_j.dumps(transcript, ensure_ascii=False),
        )

        route = respx.post(
            "https://generativelanguage.googleapis.com/v1beta/models/"
            "gemini-2.5-flash-lite:generateContent"
        ).mock(
            return_value=Response(
                200,
                json={"candidates": [{"content": {"parts": [{"text": "Krátké české shrnutí."}]}}]},
            )
        )

        ok = await summarize(conn, session_id="abc", settings=_SettingsForSummary())
        assert ok is True
        assert route.called
        body = route.calls[0].request.read().decode("utf-8")
        assert "česky" in body or "Shrň" in body or "shrň" in body
        s = await repo.get(conn, "abc")
        assert s.summary_cs == "Krátké české shrnutí."


@pytest.mark.asyncio
@respx.mock
async def test_summarize_is_idempotent(tmp_path):
    import json as _j
    from pathlib import Path

    import aiosqlite

    from backend.app.migrations_runner import apply_migrations
    from backend.app.repositories.live_sessions import LiveSessionsRepo

    MIGRATIONS = Path(__file__).resolve().parents[2] / "backend" / "migrations"

    db = tmp_path / "t.db"
    async with aiosqlite.connect(db) as conn:
        await apply_migrations(conn, MIGRATIONS)
        repo = LiveSessionsRepo()
        await repo.insert_pending(conn, id="abc", clip_id=1, prompt_version=None)
        await repo.mark_active(conn, "abc")
        await repo.mark_ended(
            conn,
            "abc",
            end_reason="user_stop",
            transcript_json=_j.dumps([{"role": "user", "text": "x", "ts": 1}]),
        )
        await repo.set_summary(conn, "abc", "Již existující shrnutí.")

        respx.post(
            "https://generativelanguage.googleapis.com/v1beta/models/"
            "gemini-2.5-flash-lite:generateContent"
        ).mock(
            return_value=Response(
                200,
                json={"candidates": [{"content": {"parts": [{"text": "new"}]}}]},
            )
        )

        ok = await summarize(conn, session_id="abc", settings=_SettingsForSummary())
        assert ok is False
        s = await repo.get(conn, "abc")
        assert s.summary_cs == "Již existující shrnutí."


@pytest.mark.asyncio
async def test_summarize_skips_when_transcript_empty(tmp_path):
    from pathlib import Path

    import aiosqlite

    from backend.app.migrations_runner import apply_migrations
    from backend.app.repositories.live_sessions import LiveSessionsRepo

    MIGRATIONS = Path(__file__).resolve().parents[2] / "backend" / "migrations"

    db = tmp_path / "t.db"
    async with aiosqlite.connect(db) as conn:
        await apply_migrations(conn, MIGRATIONS)
        repo = LiveSessionsRepo()
        await repo.insert_pending(conn, id="abc", clip_id=1, prompt_version=None)
        await repo.mark_active(conn, "abc")
        await repo.mark_ended(
            conn,
            "abc",
            end_reason="error",
            transcript_json="[]",
        )
        ok = await summarize(conn, session_id="abc", settings=_SettingsForSummary())
        assert ok is False
        s = await repo.get(conn, "abc")
        assert s.summary_cs is None
