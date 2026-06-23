import datetime as dt
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

_TELEMETRY_CTX = TelemetryCtx(install_id="inst-test")


class FakeResolver:
    def __init__(self, files: dict[int, Path]):
        self.files = files

    async def path_for_clip_id(self, clip_id: int) -> Path:
        return self.files[clip_id]

    def is_managed(self, path):
        return True


class FakeAIStore:
    """Implements just enough of AIInputStore for the worker test."""

    id = "gcs:bucket"

    def __init__(self) -> None:
        self.uploads: list[tuple[int, Path]] = []

    async def ensure_uploaded(self, clip_key, local_path, mime):
        from backend.app.archive.ai_store_model import UploadedRef

        self.uploads.append((int(clip_key[1]), local_path))
        return UploadedRef(
            handle=f"gs://bucket/clips/{clip_key[1]}.mov",
            mime_type=mime,
            size_bytes=local_path.stat().st_size,
            sha256="fakesha",
            uploaded_at=dt.datetime.now(dt.UTC),
            expires_at=None,
        )

    async def reference_for_gemini(self, ref):
        return {"file_data": {"file_uri": ref.handle, "mime_type": ref.mime_type}}

    async def status(self, clip_key):
        return None

    async def evict(self, clip_key):
        return None

    async def health(self):
        from backend.app.archive.ai_store_model import StoreHealth

        return StoreHealth(ok=True)


class FakeArchive:
    def __init__(self, clips: dict[int, dict]):
        self.clips = clips

    async def get_clip(self, clip_id_str: str) -> CanonicalClip:
        clip = self.clips[int(clip_id_str)]
        return CanonicalClip(
            key=("catdv", clip_id_str),
            name=clip.get("name", ""),
            duration_secs=0.0,
            fps=float(clip.get("fps") or 25.0),
            markers=tuple(),
            fields={},
            notes={},
            media=MediaRef(
                mime_type="video/quicktime",
                size_bytes=None,
                cached_path=None,
                upstream_handle=clip_id_str,
            ),
            provider_data=clip,
            fetched_at=dt.datetime.now(dt.UTC),
        )


@pytest.mark.asyncio
async def test_run_job_processes_two_clips_end_to_end(db, tmp_path):
    prompts = PromptsRepo()
    _, vid = await prompts.create_with_initial_version(
        db,
        name="t",
        description=None,
        body="describe scenes",
        target_map={
            "scenes": {"kind": "markers"},
            "decade": {"kind": "field", "identifier": "pragafilm.dekáda.natočení"},
        },
        output_schema={"type": "object"},
        model="gemini-2.5-pro",
    )

    jobs_repo = JobsRepo()
    job_id = await jobs_repo.create_job(db, prompt_version_id=vid, clip_ids=[101, 102])

    files = {}
    for clip_id in [101, 102]:
        p = tmp_path / f"{clip_id}.mov"
        p.write_bytes(b"X" * 100)
        files[clip_id] = p

    archive = FakeArchive(
        {
            101: {"ID": 101, "name": "Clip_101", "markers": []},
            102: {"ID": 102, "name": "Clip_102", "markers": []},
        }
    )
    resolver = FakeResolver(files)
    ai_store = FakeAIStore()
    structured = {
        "scenes": [
            {"name": "scene-1", "in": {"frm": 0, "secs": 0.0}, "out": {"frm": 25, "secs": 1.0}}
        ],
        "decade": "30.léta",
    }

    class FakeGeminiStructured:
        def annotate(self, *, file_ref, prompt, schema, model, media_resolution=None):
            import json

            return {
                "text": json.dumps(structured),
                "raw": {"candidates": [{"text": json.dumps(structured)}]},
            }

    bus = EventBus()
    sub_101 = bus.subscribe(f"job:{job_id}")

    await run_job(
        db=db,
        job_id=job_id,
        archive=archive,
        proxy_resolver=resolver,
        ai_store=ai_store,
        gemini=FakeGeminiStructured(),
        event_bus=bus,
        annotations_repo=AnnotationsRepo(),
        review_items_repo=ReviewItemsRepo(),
        jobs_repo=jobs_repo,
        prompts_repo=prompts,
        studio_runs_repo=StudioRunsRepo(),
        uploaded_clips_repo=UploadedClipsRepo(),
        run_telemetry_repo=RunTelemetryRepo(),
        telemetry_ctx=_TELEMETRY_CTX,
        model_config_repo=ModelConfigRepo(),
    )

    items = await jobs_repo.list_items(db, job_id)
    assert [it.status for it in items] == ["review_ready", "review_ready"]

    annotations = AnnotationsRepo()
    rows_101 = await annotations.list_by_clip(db, 101)
    assert len(rows_101) == 1
    review = ReviewItemsRepo()
    items_101 = await review.list_by_clip(db, 101)
    assert {it.kind for it in items_101} == {"marker", "field"}

    assert not sub_101.empty()


@pytest.mark.asyncio
async def test_run_job_marks_item_error_when_gemini_raises(db, tmp_path):
    from backend.app.services.gemini import GeminiSafetyError

    prompts = PromptsRepo()
    _, vid = await prompts.create_with_initial_version(
        db,
        name="t",
        description=None,
        body="p",
        target_map={"scenes": {"kind": "markers"}},
        output_schema={},
        model="m",
    )
    jobs_repo = JobsRepo()
    job_id = await jobs_repo.create_job(db, prompt_version_id=vid, clip_ids=[1])

    p = tmp_path / "1.mov"
    p.write_bytes(b"x")
    resolver = FakeResolver({1: p})
    archive = FakeArchive({1: {"ID": 1, "name": "c", "markers": []}})

    class FailingGemini:
        def annotate(self, **kwargs):
            raise GeminiSafetyError("blocked")

    await run_job(
        db=db,
        job_id=job_id,
        archive=archive,
        proxy_resolver=resolver,
        ai_store=FakeAIStore(),
        gemini=FailingGemini(),
        event_bus=EventBus(),
        annotations_repo=AnnotationsRepo(),
        review_items_repo=ReviewItemsRepo(),
        jobs_repo=jobs_repo,
        prompts_repo=prompts,
        studio_runs_repo=StudioRunsRepo(),
        uploaded_clips_repo=UploadedClipsRepo(),
        run_telemetry_repo=RunTelemetryRepo(),
        telemetry_ctx=_TELEMETRY_CTX,
        model_config_repo=ModelConfigRepo(),
    )
    items = await jobs_repo.list_items(db, job_id)
    assert items[0].status == "error"
    assert "blocked" in (items[0].error_message or "")


@pytest.mark.asyncio
async def test_job_error_message_includes_status_code_for_httpx_failure(tmp_path):
    """T1-3: Annotator-level error messages must carry actionable detail.

    Bare str(httpx.HTTPStatusError) is the empty string; without the
    humanise() wrapper the user sees only 'HTTPStatusError' with no
    status code or body — unactionable."""
    import httpx

    from backend.app.services import annotator as annotator_mod

    request = httpx.Request("POST", "http://example/x")
    response = httpx.Response(503, request=request, text='{"error": "EBUSY"}')
    exc = httpx.HTTPStatusError("503", request=request, response=response)

    # Use the same humanise the annotator uses, then assert the message
    # contains the status code and a body fragment.
    msg = annotator_mod._humanise_error(exc)
    assert "503" in msg
    assert "EBUSY" in msg
