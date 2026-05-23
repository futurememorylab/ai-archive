import pytest
import respx
from httpx import Response

from backend.app.services.live_sessions import (
    assemble_setup_payload,
    mint_ephemeral_token,
    summarize,
)


class _Settings:
    gemini_live_model = "gemini-2.5-flash-preview-native-audio-dialog"
    gemini_live_voice = "Aoede"


def _clip():
    return dict(
        id=42, name="P1010001", format="9,5 mm", fps=25,
        duration_secs=120.0, duration_smpte="00:02:00:00",
        notes="rodinný výlet", big_notes="",
        markers=[], fields={"pragafilm.dekáda.natočení": "20.léta"},
    )


def _draft():
    return dict(markers=[], fields={}, notes="myslím, že je to Praha")


def test_setup_payload_top_level_model_and_generation_config():
    # BidiGenerateContentSetup is FLAT: model + generationConfig + tools +
    # systemInstruction + transcription configs sit at the same level. Only
    # responseModalities/speechConfig nest inside generationConfig.
    p = assemble_setup_payload(
        clip=_clip(), draft=_draft(),
        prompt_body="SYSTÉM INSTRUKCE",
        settings=_Settings(),
    )
    assert p["model"] == "models/gemini-2.5-flash-preview-native-audio-dialog"
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
        clip=_clip(), draft=_draft(),
        prompt_body="MŮJ ČESKÝ SYSTÉM",
        settings=_Settings(),
    )
    parts = p["systemInstruction"]["parts"]
    assert parts == [{"text": "MŮJ ČESKÝ SYSTÉM"}]


def test_setup_payload_declares_google_search_and_end_session_tools():
    p = assemble_setup_payload(
        clip=_clip(), draft=_draft(),
        prompt_body="x", settings=_Settings(),
    )
    tools = p["tools"]
    assert {"googleSearch": {}} in tools
    fd = next(t for t in tools if "functionDeclarations" in t)["functionDeclarations"]
    assert any(d["name"] == "end_session" for d in fd)
    end = next(d for d in fd if d["name"] == "end_session")
    assert end["parameters"]["required"] == ["reason"]


def test_setup_payload_initial_context_turn_has_text_part():
    p = assemble_setup_payload(
        clip=_clip(), draft=_draft(),
        prompt_body="x", settings=_Settings(),
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


@pytest.mark.asyncio
@respx.mock
async def test_mint_ephemeral_token_posts_to_auth_tokens_create_endpoint():
    route = respx.post(
        "https://generativelanguage.googleapis.com/v1alpha/auth_tokens"
    ).mock(return_value=Response(200, json={"name": "auth_tokens/abc123"}))
    setup = {
        "model": "models/x",
        "config": {"responseModalities": ["AUDIO"]},
        "initial_context_turn": {"role": "user", "parts": [{"text": "hi"}]},
    }
    tok = await mint_ephemeral_token(setup=setup, settings=_SettingsWithKey())
    # mint_ephemeral_token strips the "auth_tokens/" resource prefix so the
    # returned value is the bare token suitable for `?key=` in the WSS URL.
    assert tok == "abc123"
    assert route.called
    sent = route.calls[0].request
    assert sent.url.params["key"] == "test-key-XYZ"
    import json as _j
    body = _j.loads(sent.content)
    assert body["uses"] == 1
    assert "expireTime" in body
    bidi = body["bidiGenerateContentSetup"]
    assert "initial_context_turn" not in bidi
    assert bidi["model"] == "models/x"


@pytest.mark.asyncio
@respx.mock
async def test_mint_ephemeral_token_raises_on_non_200():
    respx.post(
        "https://generativelanguage.googleapis.com/v1alpha/auth_tokens"
    ).mock(return_value=Response(403, json={"error": {"message": "Forbidden"}}))
    with pytest.raises(RuntimeError, match="auth_tokens"):
        await mint_ephemeral_token(
            setup={"model": "x", "config": {}, "initial_context_turn": {}},
            settings=_SettingsWithKey(),
        )


@pytest.mark.asyncio
async def test_mint_ephemeral_token_requires_api_key():
    s = _Settings()
    s.gemini_api_key = None  # type: ignore[attr-defined]
    with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
        await mint_ephemeral_token(
            setup={"model": "x", "config": {}, "initial_context_turn": {}},
            settings=s,
        )


class _SettingsForSummary(_Settings):
    gemini_api_key = "test-key"
    gemini_model = "gemini-2.5-flash-lite"


@pytest.mark.asyncio
@respx.mock
async def test_summarize_calls_generate_content_with_czech_prompt(tmp_path):
    import json as _j

    import aiosqlite
    from pathlib import Path
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
            conn, "abc", end_reason="user_stop",
            transcript_json=_j.dumps(transcript, ensure_ascii=False),
        )

        route = respx.post(
            "https://generativelanguage.googleapis.com/v1beta/models/"
            "gemini-2.5-flash-lite:generateContent"
        ).mock(return_value=Response(200, json={
            "candidates": [{"content": {"parts": [{"text": "Krátké české shrnutí."}]}}]
        }))

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

    import aiosqlite
    from pathlib import Path
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
            conn, "abc", end_reason="user_stop",
            transcript_json=_j.dumps([{"role": "user", "text": "x", "ts": 1}]),
        )
        await repo.set_summary(conn, "abc", "Již existující shrnutí.")

        respx.post(
            "https://generativelanguage.googleapis.com/v1beta/models/"
            "gemini-2.5-flash-lite:generateContent"
        ).mock(return_value=Response(
            200,
            json={"candidates": [{"content": {"parts": [{"text": "new"}]}}]},
        ))

        ok = await summarize(conn, session_id="abc", settings=_SettingsForSummary())
        assert ok is False
        s = await repo.get(conn, "abc")
        assert s.summary_cs == "Již existující shrnutí."


@pytest.mark.asyncio
async def test_summarize_skips_when_transcript_empty(tmp_path):
    import aiosqlite
    from pathlib import Path
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
            conn, "abc", end_reason="error", transcript_json="[]",
        )
        ok = await summarize(conn, session_id="abc", settings=_SettingsForSummary())
        assert ok is False
        s = await repo.get(conn, "abc")
        assert s.summary_cs is None
