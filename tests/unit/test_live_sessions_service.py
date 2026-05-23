from backend.app.services.live_sessions import assemble_setup_payload


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


def test_setup_payload_top_level_model_and_config():
    p = assemble_setup_payload(
        clip=_clip(), draft=_draft(),
        prompt_body="SYSTÉM INSTRUKCE",
        settings=_Settings(),
    )
    assert p["model"] == "models/gemini-2.5-flash-preview-native-audio-dialog"
    cfg = p["config"]
    assert cfg["responseModalities"] == ["AUDIO"]
    assert cfg["speechConfig"]["languageCode"] == "cs-CZ"
    assert cfg["speechConfig"]["voiceConfig"]["prebuiltVoiceConfig"]["voiceName"] == "Aoede"
    assert cfg["outputAudioTranscription"] == {}
    assert cfg["inputAudioTranscription"] == {}


def test_setup_payload_has_system_instruction_text():
    p = assemble_setup_payload(
        clip=_clip(), draft=_draft(),
        prompt_body="MŮJ ČESKÝ SYSTÉM",
        settings=_Settings(),
    )
    parts = p["config"]["systemInstruction"]["parts"]
    assert parts == [{"text": "MŮJ ČESKÝ SYSTÉM"}]


def test_setup_payload_declares_google_search_and_end_session_tools():
    p = assemble_setup_payload(
        clip=_clip(), draft=_draft(),
        prompt_body="x", settings=_Settings(),
    )
    tools = p["config"]["tools"]
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
