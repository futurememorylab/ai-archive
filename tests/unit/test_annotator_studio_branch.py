"""Annotator service — studio path persists to studio_run AND review_items.

We assert that for a job with kind='studio':
  * No annotation row is inserted (annotations_repo.insert not called).
  * The matching studio_run row transitions to status='ok' with output_json.
  * review_items ARE inserted, linked by studio_run_id (so the UI can
    render markers/fields/notes through the same panels pipeline the
    clip-detail page uses).
"""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.app.db import open_db
from backend.app.migrations_runner import apply_migrations
from backend.app.models.telemetry import TelemetryCtx
from backend.app.repositories.annotations import AnnotationsRepo
from backend.app.repositories.jobs import JobsRepo
from backend.app.repositories.prompts import PromptsRepo
from backend.app.repositories.review_items import ReviewItemsRepo
from backend.app.repositories.run_telemetry import RunTelemetryRepo
from backend.app.repositories.studio_runs import StudioRunsRepo
from backend.app.services.annotator import run_job
from backend.app.services.events import EventBus
from backend.app.services.proxy_resolver import ProxyNotFound


async def _seed_studio_fixtures(db):
    """Seed a prompt, draft version, studio_run, and a kind='studio' job."""
    prompts = PromptsRepo()
    pid, vid = await prompts.create_with_initial_version(
        db,
        name="p",
        description=None,
        body="do x",
        target_map={"scenes": {"kind": "markers"}},
        output_schema={"type": "object"},
        model="gemini-2.5-pro",
    )
    runs = StudioRunsRepo()
    run_id = await runs.create_pending(
        db, prompt_version_id=vid, clip_id=42, model="gemini-2.5-pro"
    )
    jobs = JobsRepo()
    job_id = await jobs.create_job(db, prompt_version_id=vid, clip_ids=[42], kind="studio")
    await runs.attach_job(db, run_id, job_id=job_id)
    return prompts, runs, jobs, vid, run_id, job_id


@pytest.fixture
async def db(tmp_path: Path):
    cm = open_db(tmp_path / "t.db")
    conn = await cm.__aenter__()
    await apply_migrations(conn, Path("backend/migrations"))
    yield conn
    await cm.__aexit__(None, None, None)


