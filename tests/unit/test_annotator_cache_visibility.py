"""Spec 2026-06-23-annotate-cache-queue-consistency §1: when an annotate job
caches a clip (AI-store miss → proxy download), it writes a prefetch_queue row
so the cache appears in the queue page with live progress — identical in shape
to a Cache-button row (downloading → done, progress on the download).
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
from backend.app.repositories.model_config import ModelConfigRepo
from backend.app.repositories.prefetch_queue import PrefetchQueueRepo
from backend.app.repositories.prompts import PromptsRepo
from backend.app.repositories.review_items import ReviewItemsRepo
from backend.app.repositories.run_telemetry import RunTelemetryRepo
from backend.app.repositories.studio_runs import StudioRunsRepo
from backend.app.repositories.uploaded_clips import UploadedClipsRepo
from backend.app.services.annotator import run_job
from backend.app.services.events import EventBus
from backend.app.services.proxy_resolver import ProxyNotFound


@pytest.fixture
async def db(tmp_path: Path):
    cm = open_db(tmp_path / "t.db")
    conn = await cm.__aenter__()
    await apply_migrations(conn, Path("backend/migrations"))
    yield conn
    await cm.__aexit__(None, None, None)


async def _seed_studio_job(db, *, clip_id=42):
    prompts = PromptsRepo()
    _pid, vid = await prompts.create_with_initial_version(
        db, name="p", description=None, body="do x",
        target_map={"scenes": {"kind": "markers"}},
        output_schema={"type": "object"}, model="gemini-2.5-pro",
    )
    runs = StudioRunsRepo()
    run_id = await runs.create_pending(
        db, prompt_version_id=vid, clip_id=clip_id, model="gemini-2.5-pro"
    )
    jobs = JobsRepo()
    job_id = await jobs.create_job(
        db, prompt_version_id=vid, clip_ids=[clip_id], kind="studio"
    )
    await runs.attach_job(db, run_id, job_id=job_id)
    return prompts, runs, jobs, vid, run_id, job_id


def _gemini_ok():
    g = MagicMock()
    g.annotate = MagicMock(
        return_value={
            "text": json.dumps({"scenes": [{"name": "s1", "in": {"secs": 0.0}, "out": {"secs": 5.0}}]}),
            "raw": {"usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 1}},
        }
    )
    return g


async def _run(db, *, job_id, proxy, ai_store, jobs, prompts, runs, queue_repo, archive=None):
    await run_job(
        db=db, job_id=job_id,
        archive=archive or MagicMock(),
        proxy_resolver=proxy, ai_store=ai_store, gemini=_gemini_ok(),
        event_bus=EventBus(),
        annotations_repo=AnnotationsRepo(), review_items_repo=ReviewItemsRepo(),
        jobs_repo=jobs, prompts_repo=prompts, studio_runs_repo=runs,
        uploaded_clips_repo=UploadedClipsRepo(), run_telemetry_repo=RunTelemetryRepo(),
        telemetry_ctx=TelemetryCtx(install_id="t"),
        model_config_repo=ModelConfigRepo(),
        prefetch_queue_repo=queue_repo,
    )


@pytest.mark.asyncio
async def test_annotate_cache_miss_writes_done_queue_row_with_progress(db):
    prompts, runs, jobs, vid, run_id, job_id = await _seed_studio_job(db, clip_id=42)

    archive = MagicMock()
    archive.get_clip = AsyncMock(
        return_value=MagicMock(provider_data={"name": "clip-42"}, duration_secs=10.0)
    )

    # The download reports progress through the threaded callback, exactly like
    # the real resolver does (#78 plumbing).
    async def _resolve(clip_id, progress_cb=None):
        assert progress_cb is not None, "annotator must thread a progress_cb"
        await progress_cb(7_000_000, 21_000_000)
        return Path("/tmp/clip-42.mp4")

    proxy = MagicMock()
    proxy.path_for_clip_id = AsyncMock(side_effect=_resolve)

    ai_store = MagicMock()
    ai_store.status = AsyncMock(return_value=None)  # AI-store miss → download
    ai_store.ensure_uploaded = AsyncMock(return_value="upload-ref")
    ai_store.reference_for_gemini = AsyncMock(return_value={"uri": "gs://x"})

    queue_repo = PrefetchQueueRepo()
    await _run(db, job_id=job_id, proxy=proxy, ai_store=ai_store, jobs=jobs, prompts=prompts, runs=runs, queue_repo=queue_repo, archive=archive)

    rows = await queue_repo.list_recent(db, limit=10)
    mine = [r for r in rows if str(r["provider_clip_id"]) == "42"]
    assert len(mine) == 1, "annotate cache miss must create exactly one queue row"
    row = mine[0]
    assert row["status"] == "done"
    assert row["requested_by"] == "annotate"
    assert row["bytes_downloaded"] == 7_000_000
    assert row["bytes_total"] == 21_000_000


@pytest.mark.asyncio
async def test_annotate_cache_miss_marks_row_error_when_uncacheable(db):
    prompts, runs, jobs, vid, run_id, job_id = await _seed_studio_job(db, clip_id=77)

    proxy = MagicMock()
    proxy.path_for_clip_id = AsyncMock(side_effect=ProxyNotFound("not cached"))
    ai_store = MagicMock()
    ai_store.status = AsyncMock(return_value=None)

    queue_repo = PrefetchQueueRepo()
    await _run(db, job_id=job_id, proxy=proxy, ai_store=ai_store, jobs=jobs, prompts=prompts, runs=runs, queue_repo=queue_repo)

    rows = await queue_repo.list_recent(db, limit=10)
    mine = [r for r in rows if str(r["provider_clip_id"]) == "77"]
    assert len(mine) == 1
    assert mine[0]["status"] == "error"
    assert mine[0]["error"]


@pytest.mark.asyncio
async def test_annotate_ai_store_hit_writes_no_queue_row(db):
    """Fast path: clip already in GCS → no download, no queue row (matches the
    Cache button, which also no-ops when already cached)."""
    prompts, runs, jobs, vid, run_id, job_id = await _seed_studio_job(db, clip_id=88)

    archive = MagicMock()
    archive.get_clip = AsyncMock(
        return_value=MagicMock(provider_data={"name": "clip-88"}, duration_secs=10.0)
    )
    proxy = MagicMock()
    proxy.path_for_clip_id = AsyncMock(side_effect=ProxyNotFound("must not be called"))
    ai_store = MagicMock()
    ai_store.status = AsyncMock(return_value=MagicMock(handle="gs://bucket/88.mp4"))
    ai_store.reference_for_gemini = AsyncMock(return_value={"uri": "gs://bucket/88.mp4"})

    queue_repo = PrefetchQueueRepo()
    await _run(db, job_id=job_id, proxy=proxy, ai_store=ai_store, jobs=jobs, prompts=prompts, runs=runs, queue_repo=queue_repo, archive=archive)

    rows = await queue_repo.list_recent(db, limit=10)
    assert [r for r in rows if str(r["provider_clip_id"]) == "88"] == []
