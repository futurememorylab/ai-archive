import hashlib
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from backend.app.models.template import Template
from backend.app.repositories.annotations import AnnotationsRepo
from backend.app.repositories.gcs_files import GcsFilesRepo
from backend.app.repositories.jobs import JobsRepo
from backend.app.repositories.review_items import ReviewItemsRepo
from backend.app.repositories.templates import TemplatesRepo
from backend.app.services.annotator import run_job
from backend.app.services.events import EventBus
from tests.fakes.fake_gemini import FakeResponse


class FakeResolver:
    def __init__(self, files: dict[int, Path]):
        self.files = files

    async def path_for_clip_id(self, clip_id: int) -> Path:
        return self.files[clip_id]

    def is_managed(self, path):
        return True


class FakeGcs:
    def __init__(self, bucket: str):
        self.bucket_name = bucket
        self.uploads: list[tuple[int, Path]] = []

    def gs_uri(self, clip_id: int) -> str:
        return f"gs://{self.bucket_name}/clips/{clip_id}.mov"

    def upload_if_absent(self, *, clip_id: int, local_path: Path, mime: str) -> str:
        self.uploads.append((clip_id, local_path))
        return self.gs_uri(clip_id)


class FakeCatdv:
    def __init__(self, clips: dict[int, dict]):
        self.clips = clips

    async def get_clip(self, clip_id: int) -> dict:
        return self.clips[clip_id]


@pytest.mark.asyncio
async def test_run_job_processes_two_clips_end_to_end(db, tmp_path):
    templates = TemplatesRepo()
    template_id = await templates.create(db, Template(
        name="t",
        prompt="describe scenes",
        output_schema={"type": "object"},
        target_map={
            "scenes": {"kind": "markers"},
            "decade": {"kind": "field", "identifier": "pragafilm.dekáda.natočení"},
        },
        model="gemini-2.5-pro",
    ))

    jobs_repo = JobsRepo()
    job_id = await jobs_repo.create_job(db, template_id=template_id, clip_ids=[101, 102])

    files = {}
    for clip_id in [101, 102]:
        p = tmp_path / f"{clip_id}.mov"
        p.write_bytes(b"X" * 100)
        files[clip_id] = p

    catdv = FakeCatdv({
        101: {"ID": 101, "name": "Clip_101", "markers": []},
        102: {"ID": 102, "name": "Clip_102", "markers": []},
    })
    resolver = FakeResolver(files)
    gcs = FakeGcs("bucket")
    structured = {
        "scenes": [{"name": "scene-1", "in": {"frm": 0, "secs": 0.0}, "out": {"frm": 25, "secs": 1.0}}],
        "decade": "30.léta",
    }

    class FakeGeminiStructured:
        def annotate(self, *, gcs_uri, mime, prompt, schema, model):
            import json
            return {"text": json.dumps(structured), "raw": {"candidates": [{"text": json.dumps(structured)}]}}

    bus = EventBus()
    sub_101 = bus.subscribe(f"job:{job_id}")

    await run_job(
        db=db,
        job_id=job_id,
        catdv=catdv,
        proxy_resolver=resolver,
        gcs=gcs,
        gemini=FakeGeminiStructured(),
        event_bus=bus,
        gcs_files_repo=GcsFilesRepo(),
        annotations_repo=AnnotationsRepo(),
        review_items_repo=ReviewItemsRepo(),
        jobs_repo=jobs_repo,
        templates_repo=templates,
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

    templates = TemplatesRepo()
    template_id = await templates.create(db, Template(
        name="t", prompt="p", output_schema={}, target_map={"scenes": {"kind": "markers"}},
        model="m",
    ))
    jobs_repo = JobsRepo()
    job_id = await jobs_repo.create_job(db, template_id=template_id, clip_ids=[1])

    p = tmp_path / "1.mov"
    p.write_bytes(b"x")
    resolver = FakeResolver({1: p})
    catdv = FakeCatdv({1: {"ID": 1, "name": "c", "markers": []}})

    class FailingGemini:
        def annotate(self, **kwargs):
            raise GeminiSafetyError("blocked")

    await run_job(
        db=db,
        job_id=job_id,
        catdv=catdv,
        proxy_resolver=resolver,
        gcs=FakeGcs("b"),
        gemini=FailingGemini(),
        event_bus=EventBus(),
        gcs_files_repo=GcsFilesRepo(),
        annotations_repo=AnnotationsRepo(),
        review_items_repo=ReviewItemsRepo(),
        jobs_repo=jobs_repo,
        templates_repo=templates,
    )
    items = await jobs_repo.list_items(db, job_id)
    assert items[0].status == "error"
    assert "blocked" in (items[0].error_message or "")
