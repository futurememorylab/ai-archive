import datetime as dt
import json
from pathlib import Path

import pytest

from backend.app.archive.model import CanonicalClip, MediaRef
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


class _Resolver:
    def __init__(self, files):
        self.files = files

    async def path_for_clip_id(self, clip_id, progress_cb=None):
        return self.files[clip_id]

    def is_managed(self, path):
        return True


class _AIStore:
    id = "gcs:bucket"

    async def status(self, clip_key):
        return None

    async def ensure_uploaded(self, clip_key, local_path, mime):
        from backend.app.archive.ai_store_model import UploadedRef

        return UploadedRef(
            handle=f"gs://b/{clip_key[1]}.mov",
            mime_type=mime,
            size_bytes=1,
            sha256="x",
            uploaded_at=dt.datetime.now(dt.UTC),
            expires_at=None,
        )

    async def reference_for_gemini(self, ref):
        return {"file_data": {"file_uri": ref.handle, "mime_type": ref.mime_type}}


class _Archive:
    async def get_clip(self, clip_id_str):
        return CanonicalClip(
            key=("catdv", clip_id_str),
            name=f"Clip_{clip_id_str}",
            duration_secs=0.0,
            fps=25.0,
            markers=tuple(),
            fields={},
            notes={},
            media=MediaRef(
                mime_type="video/quicktime",
                size_bytes=None,
                cached_path=None,
                upstream_handle=clip_id_str,
            ),
            provider_data={},
            fetched_at=dt.datetime.now(dt.UTC),
        )


class _Gemini:
    def annotate(self, *, file_ref, prompt, schema, model, media_resolution=None):
        out = json.dumps({"scenes": [{"name": "s", "in": {"secs": 0.0}, "out": {"secs": 1.0}}]})
        return {"text": out, "raw": {"candidates": [{"text": out}]}}


@pytest.mark.asyncio
async def test_run_job_processes_only_pending_or_error_items(db, tmp_path):
    """Retry scoping is now done by status, not an only_clip_ids parameter:
    run_job re-processes only items in 'pending'/'error'. A clip already in a
    terminal state (here, item 101 pre-marked 'review_ready') is skipped, so
    resetting just the targeted items to 'pending' is what scopes a retry."""
    prompts = PromptsRepo()
    _, vid = await prompts.create_with_initial_version(
        db,
        name="t",
        description=None,
        body="describe",
        target_map={"scenes": {"kind": "markers"}},
        output_schema={},
        model="m",
    )
    jobs = JobsRepo()
    job_id = await jobs.create_job(db, prompt_version_id=vid, clip_ids=[101, 102])

    files = {}
    for cid in (101, 102):
        p: Path = tmp_path / f"{cid}.mov"
        p.write_bytes(b"X" * 10)
        files[cid] = p

    # 101 is already done; only 102 stays pending and should re-run.
    items_before = {it.catdv_clip_id: it.id for it in await jobs.list_items(db, job_id)}
    await jobs.update_item_status(db, items_before[101], "review_ready")

    await run_job(
        db=db,
        job_id=job_id,
        archive=_Archive(),
        proxy_resolver=_Resolver(files),
        ai_store=_AIStore(),
        gemini=_Gemini(),
        event_bus=EventBus(),
        annotations_repo=AnnotationsRepo(),
        review_items_repo=ReviewItemsRepo(),
        jobs_repo=jobs,
        prompts_repo=prompts,
        studio_runs_repo=StudioRunsRepo(),
        uploaded_clips_repo=UploadedClipsRepo(),
        run_telemetry_repo=RunTelemetryRepo(),
        telemetry_ctx=TelemetryCtx(install_id="inst-test"),
        model_config_repo=ModelConfigRepo(),
    )

    items = {it.catdv_clip_id: it.status for it in await jobs.list_items(db, job_id)}
    assert items[101] == "review_ready"  # terminal -> skipped (not re-run)
    assert items[102] == "review_ready"  # pending -> processed
