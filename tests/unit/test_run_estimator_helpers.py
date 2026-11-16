"""Unit tests for the helpers estimate_clips is built from."""

from backend.app.services.run_estimator import (
    IMAGE_TILE_TOKENS,
    SEED_INPUT_TOKENS_PER_SEC,
    SEED_OUTPUT_TOKENS_PER_IMAGE,
    SEED_OUTPUT_TOKENS_PER_SEC,
    ClipEstimateInput,
    _clip_tokens,
    _confidence,
    _KindStats,
    _resolution_for_kind,
)

SEEDS = _KindStats(p50=None, p90=None, n=0, level=3)


def test_resolution_high_downgrades_to_medium_for_video_only():
    assert _resolution_for_kind("high", "video+audio") == "medium"
    assert _resolution_for_kind("high", "image") == "high"
    assert _resolution_for_kind(None, "video") is None


def test_clip_tokens_image_uses_tiles_and_seed_output():
    clip = ClipEstimateInput(clip_id=1, media_kind="image", duration_secs=None)
    media_in, audio_in, p50, p90 = _clip_tokens(clip, SEEDS)
    assert (media_in, audio_in) == (IMAGE_TILE_TOKENS, 0.0)
    assert (p50, p90) == (SEED_OUTPUT_TOKENS_PER_IMAGE, SEED_OUTPUT_TOKENS_PER_IMAGE * 2)


def test_clip_tokens_audio_counts_its_media_as_audio():
    clip = ClipEstimateInput(clip_id=2, media_kind="audio", duration_secs=10.0)
    stats = _KindStats(p50=5.0, p90=8.0, n=4, level=2, input_ratio=40.0)
    media_in, audio_in, p50, p90 = _clip_tokens(clip, stats)
    assert media_in == audio_in == 400.0
    assert (p50, p90) == (50.0, 80.0)


def test_clip_tokens_video_without_history_uses_seeds():
    clip = ClipEstimateInput(clip_id=3, media_kind="video", duration_secs=2.0)
    media_in, audio_in, p50, _p90 = _clip_tokens(clip, SEEDS)
    assert media_in == 2.0 * SEED_INPUT_TOKENS_PER_SEC["video"]
    assert audio_in == 0.0
    assert p50 == 2.0 * SEED_OUTPUT_TOKENS_PER_SEC


def test_confidence_reflects_weakest_kind():
    good = _KindStats(p50=1.0, p90=2.0, n=12, level=1)
    fair = _KindStats(p50=1.0, p90=2.0, n=4, level=2)
    assert _confidence({"video": good}) == ("good", 12)
    assert _confidence({"video": good, "audio": fair}) == ("fair", 4)
    assert _confidence({"video": good, "image": SEEDS}) == ("rough", 0)