@pytest.mark.asyncio
async def test_studio_kind_persists_run_skips_catdv_write(db):
    # Seed a prompt + draft version
    prompts = PromptsRepo()
    pid, vid = await prompts.create_with_initial_version(
        db,
        name="p",
        description=None,
        body="do x",
        target_map={"scenes": {"kind": "markers"}},
        output_schema={"type": "object"},
        model="gemini-2.5-pro",
    )

    # Create a studio_run row first, then a kind='studio' job linked to it
    runs = StudioRunsRepo()
    run_id = await runs.create_pending(
        db, prompt_version_id=vid, clip_id=42, model="gemini-2.5-pro"
    )
    jobs = JobsRepo()
    job_id = await jobs.create_job(db, prompt_version_id=vid, clip_ids=[42], kind="studio")
    await runs.attach_job(db, run_id, job_id=job_id)

    # Fakes for the externals.
    archive = MagicMock()
    archive.get_clip = AsyncMock(
        return_value=MagicMock(
            provider_data={"name": "clip-42"},
            duration_secs=10.0,
        )
    )
    proxy = MagicMock()
    proxy.path_for_clip_id = AsyncMock(return_value=Path("/tmp/clip-42.mp4"))
    ai_store = MagicMock()
    # Force the upload path (AI store miss) — this test exercises the
    # resolve → upload → annotate flow.
    ai_store.status = AsyncMock(return_value=None)
    ai_store.ensure_uploaded = AsyncMock(return_value="upload-ref")
    ai_store.reference_for_gemini = AsyncMock(return_value={"uri": "gs://x"})
    gemini = MagicMock()
    gemini.annotate = MagicMock(
        return_value={
            "text": json.dumps(
                {
                    "scenes": [
                        {"name": "s1", "in": {"secs": 0.0}, "out": {"secs": 5.0}},
                    ],
                }
            ),
            "raw": {"usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 50}},
        }
    )

    annotations = AnnotationsRepo()
    review_items = ReviewItemsRepo()
    annotations.insert = AsyncMock()  # type: ignore[method-assign]
    review_items.bulk_insert = AsyncMock()  # type: ignore[method-assign]

    bus = EventBus()

    await run_job(
        db=db,
        job_id=job_id,
        archive=archive,
        proxy_resolver=proxy,
        ai_store=ai_store,
        gemini=gemini,
        event_bus=bus,
        annotations_repo=annotations,
        review_items_repo=review_items,
        jobs_repo=jobs,
        prompts_repo=prompts,
        studio_runs_repo=runs,
        run_telemetry_repo=RunTelemetryRepo(),
        telemetry_ctx=TelemetryCtx(install_id="inst-test"),
    )

    # Assertion: CatDV-side annotations write was NOT called
    annotations.insert.assert_not_called()

    # review_items WERE inserted — once for the marker. Verify the items
    # carry studio_run_id (not annotation_id) so the studio render path
    # can find them.
    assert review_items.bulk_insert.await_count == 1
    inserted_items = review_items.bulk_insert.await_args.args[1]
    assert len(inserted_items) == 1
    assert inserted_items[0].kind == "marker"
    assert inserted_items[0].studio_run_id == run_id
    assert inserted_items[0].annotation_id is None

    # studio_run completed ok with output
    run = await runs.get(db, run_id)
    assert run.status == "ok"
    assert run.output_json == {
        "scenes": [{"name": "s1", "in": {"secs": 0.0}, "out": {"secs": 5.0}}],
    }


@pytest.mark.asyncio
async def test_ai_cache_hit_skips_proxy_resolver(db):
    """When the AI store already has the clip, skip the local proxy resolver
    + upload step entirely. Gemini can read directly from GCS."""
    prompts, runs, jobs, vid, run_id, job_id = await _seed_studio_fixtures(db)

    archive = MagicMock()
    archive.get_clip = AsyncMock(
        return_value=MagicMock(
            provider_data={"name": "clip-42"},
            duration_secs=10.0,
        )
    )
    # Proxy resolver MUST NOT be called — if it is, raise loud.
    proxy = MagicMock()
    proxy.path_for_clip_id = AsyncMock(side_effect=ProxyNotFound("not cached"))

    ai_store = MagicMock()
    cached_ref = MagicMock(handle="gs://bucket/42.mp4")
    ai_store.status = AsyncMock(return_value=cached_ref)
    # ensure_uploaded MUST NOT be called — fast path skips it.
    ai_store.ensure_uploaded = AsyncMock(side_effect=AssertionError("ensure_uploaded called"))
    ai_store.reference_for_gemini = AsyncMock(return_value={"uri": "gs://bucket/42.mp4"})

    gemini = MagicMock()
    gemini.annotate = MagicMock(
        return_value={
            "text": json.dumps({"scenes": [{"name": "s1", "in_secs": 0, "out_secs": 5}]}),
            "raw": {"usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5}},
        }
    )

    annotations = AnnotationsRepo()
    review_items = ReviewItemsRepo()
    annotations.insert = AsyncMock()  # type: ignore[method-assign]
    review_items.bulk_insert = AsyncMock()  # type: ignore[method-assign]

    bus = EventBus()

    await run_job(
        db=db,
        job_id=job_id,
        archive=archive,
        proxy_resolver=proxy,
        ai_store=ai_store,
        gemini=gemini,
        event_bus=bus,
        annotations_repo=annotations,
        review_items_repo=review_items,
        jobs_repo=jobs,
        prompts_repo=prompts,
        studio_runs_repo=runs,
        run_telemetry_repo=RunTelemetryRepo(),
        telemetry_ctx=TelemetryCtx(install_id="inst-test"),
    )

    # Fast path: resolver + uploader were skipped entirely.
    proxy.path_for_clip_id.assert_not_awaited()
    ai_store.ensure_uploaded.assert_not_awaited()
    # AI store status was consulted, then reference_for_gemini was called
    # with the cached ref.
    ai_store.status.assert_awaited_once()
    ai_store.reference_for_gemini.assert_awaited_once_with(cached_ref)

    # Run still completes successfully.
    run = await runs.get(db, run_id)
    assert run.status == "ok"


@pytest.mark.asyncio
async def test_run_fails_clearly_when_neither_cached(db):
    """When neither local proxy cache nor AI store has the clip, the run
    must fail with a message that names both caches so the operator knows
    what to do next."""
    prompts, runs, jobs, vid, run_id, job_id = await _seed_studio_fixtures(db)

    archive = MagicMock()
    archive.get_clip = AsyncMock(side_effect=AssertionError("archive.get_clip called"))

    proxy = MagicMock()
    proxy.path_for_clip_id = AsyncMock(side_effect=ProxyNotFound("clip 42 not cached locally"))

    ai_store = MagicMock()
    ai_store.status = AsyncMock(return_value=None)
    ai_store.ensure_uploaded = AsyncMock(side_effect=AssertionError("ensure_uploaded called"))
    ai_store.reference_for_gemini = AsyncMock(
        side_effect=AssertionError("reference_for_gemini called")
    )

    gemini = MagicMock()
    gemini.annotate = MagicMock(side_effect=AssertionError("gemini.annotate called"))

    annotations = AnnotationsRepo()
    review_items = ReviewItemsRepo()
    annotations.insert = AsyncMock()  # type: ignore[method-assign]
    review_items.bulk_insert = AsyncMock()  # type: ignore[method-assign]

    bus = EventBus()

    await run_job(
        db=db,
        job_id=job_id,
        archive=archive,
        proxy_resolver=proxy,
        ai_store=ai_store,
        gemini=gemini,
        event_bus=bus,
        annotations_repo=annotations,
        review_items_repo=review_items,
        jobs_repo=jobs,
        prompts_repo=prompts,
        studio_runs_repo=runs,
        run_telemetry_repo=RunTelemetryRepo(),
        telemetry_ctx=TelemetryCtx(install_id="inst-test"),
    )

    # Studio run reports the error with a message naming both caches.
    run = await runs.get(db, run_id)
    assert run.status == "error"
    assert run.error is not None
    assert "not locally cached" in run.error
    assert "AI store" in run.error

    # Job item also reflects the error.
    items = await jobs.list_items(db, job_id)
    assert len(items) == 1
    assert items[0].status == "error"
    assert items[0].error_message is not None
    assert "not locally cached" in items[0].error_message
    assert "AI store" in items[0].error_message
