"""compute_cost math against an injected rate card; unknown model → None."""

import pytest

from backend.app.services.pricing import (
    PRICING_VERSION,
    RateCard,
    compute_cost,
)
from backend.app.services.telemetry_capture import TokenUsage

CARD = RateCard(
    input_text_video_image_per_1m=0.10,
    input_audio_per_1m=0.30,
    input_cached_per_1m=0.025,
    output_per_1m=0.40,
    source_url="https://example.test/pricing",
)


def test_cost_math_modality_split():
    usage = TokenUsage(
        tokens_in=1_000_000, tokens_in_text=100_000, tokens_in_video=800_000,
        tokens_in_audio=100_000, tokens_cached=0,
        tokens_out=100_000, tokens_thinking=100_000,
    )
    cost, version = compute_cost(usage, "any-model", card=CARD)
    # (100k + 800k) * 0.10/1M + 100k * 0.30/1M + 200k * 0.40/1M
    assert cost == pytest.approx(0.09 + 0.03 + 0.08)
    assert version == PRICING_VERSION


def test_cached_tokens_billed_at_cached_rate():
    usage = TokenUsage(
        tokens_in=1_000_000, tokens_in_text=1_000_000, tokens_cached=400_000,
    )
    cost, _ = compute_cost(usage, "any-model", card=CARD)
    # 600k fresh text at 0.10 + 400k cached at 0.025
    assert cost == pytest.approx(0.06 + 0.01)


def test_no_modality_detail_falls_back_to_total():
    usage = TokenUsage(tokens_in=1_000_000, tokens_out=0)
    cost, _ = compute_cost(usage, "any-model", card=CARD)
    assert cost == pytest.approx(0.10)


def test_unknown_model_returns_none():
    usage = TokenUsage(tokens_in=1000)
    cost, version = compute_cost(usage, "model-that-does-not-exist")
    assert cost is None
    assert version == PRICING_VERSION
