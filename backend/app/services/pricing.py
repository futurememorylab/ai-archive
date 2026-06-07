"""Vertex Gemini rate card + cost computation.

Rates are per **1M tokens**, split the way Gemini bills: text/image/
video input share one rate, audio input is higher, cached input is
discounted, and output (candidates + thinking) is one rate. Rates ship
with app releases; tokens are always stored alongside cost so history
is recomputable when the card was stale.

UPDATE RATES from https://cloud.google.com/vertex-ai/generative-ai/pricing
and bump PRICING_VERSION whenever they change.

Rates last verified: 2026-06-07 from
https://cloud.google.com/vertex-ai/generative-ai/pricing

NOTE: Gemini 2.5 Pro has tiered pricing (≤200K vs >200K input tokens).
We use the ≤200K (standard) rate here — the vast majority of annotation
runs stay well within that threshold. If a run exceeds 200K tokens the
cost estimate will be lower than the actual bill; tokens are stored so
the cost can be recomputed with the correct tier when needed.
"""

import logging
from dataclasses import dataclass

from backend.app.services.telemetry_capture import TokenUsage

log = logging.getLogger(__name__)

PRICING_VERSION = "2026-06"


@dataclass(frozen=True)
class RateCard:
    input_text_video_image_per_1m: float
    input_audio_per_1m: float
    input_cached_per_1m: float
    output_per_1m: float
    source_url: str  # provenance: where this rate was read (spec §5 audit trail)


# Rates verified 2026-06-07 from:
# https://cloud.google.com/vertex-ai/generative-ai/pricing
RATE_CARDS: dict[str, RateCard] = {
    "gemini-2.5-flash-lite": RateCard(
        input_text_video_image_per_1m=0.10,
        input_audio_per_1m=0.30,
        input_cached_per_1m=0.01,
        output_per_1m=0.40,
        source_url="https://cloud.google.com/vertex-ai/generative-ai/pricing",
    ),
    "gemini-2.5-flash": RateCard(
        input_text_video_image_per_1m=0.30,
        input_audio_per_1m=1.00,
        input_cached_per_1m=0.03,
        output_per_1m=2.50,
        source_url="https://cloud.google.com/vertex-ai/generative-ai/pricing",
    ),
    "gemini-2.5-pro": RateCard(
        # Tiered: ≤200K tokens=$1.25, >200K=$2.50; using ≤200K rate.
        input_text_video_image_per_1m=1.25,
        input_audio_per_1m=1.25,
        input_cached_per_1m=0.13,
        output_per_1m=10.00,
        source_url="https://cloud.google.com/vertex-ai/generative-ai/pricing",
    ),
}


def compute_cost(
    usage: TokenUsage, model: str, *, card: RateCard | None = None
) -> tuple[float | None, str]:
    """Cost in USD for one call, or (None, version) when the model is
    not in the card. Never raises — a missing rate must not fail a run."""
    if card is None:
        card = RATE_CARDS.get(model)
    if card is None:
        log.warning("pricing: no rate card for model %r; cost_usd=NULL", model)
        return None, PRICING_VERSION

    audio = usage.tokens_in_audio
    detailed = (
        usage.tokens_in_text + usage.tokens_in_video
        + usage.tokens_in_image + audio
    )
    # Modality detail can be absent or partial (older responses, future
    # modalities). Bill against whichever is larger: the detailed sum or
    # the authoritative total — never drop tokens.
    non_audio = max(detailed, usage.tokens_in) - audio
    # Known limitation: usageMetadata doesn't break cached tokens down by
    # modality, so cached audio is conservatively billed at the full audio
    # rate (slight overestimate — the safe direction for cost quotes).
    cached = min(usage.tokens_cached, non_audio)
    fresh_non_audio = non_audio - cached

    cost = (
        fresh_non_audio * card.input_text_video_image_per_1m
        + audio * card.input_audio_per_1m
        + cached * card.input_cached_per_1m
        + usage.billable_out * card.output_per_1m
    ) / 1_000_000
    return cost, PRICING_VERSION
