from backend.app.archive.ai_store import AIInputStore


def test_ai_input_store_protocol_exposes_expected_names():
    expected = {
        "id",
        "capabilities",
        "ensure_uploaded",
        "status",
        "evict",
        "health",
        "reference_for_gemini",
    }
    assert expected.issubset(set(dir(AIInputStore)))
