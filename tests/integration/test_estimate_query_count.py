"""estimate_for_clip_ids: DB-first (offline-safe), query count does not
scale with clip count (ADR 0046)."""

import importlib
from datetime import UTC, datetime

import pytest

from backend.app.archive.model import CanonicalClip, MediaRef
from backend.app.repositories.clip_cache import ClipCacheRepo
from backend.app.repositories.model_config import ModelConfigRepo
from backend.app.repositories.prompts import PromptsRepo
from backend.app.repositories.run_telemetry import RunTelemetryRepo
from backend.app.services.run_estimator import IMAGE_TILE_TOKENS, estimate_for_clip_ids
from tests._helpers.query_count import assert_query_count
from tests.integration.test_clip_cache_get_many import _seed


@pytest.mark.asyncio
async def test_estimate_for_clip_ids_smoke_and_query_count(db):
    prompts = PromptsRepo()
    _, vid = await prompts.create_with_initial_version(
        db,
        name="t",
        description=None,
        body="describe",
        target_map={"scenes": {"kind": "markers"}},
        output_schema={"type": "object"},
        model="gemini-2.5-flash-lite",
    )
    cache = ClipCacheRepo()
    for cid in range(1, 101):
        await _seed(db, cache, cid)

    result_small = await estimate_for_clip_ids(
        db,
        clip_cache_repo=cache,
        run_telemetry_repo=RunTelemetryRepo(),
        prompts_repo=prompts,
        model_config_repo=ModelConfigRepo(),
        provider_id="catdv",
        clip_ids=list(range(1, 11)),
        prompt_version_id=vid,
    )
    assert result_small["tokens_in"] > 0
    assert result_small["confidence"] == "rough"
    assert result_small["n_clips"] == 10
    assert result_small["n_unknown"] == 0

    # Query count must be the same for 10 and 100 clips.
    # Breakdown: 1 prompt version read + 1 model_config default-resolution
    # read + 1 clip_cache chunk read + 1 input_ratio + 2 output_rates
    # (prompt-hash miss then model-only) = 6 queries, regardless of clip count.
    async with assert_query_count(db, 6):
        await estimate_for_clip_ids(
            db,
            clip_cache_repo=cache,
            run_telemetry_repo=RunTelemetryRepo(),
            prompts_repo=prompts,
            model_config_repo=ModelConfigRepo(),
            provider_id="catdv",
            clip_ids=list(range(1, 11)),
            prompt_version_id=vid,
        )
    async with assert_query_count(db, 6):
        await estimate_for_clip_ids(
            db,
            clip_cache_repo=cache,
            run_telemetry_repo=RunTelemetryRepo(),
            prompts_repo=prompts,
            model_config_repo=ModelConfigRepo(),
            provider_id="catdv",
            clip_ids=list(range(1, 101)),
            prompt_version_id=vid,
        )


@pytest.mark.asyncio
async def test_uncached_clips_estimated_as_unknown(db):
    prompts = PromptsRepo()
    _, vid = await prompts.create_with_initial_version(
        db,
        name="t2",
        description=None,
        body="describe",
        target_map={"scenes": {"kind": "markers"}},
        output_schema={"type": "object"},
        model="gemini-2.5-flash-lite",
    )
    result = await estimate_for_clip_ids(
        db,
        clip_cache_repo=ClipCacheRepo(),
        run_telemetry_repo=RunTelemetryRepo(),
        prompts_repo=prompts,
        model_config_repo=ModelConfigRepo(),
        provider_id="catdv",
        clip_ids=[777],
        prompt_version_id=vid,
    )
    # Unknown clip → conservative defaults, never an exception.
    assert result["n_clips"] == 1
    assert result["confidence"] == "rough"
    assert result["n_unknown"] == 1


