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
    """recent_* return canned lists keyed on resolution.

    input_ratios keyed by (media_kind, media_resolution); output_rates keyed
    by (media_kind, prompt_hash or '*', media_resolution).
    """

    def __init__(self, input_ratios=None, output_rates=None):
        self.input_ratios = input_ratios or {}
        self.output_rates = output_rates or {}

    async def recent_input_ratios(
        self, conn, *, model, media_kind, media_resolution=None, limit=50
    ):
        return self.input_ratios.get((media_kind, media_resolution), [])

    async def recent_output_rates(
        self, conn, *, model, media_kind, prompt_hash=None, media_resolution=None, limit=50
    ):
        return self.output_rates.get((media_kind, prompt_hash or "*", media_resolution), [])


VIDEO = ClipEstimateInput(clip_id=1, media_kind="video+audio", duration_secs=60.0)
IMAGE = ClipEstimateInput(clip_id=2, media_kind="image", duration_secs=None)


@pytest.mark.asyncio
async def test_zero_history_uses_seeds_and_is_rough():
    est = await estimate_clips(
        None,
        FakeRepo(),
        [VIDEO],
        prompt_body="p" * 400,
        schema={"type": "object"},
        model="gemini-2.5-flash-lite",
    )
    # 60s * seed 300 tok/s = 18000 media tokens + prompt/schema chars/4 > 0
    assert est.tokens_in > 18000
    assert est.confidence == "rough"
    assert est.tokens_out_p90 >= est.tokens_out_p50 > 0


@pytest.mark.asyncio
async def test_input_calibration_overrides_seed():
    repo = FakeRepo(input_ratios={("video+audio", None): [250.0] * 10})
    est = await estimate_clips(
        None,
        repo,
        [VIDEO],
        prompt_body="",
        schema={},
        model="m",
    )
    assert 60 * 250 <= est.tokens_in <= 60 * 250 + 50  # calibrated, +schema/prompt≈0


@pytest.mark.asyncio
async def test_prompt_level_history_wins_and_confidence_good():
    repo = FakeRepo(
        output_rates={
            ("video+audio", "HASH", None): [10.0] * 12,  # level 1: 12 samples
            ("video+audio", "*", None): [99.0] * 50,  # level 2 would say 99/s
        }
    )
    est = await estimate_clips(
        None,
        repo,
        [VIDEO],
        prompt_body="body",
        schema={},
        model="m",
        prompt_hash_override="HASH",
    )
    assert est.tokens_out_p50 == 600  # 60s * p50(10/s)
    assert est.confidence == "good"


@pytest.mark.asyncio
async def test_fallback_to_model_level_is_fair():
    repo = FakeRepo(
        output_rates={
            ("video+audio", "HASH", None): [10.0],  # only 1 sample — below min 3
            ("video+audio", "*", None): [20.0, 20.0, 20.0],  # level 2 wins
        }
    )
    est = await estimate_clips(
        None,
        repo,
        [VIDEO],
        prompt_body="body",
        schema={},
        model="m",
        prompt_hash_override="HASH",
    )
    assert est.tokens_out_p50 == 1200
    assert est.confidence == "fair"


@pytest.mark.asyncio
async def test_image_unknown_dims_one_tile_and_per_item_output():
    repo = FakeRepo(output_rates={("image", "*", None): [500.0, 600.0, 700.0]})
    est = await estimate_clips(
        None,
        repo,
        [IMAGE],
        prompt_body="",
        schema={},
        model="m",
    )
    assert est.tokens_in >= 258  # 1 tile minimum
    assert est.tokens_out_p50 == 600  # per-item median


@pytest.mark.asyncio
async def test_unknown_model_cost_is_none_but_tokens_present():
    est = await estimate_clips(
        None,
        FakeRepo(),
        [VIDEO],
        prompt_body="",
        schema={},
        model="no-such-model",
    )
    assert est.tokens_in > 0
    assert est.cost_usd_p50 is None and est.cost_usd_p90 is None


@pytest.mark.asyncio
async def test_empty_clips_returns_zeroed_estimate():
    est = await estimate_clips(
        None,
        FakeRepo(),
        [],
        prompt_body="x",
        schema={},
        model="gemini-2.5-flash-lite",
    )
    assert est.n_clips == 0 and est.tokens_in == 0
    assert est.tokens_out_p50 == 0 and est.confidence == "rough"


@pytest.mark.asyncio
async def test_audio_clip_calibrated_below_seed_cost_not_negative():
    # Calibrated audio input ratio (20/s) below the 32/s seed must not
    # produce a negative video bucket in the cost split.
    repo = FakeRepo(input_ratios={("audio", None): [20.0] * 5})
    audio = ClipEstimateInput(clip_id=3, media_kind="audio", duration_secs=60.0)
    est = await estimate_clips(
        None,
        repo,
        [audio],
        prompt_body="",
        schema={},
        model="gemini-2.5-flash-lite",
    )
    assert est.tokens_in == 1200  # 60s * calibrated 20/s
    assert est.cost_usd_p50 is not None and est.cost_usd_p50 > 0


