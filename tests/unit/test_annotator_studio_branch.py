"""Annotator service — studio path persists to studio_run and skips CatDV-write.

We assert that for a job with kind='studio':
  * No annotation row is inserted (annotations_repo.insert not called).
  * The matching studio_run row transitions to status='ok' with output_json.
  * review_items are not inserted (target_map.expand not called).
"""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.app.db import open_db
from backend.app.migrations_runner import apply_migrations
from backend.app.repositories.annotations import AnnotationsRepo
from backend.app.repositories.jobs import JobsRepo
from backend.app.repositories.prompts import PromptsRepo
from backend.app.repositories.review_items import ReviewItemsRepo
from backend.app.repositories.studio_runs import StudioRunsRepo
from backend.app.services.annotator import run_job
from backend.app.services.events import EventBus


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
    job_id = await jobs.create_job(
        db, prompt_version_id=vid, clip_ids=[42], kind="studio"
    )
    await runs.attach_job(db, run_id, job_id=job_id)

    # Fakes for the externals.
    archive = MagicMock()
    archive.get_clip = AsyncMock(return_value=MagicMock(
        provider_data={"name": "clip-42"},
        duration_secs=10.0,
    ))
    proxy = MagicMock()
    proxy.path_for_clip_id = AsyncMock(return_value=Path("/tmp/clip-42.mp4"))
    ai_store = MagicMock()
    ai_store.ensure_uploaded = AsyncMock(return_value="upload-ref")
    ai_store.reference_for_gemini = AsyncMock(return_value={"uri": "gs://x"})
    gemini = MagicMock()
    gemini.annotate = MagicMock(return_value={
        "text": json.dumps({"scenes": [{"name": "s1", "in_secs": 0, "out_secs": 5}]}),
        "raw": {"usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 50}},
    })

    annotations = AnnotationsRepo()
    review_items = ReviewItemsRepo()
    annotations.insert = AsyncMock()  # type: ignore[method-assign]
    review_items.bulk_insert = AsyncMock()  # type: ignore[method-assign]

    bus = EventBus()

    await run_job(
        db=db, job_id=job_id,
        archive=archive, proxy_resolver=proxy, ai_store=ai_store, gemini=gemini,
        event_bus=bus,
        annotations_repo=annotations, review_items_repo=review_items,
        jobs_repo=jobs, prompts_repo=prompts,
        studio_runs_repo=runs,
    )

    # Assertion: CatDV-side writes were NOT called
    annotations.insert.assert_not_called()
    review_items.bulk_insert.assert_not_called()

    # studio_run completed ok with output
    run = await runs.get(db, run_id)
    assert run.status == "ok"
    assert run.output_json == {"scenes": [{"name": "s1", "in_secs": 0, "out_secs": 5}]}
