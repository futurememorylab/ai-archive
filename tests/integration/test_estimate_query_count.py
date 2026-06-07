"""estimate_for_clip_ids: DB-first (offline-safe), query count does not
scale with clip count (ADR 0046)."""

import importlib

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
        provider_id="catdv",
        clip_ids=list(range(1, 11)),
        prompt_version_id=vid,
    )
    assert result_small["tokens_in"] > 0
    assert result_small["confidence"] == "rough"
    assert result_small["n_clips"] == 10
    assert result_small["n_unknown"] == 0

    # Query count must be the same for 10 and 100 clips.
    # Breakdown: 1 prompt version read + 1 clip_cache chunk read +
    # 1 input_ratio + 2 output_rates (prompt-hash miss then model-only)
    # = 5 queries, regardless of clip count.
    async with assert_query_count(db, 5):
        await estimate_for_clip_ids(
            db,
            clip_cache_repo=cache,
            run_telemetry_repo=RunTelemetryRepo(),
            prompts_repo=prompts,
            provider_id="catdv",
            clip_ids=list(range(1, 11)),
            prompt_version_id=vid,
        )
    async with assert_query_count(db, 5):
        await estimate_for_clip_ids(
            db,
            clip_cache_repo=cache,
            run_telemetry_repo=RunTelemetryRepo(),
            prompts_repo=prompts,
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
        provider_id="catdv",
        clip_ids=[777],
        prompt_version_id=vid,
    )
    # Unknown clip → conservative defaults, never an exception.
    assert result["n_clips"] == 1
    assert result["confidence"] == "rough"
    assert result["n_unknown"] == 1


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