@pytest.mark.asyncio
async def test_estimate_pricing_missing_flag(db):
    # Model with NO rate card → costs are None but tokens are still computed.
    # The returned dict must flag pricing_missing=True so UIs can distinguish
    # "cost unknown" from "free".
    prompts = PromptsRepo()
    _, vid = await prompts.create_with_initial_version(
        db,
        name="t3",
        description=None,
        body="describe",
        target_map={"scenes": {"kind": "markers"}},
        output_schema={"type": "object"},
        model="no-card-model",  # absent from SEED_RATE_CARDS → pricing missing
    )
    cache = ClipCacheRepo()
    from tests.integration.test_clip_cache_get_many import _seed as _seed_clip

    await _seed_clip(db, cache, 1)
    result = await estimate_for_clip_ids(
        db,
        clip_cache_repo=cache,
        run_telemetry_repo=RunTelemetryRepo(),
        prompts_repo=prompts,
        model_config_repo=ModelConfigRepo(),
        provider_id="catdv",
        clip_ids=[1],
        prompt_version_id=vid,
    )
    assert result["pricing_missing"] is True
    assert result["cost_usd_p50"] is None
    assert result["tokens_in"] > 0


def _setenv(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("CATDV_BASE_URL", "http://localhost:0")
    monkeypatch.setenv("CATDV_USERNAME", "")
    monkeypatch.setenv("CATDV_PASSWORD", "p")
    monkeypatch.setenv("CATDV_CATALOG_ID", "881507")
    monkeypatch.setenv("GCP_PROJECT_ID", "p")
    monkeypatch.setenv("GCS_BUCKET_NAME", "b")
    monkeypatch.setenv("PROXY_SOURCE", "rest")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))


import asyncio as _asyncio  # noqa: E402


