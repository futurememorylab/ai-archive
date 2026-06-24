"""The active rate-card cache: defaults to the seed, swappable, used by compute_cost."""

import pytest

from backend.app.services import pricing
from backend.app.services.pricing import RateCard, compute_cost, rate_cards, set_rate_cards
from backend.app.services.telemetry_capture import TokenUsage


@pytest.fixture(autouse=True)
def _reset_cards():
    # Tests mutate a process-global cache; restore the seed afterwards.
    yield
    set_rate_cards(pricing.SEED_RATE_CARDS)


def test_cache_defaults_to_seed():
    assert "gemini-2.5-flash-lite" in rate_cards()


def test_set_rate_cards_replaces_active_lookup():
    set_rate_cards(
        {
            "only-model": RateCard(
                input_text_video_image_per_1m=1.0,
                input_audio_per_1m=1.0,
                input_cached_per_1m=1.0,
                output_per_1m=1.0,
                source_url="x",
            )
        }
    )
    assert "gemini-2.5-flash-lite" not in rate_cards()
    cost, _ = compute_cost(TokenUsage(tokens_in=1_000_000), "only-model")
    assert cost == pytest.approx(1.0)
    none_cost, _ = compute_cost(TokenUsage(tokens_in=1000), "gemini-2.5-flash-lite")
    assert none_cost is None
