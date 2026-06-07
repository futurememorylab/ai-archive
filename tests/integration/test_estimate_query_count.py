"""estimate_for_clip_ids: DB-first (offline-safe), query count does not
scale with clip count (ADR 0046)."""

import pytest

from backend.app.repositories.clip_cache import ClipCacheRepo
from backend.app.repositories.prompts import PromptsRepo
from backend.app.repositories.run_telemetry import RunTelemetryRepo
from backend.app.services.run_estimator import estimate_for_clip_ids
from tests._helpers.query_count import assert_query_count
from tests.integration.test_clip_cache_get_many import _seed


@pytest.mark.asyncio
async def test_estimate_for_clip_ids_smoke_and_query_count(db):
    prompts = PromptsRepo()
    _, vid = await prompts.create_with_initial_version(
        db, name="t", description=None, body="describe",
        target_map={"scenes": {"kind": "markers"}},
        output_schema={"type": "object"}, model="gemini-2.5-flash-lite",
    )
    cache = ClipCacheRepo()
    for cid in range(1, 101):
        await _seed(db, cache, cid)

    result_small = await estimate_for_clip_ids(
        db, clip_cache_repo=cache, run_telemetry_repo=RunTelemetryRepo(),
        prompts_repo=prompts, provider_id="catdv",
        clip_ids=list(range(1, 11)), prompt_version_id=vid,
    )
    assert result_small["tokens_in"] > 0
    assert result_small["confidence"] == "rough"
    assert result_small["n_clips"] == 10

    # Query count must be the same for 10 and 100 clips (one cache chunk,
    # one prompt read, ≤3 aggregate reads per media-kind group).
    async with assert_query_count(db, 8):
        await estimate_for_clip_ids(
            db, clip_cache_repo=cache, run_telemetry_repo=RunTelemetryRepo(),
            prompts_repo=prompts, provider_id="catdv",
            clip_ids=list(range(1, 11)), prompt_version_id=vid,
        )
    async with assert_query_count(db, 8):
        await estimate_for_clip_ids(
            db, clip_cache_repo=cache, run_telemetry_repo=RunTelemetryRepo(),
            prompts_repo=prompts, provider_id="catdv",
            clip_ids=list(range(1, 101)), prompt_version_id=vid,
        )


@pytest.mark.asyncio
async def test_uncached_clips_estimated_as_unknown(db):
    prompts = PromptsRepo()
    _, vid = await prompts.create_with_initial_version(
        db, name="t2", description=None, body="describe",
        target_map={"scenes": {"kind": "markers"}},
        output_schema={"type": "object"}, model="gemini-2.5-flash-lite",
    )
    result = await estimate_for_clip_ids(
        db, clip_cache_repo=ClipCacheRepo(),
        run_telemetry_repo=RunTelemetryRepo(), prompts_repo=prompts,
        provider_id="catdv", clip_ids=[777], prompt_version_id=vid,
    )
    # Unknown clip → conservative defaults, never an exception.
    assert result["n_clips"] == 1
    assert result["confidence"] == "rough"