def _run(coro):
    loop = _asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def test_estimate_route_happy_path_and_404(monkeypatch, tmp_path):
    _setenv(monkeypatch, tmp_path)
    import backend.app.main as main_mod

    importlib.reload(main_mod)
    from fastapi.testclient import TestClient

    app = main_mod.app
    with TestClient(app) as client:
        ctx = client.app.state.core_ctx
        _, vid = _run(
            ctx.prompts_repo.create_with_initial_version(
                ctx.db,
                name="est-test",
                description=None,
                body="describe",
                target_map={"scenes": {"kind": "markers"}},
                output_schema={"type": "object"},
                model="gemini-2.5-flash-lite",
            )
        )
        # 200 happy path — empty clip list, valid prompt version
        r = client.post(
            "/api/jobs/estimate",
            json={"prompt_version_id": vid, "clip_ids": []},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["n_clips"] == 0
        assert body["n_unknown"] == 0
        assert "tokens_in" in body

        # 404 when prompt version does not exist
        r = client.post(
            "/api/jobs/estimate",
            json={"prompt_version_id": 999999, "clip_ids": []},
        )
        assert r.status_code == 404


async def _seed_image_clip(db, repo: ClipCacheRepo, clip_id: int, width, height) -> None:
    """Seed a JPEG image clip with real pixel dimensions in provider_data.media."""
    clip = CanonicalClip(
        key=("catdv", str(clip_id)),
        name=f"photo_{clip_id}.jpg",
        duration_secs=0.0,
        fps=25.0,
        markers=(),
        fields={},
        notes={},
        media=MediaRef(
            mime_type="image/jpeg",
            size_bytes=None,
            cached_path=None,
            upstream_handle=f"photo_{clip_id}.jpg",
        ),
        provider_data={"media": {"width": width, "height": height}},
        fetched_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    await repo.upsert(db, clip=clip, catalog_id="test-catalog")


@pytest.mark.asyncio
async def test_image_clip_dimensions_used_in_token_estimate(db):
    """estimate_for_clip_ids must use provider_data.media width/height to
    compute multi-tile token counts for image clips; a 1536x1536 image spans
    4 tiles (ceil(1536/768)=2 per axis) and must yield more tokens_in than a
    768x768 image (1 tile per axis)."""
    prompts = PromptsRepo()
    _, vid = await prompts.create_with_initial_version(
        db,
        name="img-dim-test",
        description=None,
        body="describe",
        target_map={"scenes": {"kind": "markers"}},
        output_schema={"type": "object"},
        model="gemini-2.5-flash-lite",
    )
    cache = ClipCacheRepo()
    # 1536x1536: ceil(1536/768)=2 per axis → 4 tiles → 4 * IMAGE_TILE_TOKENS image tokens
    await _seed_image_clip(db, cache, clip_id=101, width=1536, height=1536)
    # 768x768: ceil(768/768)=1 per axis → 1 tile → 1 * IMAGE_TILE_TOKENS image tokens
    await _seed_image_clip(db, cache, clip_id=102, width=768, height=768)

    result_large = await estimate_for_clip_ids(
        db,
        clip_cache_repo=cache,
        run_telemetry_repo=RunTelemetryRepo(),
        prompts_repo=prompts,
        model_config_repo=ModelConfigRepo(),
        provider_id="catdv",
        clip_ids=[101],
        prompt_version_id=vid,
    )
    result_small = await estimate_for_clip_ids(
        db,
        clip_cache_repo=cache,
        run_telemetry_repo=RunTelemetryRepo(),
        prompts_repo=prompts,
        model_config_repo=ModelConfigRepo(),
        provider_id="catdv",
        clip_ids=[102],
        prompt_version_id=vid,
    )

    # 1536x1536 → 4 tiles; 768x768 → 1 tile. tokens_in difference = 3 * IMAGE_TILE_TOKENS.
    diff = result_large["tokens_in"] - result_small["tokens_in"]
    assert diff == 3 * IMAGE_TILE_TOKENS, (
        f"Expected 3-tile difference ({3 * IMAGE_TILE_TOKENS} tokens) between "
        f"1536x1536 and 768x768 images, got {diff} "
        f"(large={result_large['tokens_in']}, small={result_small['tokens_in']})"
    )


@pytest.mark.asyncio
async def test_image_dimensions_as_strings_coerced_not_one_tile(db):
    """L3: CatDV may serialise width/height as numeric strings ("1536"). The
    estimate must coerce them to ints (4 tiles for 1536x1536), not silently
    degrade to a single 1-tile estimate. tokens_in must match the int-valued
    case."""
    prompts = PromptsRepo()
    _, vid = await prompts.create_with_initial_version(
        db,
        name="img-strdim-test",
        description=None,
        body="describe",
        target_map={"scenes": {"kind": "markers"}},
        output_schema={"type": "object"},
        model="gemini-2.5-flash-lite",
    )
    cache = ClipCacheRepo()
    # String dimensions (the bug trigger) vs the int-valued equivalent.
    await _seed_image_clip(db, cache, clip_id=201, width="1536", height="1536")
    await _seed_image_clip(db, cache, clip_id=202, width=1536, height=1536)

    result_str = await estimate_for_clip_ids(
        db,
        clip_cache_repo=cache,
        run_telemetry_repo=RunTelemetryRepo(),
        prompts_repo=prompts,
        model_config_repo=ModelConfigRepo(),
        provider_id="catdv",
        clip_ids=[201],
        prompt_version_id=vid,
    )
    result_int = await estimate_for_clip_ids(
        db,
        clip_cache_repo=cache,
        run_telemetry_repo=RunTelemetryRepo(),
        prompts_repo=prompts,
        model_config_repo=ModelConfigRepo(),
        provider_id="catdv",
        clip_ids=[202],
        prompt_version_id=vid,
    )
    # String "1536" must yield the same 4-tile estimate as int 1536, not 1 tile.
    assert result_str["tokens_in"] == result_int["tokens_in"]
    # And it must genuinely be 4 tiles, not the 1-tile degradation.
    assert result_str["tokens_in"] >= 4 * IMAGE_TILE_TOKENS
