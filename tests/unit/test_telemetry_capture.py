"""extract_usage handles camelCase AND snake_case usageMetadata,
modality details, thinking/cached tokens; hashes are template-stable."""

from backend.app.services.telemetry_capture import (
    TokenUsage,
    extract_finish_reason,
    extract_usage,
    prompt_hash,
    schema_hash,
)

CAMEL = {
    "usageMetadata": {
        "promptTokenCount": 1000,
        "candidatesTokenCount": 200,
        "thoughtsTokenCount": 50,
        "cachedContentTokenCount": 10,
        "promptTokensDetails": [
            {"modality": "TEXT", "tokenCount": 100},
            {"modality": "VIDEO", "tokenCount": 800},
            {"modality": "AUDIO", "tokenCount": 100},
        ],
    },
    "candidates": [{"finishReason": "STOP"}],
}

SNAKE = {
    "usage_metadata": {
        "prompt_token_count": 1000,
        "candidates_token_count": 200,
        "thoughts_token_count": 50,
        "cached_content_token_count": 10,
        "prompt_tokens_details": [
            {"modality": "TEXT", "token_count": 100},
            {"modality": "IMAGE", "token_count": 900},
        ],
    },
    "candidates": [{"finish_reason": "MAX_TOKENS"}],
}


def test_extract_camel():
    u = extract_usage(CAMEL)
    assert u == TokenUsage(
        tokens_in=1000,
        tokens_in_text=100,
        tokens_in_video=800,
        tokens_in_audio=100,
        tokens_in_image=0,
        tokens_cached=10,
        tokens_out=200,
        tokens_thinking=50,
    )
    assert extract_finish_reason(CAMEL) == "STOP"


def test_extract_snake():
    u = extract_usage(SNAKE)
    assert u.tokens_in == 1000
    assert u.tokens_in_image == 900
    assert u.tokens_thinking == 50
    assert extract_finish_reason(SNAKE) == "MAX_TOKENS"


def test_extract_missing_usage_is_zeros():
    u = extract_usage({})
    assert u.tokens_in == 0 and u.tokens_out == 0 and u.tokens_thinking == 0
    assert extract_finish_reason({}) is None


def test_billable_out():
    assert extract_usage(CAMEL).billable_out == 250


def test_prompt_hash_is_template_stable():
    # Same template → same hash regardless of how it gets rendered later.
    h1 = prompt_hash("describe scenes")
    h2 = prompt_hash("describe scenes")
    assert h1 == h2 and len(h1) == 64
    assert prompt_hash("describe scenes!") != h1


def test_schema_hash_key_order_insensitive():
    assert schema_hash({"a": 1, "b": 2}) == schema_hash({"b": 2, "a": 1})


def test_camel_wins_when_both_key_forms_present():
    raw = {
        "usageMetadata": {"promptTokenCount": 42},
        "usage_metadata": {"prompt_token_count": 99},
    }
    assert extract_usage(raw).tokens_in == 42


def test_finish_reason_malformed_candidate_is_none():
    assert extract_finish_reason({"candidates": [None]}) is None
