"""Estimator: deterministic input per media kind; output distribution
with fallback chain; confidence labels; seeds when no history.

Uses a fake repo so no DB is needed — the repo contract is covered by
tests/integration/test_run_telemetry_repo.py.
"""

import pytest

from backend.app.services.run_estimator import (
    ClipEstimateInput,
    estimate_clips,
)


class FakeRepo:
    """recent_* return canned lists keyed by (media_kind, prompt_hash or '*')."""

    def __init__(self, input_ratios=None, output_rates=None):
        self.input_ratios = input_ratios or {}
        self.output_rates = output_rates or {}

    async def recent_input_ratios(self, conn, *, model, media_kind, limit=50):
        return self.input_ratios.get(media_kind, [])

    async def recent_output_rates(
        self, conn, *, model, media_kind, prompt_hash=None, limit=50
    ):
        return self.output_rates.get((media_kind, prompt_hash or "*"), [])


VIDEO = ClipEstimateInput(clip_id=1, media_kind="video+audio", duration_secs=60.0)
IMAGE = ClipEstimateInput(clip_id=2, media_kind="image", duration_secs=None)


@pytest.mark.asyncio
async def test_zero_history_uses_seeds_and_is_rough():
    est = await estimate_clips(
        None, FakeRepo(), [VIDEO],
        prompt_body="p" * 400, schema={"type": "object"},
        model="gemini-2.5-flash-lite",
    )
    # 60s * seed 300 tok/s = 18000 media tokens + prompt/schema chars/4 > 0
    assert est.tokens_in > 18000
    assert est.confidence == "rough"
    assert est.tokens_out_p90 >= est.tokens_out_p50 > 0


@pytest.mark.asyncio
async def test_input_calibration_overrides_seed():
    repo = FakeRepo(input_ratios={"video+audio": [250.0] * 10})
    est = await estimate_clips(
        None, repo, [VIDEO],
        prompt_body="", schema={}, model="m",
    )
    assert 60 * 250 <= est.tokens_in <= 60 * 250 + 50  # calibrated, +schema/prompt≈0


@pytest.mark.asyncio
async def test_prompt_level_history_wins_and_confidence_good():
    repo = FakeRepo(output_rates={
        ("video+audio", "HASH"): [10.0] * 12,   # level 1: 12 samples
        ("video+audio", "*"): [99.0] * 50,      # level 2 would say 99/s
    })
    est = await estimate_clips(
        None, repo, [VIDEO],
        prompt_body="body", schema={}, model="m", prompt_hash_override="HASH",
    )
    assert est.tokens_out_p50 == 600  # 60s * p50(10/s)
    assert est.confidence == "good"


@pytest.mark.asyncio
async def test_fallback_to_model_level_is_fair():
    repo = FakeRepo(output_rates={
        ("video+audio", "HASH"): [10.0],          # only 1 sample — below min 3
        ("video+audio", "*"): [20.0, 20.0, 20.0], # level 2 wins
    })
    est = await estimate_clips(
        None, repo, [VIDEO],
        prompt_body="body", schema={}, model="m", prompt_hash_override="HASH",
    )
    assert est.tokens_out_p50 == 1200
    assert est.confidence == "fair"


@pytest.mark.asyncio
async def test_image_unknown_dims_one_tile_and_per_item_output():
    repo = FakeRepo(output_rates={("image", "*"): [500.0, 600.0, 700.0]})
    est = await estimate_clips(
        None, repo, [IMAGE],
        prompt_body="", schema={}, model="m",
    )
    assert est.tokens_in >= 258           # 1 tile minimum
    assert est.tokens_out_p50 == 600      # per-item median


@pytest.mark.asyncio
async def test_unknown_model_cost_is_none_but_tokens_present():
    est = await estimate_clips(
        None, FakeRepo(), [VIDEO],
        prompt_body="", schema={}, model="no-such-model",
    )
    assert est.tokens_in > 0
    assert est.cost_usd_p50 is None and est.cost_usd_p90 is None
