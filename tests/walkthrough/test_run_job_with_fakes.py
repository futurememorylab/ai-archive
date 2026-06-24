"""Integration check: the walkthrough fakes drive a real run_job to completion.

The Playwright walkthrough boots the whole app, but this proves the backend the
bulk-annotate-start scenario depends on — FakeAIStore's fast path + FakeGemini +
the seeded production prompt — runs a job end-to-end offline, with no browser
and no ffmpeg.
"""

from __future__ import annotations

from pathlib import Path

from backend.app.models.telemetry import TelemetryCtx
from backend.app.repositories.annotations import AnnotationsRepo
from backend.app.repositories.jobs import JobsRepo
from backend.app.repositories.model_config import ModelConfigRepo
from backend.app.repositories.prompts import PromptsRepo
from backend.app.repositories.review_items import ReviewItemsRepo
from backend.app.repositories.run_telemetry import RunTelemetryRepo
from backend.app.repositories.studio_runs import StudioRunsRepo
from backend.app.repositories.uploaded_clips import UploadedClipsRepo
from backend.app.services.annotator import run_job
from backend.app.services.events import EventBus
from tests.walkthrough import seed
from tests.walkthrough.fakes import (
    DECADE_IDENT,
    FakeAIStore,
    FakeArchive,
    FakeGemini,
    build_clips,
)

_TELEMETRY_CTX = TelemetryCtx(install_id="inst-walkthrough")


async def _run(db, clip_id: int, gemini: FakeGemini) -> tuple[JobsRepo, int]:
    """Seed the production prompt + a one-clip job, then run it with the fakes.

    proxy_resolver is None on purpose: FakeAIStore.status() returns non-None so
    the annotator takes its fast path and never touches the resolver."""
    vid = await seed.seed_production_prompt(db)
    video = Path("/tmp") / "wt_fake.mp4"
    video.write_bytes(b"\x00" * 2048)
    clips = build_clips(video)

    jobs_repo = JobsRepo()
    job_id = await jobs_repo.create_job(db, prompt_version_id=vid, clip_ids=[clip_id])
    await run_job(
        db=db,
        job_id=job_id,
        archive=FakeArchive(clips),
        proxy_resolver=None,
        ai_store=FakeAIStore(),
        gemini=gemini,
        event_bus=EventBus(),
        annotations_repo=AnnotationsRepo(),
        review_items_repo=ReviewItemsRepo(),
        jobs_repo=jobs_repo,
        prompts_repo=PromptsRepo(),
        studio_runs_repo=StudioRunsRepo(),
        uploaded_clips_repo=UploadedClipsRepo(),
        run_telemetry_repo=RunTelemetryRepo(),
        telemetry_ctx=_TELEMETRY_CTX,
        model_config_repo=ModelConfigRepo(),
    )
    return jobs_repo, job_id


async def test_run_job_completes_and_produces_a_decade_draft(db):
    jobs_repo, job_id = await _run(db, 105, FakeGemini())

    job = await jobs_repo.get_job(db, job_id)
    assert job.status == "completed"
    items = await jobs_repo.list_items(db, job.id)
    assert [it.status for it in items] == ["review_ready"]

    review = await ReviewItemsRepo().list_by_clip(db, 105)
    decade = [ri for ri in review if ri.target_identifier == DECADE_IDENT]
    assert decade and decade[0].proposed_value == "30.léta"