@pytest.mark.asyncio
async def test_mixed_kinds_confidence_uses_weakest_kind():
    repo = FakeRepo(
        output_rates={
            ("video+audio", "*", None): [10.0] * 50,  # strong history
            # image: no history at all → level 3
        }
    )
    est = await estimate_clips(
        None,
        repo,
        [VIDEO, IMAGE],
        prompt_body="",
        schema={},
        model="m",
    )
    assert est.confidence == "rough"  # weakest kind dominates


@pytest.mark.asyncio
async def test_p90_exceeds_p50_on_skewed_history():
    repo = FakeRepo(
        output_rates={
            ("video+audio", "*", None): [1.0, 1.0, 1.0, 10.0, 10.0, 10.0, 10.0, 100.0, 100.0, 100.0],
        }
    )
    est = await estimate_clips(
        None,
        repo,
        [VIDEO],
        prompt_body="",
        schema={},
        model="m",
    )
    assert est.tokens_out_p90 > est.tokens_out_p50


@pytest.mark.asyncio
async def test_estimate_uses_resolution_keyed_history():
    # Uses low vs medium (both valid for video, neither downgraded) so this
    # asserts the resolution-keying alone, independent of the HIGH→medium
    # per-kind downgrade (covered by the dedicated tests below).
    repo = FakeRepo(output_rates={("video+audio", "*", "low"): [5.0] * 5,
                                  ("video+audio", "*", "medium"): [50.0] * 5})
    low = await estimate_clips(None, repo, [VIDEO], prompt_body="", schema={}, model="m", media_resolution="low")
    medium = await estimate_clips(None, repo, [VIDEO], prompt_body="", schema={}, model="m", media_resolution="medium")
    assert low.tokens_out_p50 == 300    # 60s * 5/s
    assert medium.tokens_out_p50 == 3000  # 60s * 50/s


@pytest.mark.asyncio
async def test_resolution_with_no_history_falls_back_to_seeds_rough():
    repo = FakeRepo(output_rates={("video+audio", "*", "high"): [50.0] * 5})
    est = await estimate_clips(None, repo, [VIDEO], prompt_body="", schema={}, model="m", media_resolution="low")
    assert est.confidence == "rough"  # no 'low' history → seeds


@pytest.mark.asyncio
async def test_high_resolution_downgraded_to_medium_for_non_image_clip():
    """L2: a video clip under a HIGH-resolution prompt RUNS at medium (HIGH is
    image-only), so the estimate must read MEDIUM-keyed history, not HIGH."""
    repo = FakeRepo(
        output_rates={
            ("video+audio", "*", "high"): [50.0] * 5,  # must NOT be selected
            ("video+audio", "*", "medium"): [10.0] * 5,  # downgraded → selected
        }
    )
    est = await estimate_clips(
        None, repo, [VIDEO], prompt_body="", schema={}, model="m", media_resolution="high"
    )
    # 60s * medium 10/s = 600 (not 60 * 50 = 3000 had it stayed at high)
    assert est.tokens_out_p50 == 600


@pytest.mark.asyncio
async def test_high_resolution_kept_for_image_clip():
    """L2 counterpart: an image clip under HIGH stays HIGH (HIGH is image-only)."""
    repo = FakeRepo(
        output_rates={
            ("image", "*", "high"): [500.0, 600.0, 700.0],  # selected for image
            ("image", "*", "medium"): [10.0] * 5,  # must NOT be selected
        }
    )
    est = await estimate_clips(
        None, repo, [IMAGE], prompt_body="", schema={}, model="m", media_resolution="high"
    )
    assert est.tokens_out_p50 == 600  # per-item median from HIGH history


@pytest.mark.asyncio
async def test_high_resolution_per_kind_in_mixed_batch():
    """Mixed batch: the image keeps HIGH history, the video downgrades to MEDIUM
    — proving the downgrade is per kind, not a single batch-wide decision."""
    repo = FakeRepo(
        output_rates={
            ("image", "*", "high"): [400.0, 400.0, 400.0],
            ("video+audio", "*", "medium"): [10.0] * 5,
            ("video+audio", "*", "high"): [50.0] * 5,  # must NOT be selected
        }
    )
    est = await estimate_clips(
        None, repo, [VIDEO, IMAGE], prompt_body="", schema={}, model="m", media_resolution="high"
    )
    # image: 400 (from high history); video: 60s * 10/s = 600 (from medium history)
    assert est.tokens_out_p50 == 400 + 600
