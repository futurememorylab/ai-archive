"""Guard: the seeded rate cards match the values shipped at PR1 time.

If you intentionally change a rate, update BOTH SEED_RATE_CARDS and this
pin in the same commit (and bump PRICING_VERSION). The pin is the
deliberate-change checkpoint — see the cost-prediction spec §6.
"""

from backend.app.services.pricing import SEED_RATE_CARDS

EXPECTED = {
    # ── Gemini 2.5 series ────────────────────────────────────────────────────
    "gemini-2.5-flash-lite": (0.10, 0.30, 0.01, 0.40),
    "gemini-2.5-flash": (0.30, 1.00, 0.03, 2.50),
    "gemini-2.5-pro": (1.25, 1.25, 0.13, 10.00),
    # ── Gemini 3.x / 3.5 series (Global standard, all per 1M tokens) ────────
    "gemini-3-flash-preview": (0.50, 1.00, 0.05, 3.00),
    "gemini-3.1-pro-preview": (2.00, 2.00, 0.20, 12.00),
    "gemini-3.1-flash-lite": (0.25, 0.50, 0.025, 1.50),
    "gemini-3.1-flash-lite-preview": (0.25, 0.50, 0.025, 1.50),
    "gemini-3.5-flash": (1.50, 1.50, 0.15, 9.00),
}


def test_seed_rate_cards_match_pin():
    actual = {
        m: (
            c.input_text_video_image_per_1m,
            c.input_audio_per_1m,
            c.input_cached_per_1m,
            c.output_per_1m,
        )
        for m, c in SEED_RATE_CARDS.items()
    }
    assert actual == EXPECTED
