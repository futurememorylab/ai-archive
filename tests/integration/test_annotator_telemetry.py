"""Both finalize paths write run_telemetry; studio_run.tokens_out is
billable (candidates + thinking); cost_usd computed; errors recorded."""

import json

import pytest

from backend.app.models.telemetry import TelemetryCtx
from backend.app.repositories.annotations import AnnotationsRepo
from backend.app.repositories.jobs import JobsRepo
from backend.app.repositories.prompts import PromptsRepo
from backend.app.repositories.review_items import ReviewItemsRepo
from backend.app.repositories.run_telemetry import RunTelemetryRepo
from backend.app.repositories.studio_runs import StudioRunsRepo
from backend.app.repositories.uploaded_clips import UploadedClipsRepo
from backend.app.services.annotator import run_job
from backend.app.services.events import EventBus

# --- fakes: copy FakeResolver / FakeAIStore / FakeArchive verbatim from
# tests/integration/test_annotator_worker.py (they are stable test
# doubles; a shared tests/fakes module refactor is out of scope). ---
from tests.integration.test_annotator_worker import (  # type: ignore
    FakeAIStore,
    FakeArchive,
    FakeResolver,
)

USAGE = {
    "promptTokenCount": 3000,
    "candidatesTokenCount": 100,
    "thoughtsTokenCount": 40,
    "promptTokensDetails": [
        {"modality": "TEXT", "tokenCount": 100},
        {"modality": "VIDEO", "tokenCount": 2800},
        {"modality": "AUDIO", "tokenCount": 100},
    ],
}


class FakeGemini:
    def __init__(self, fail: bool = False):
        self.fail = fail

    def annotate(self, *, file_ref, prompt, schema, model):
        if self.fail:
            raise RuntimeError("boom")
        return {
            "text": json.dumps({"scenes": []}),
            "raw": {"usageMetadata": USAGE, "candidates": [{"finishReason": "STOP"}]},
        }


TCTX = TelemetryCtx(
    install_id="inst-test",
    archive_id="catdv:test",
    vertex_project="p",
    vertex_location="europe-west3",
)


async def _setup(db, tmp_path, *, kind=None):
    prompts = PromptsRepo()
    _, vid = await prompts.create_with_initial_version(
        db,
        name="t",
        description=None,
        body="describe scenes",
        target_map={"scenes": {"kind": "markers"}},
        output_schema={"type": "object"},
        model="gemini-2.5-flash-lite",
    )
    jobs = JobsRepo()
    f = tmp_path / "c1.mov"
    f.write_bytes(b"fake")
    job_id = await jobs.create_job(db, prompt_version_id=vid, clip_ids=[101], kind=kind)
    if kind == "studio":
        sruns = StudioRunsRepo()
        rid = await sruns.create_pending(
            db, prompt_version_id=vid, clip_id=101, model="gemini-2.5-flash-lite"
        )
        await sruns.attach_job(db, rid, job_id=job_id)
    return job_id, f


def _run_kwargs(db, files, gemini):
    return dict(
        db=db,
        archive=FakeArchive({101: {"name": "clip 101"}}),
        proxy_resolver=FakeResolver(files),
        ai_store=FakeAIStore(),
        gemini=gemini,
        event_bus=EventBus(),
        annotations_repo=AnnotationsRepo(),
        review_items_repo=ReviewItemsRepo(),
        jobs_repo=JobsRepo(),
        prompts_repo=PromptsRepo(),
        studio_runs_repo=StudioRunsRepo(),
        uploaded_clips_repo=UploadedClipsRepo(),
        run_telemetry_repo=RunTelemetryRepo(),
        telemetry_ctx=TCTX,
    )


@pytest.mark.asyncio
async def test_annotation_path_records_telemetry(db, tmp_path):
    job_id, f = await _setup(db, tmp_path, kind=None)
    await run_job(job_id=job_id, **_run_kwargs(db, {101: f}, FakeGemini()))
    cur = await db.execute(
        "SELECT kind, status, tokens_in, tokens_out, tokens_thinking, "
        "tokens_in_video, cost_usd, prompt_hash, media_kind, install_id, "
        "est_tokens_in, finish_reason, clip_name, prompt_chars_rendered FROM run_telemetry"
    )
    rows = await cur.fetchall()
    assert len(rows) == 1
    r = rows[0]
    assert r[0] == "annotation" and r[1] == "ok"
    assert (r[2], r[3], r[4], r[5]) == (3000, 100, 40, 2800)
    assert r[6] is not None and r[6] > 0  # cost computed
    assert len(r[7]) == 64  # prompt_hash of TEMPLATE
    assert r[8] == "video+audio"
    assert r[9] == "inst-test"
    assert r[10] is not None and r[10] > 0  # est stamped pre-call
    assert r[11] == "STOP"
    assert r[12] == "clip 101"
    assert r[13] is not None and r[13] > 0  # prompt_chars_rendered populated


@pytest.mark.asyncio
async def test_studio_path_billable_tokens_and_cost(db, tmp_path):
    job_id, f = await _setup(db, tmp_path, kind="studio")
    await run_job(job_id=job_id, **_run_kwargs(db, {101: f}, FakeGemini()))
    cur = await db.execute("SELECT tokens_out, cost_usd FROM studio_run")
    out, cost = await cur.fetchone()
    assert out == 140  # candidates 100 + thinking 40 (billable)
    assert cost is not None and cost > 0
    cur = await db.execute("SELECT kind, status FROM run_telemetry")
    assert (await cur.fetchone()) == ("studio", "ok")


@pytest.mark.asyncio
async def test_failed_run_records_error_row(db, tmp_path):
    job_id, f = await _setup(db, tmp_path, kind=None)
    await run_job(job_id=job_id, **_run_kwargs(db, {101: f}, FakeGemini(fail=True)))
    cur = await db.execute("SELECT status, error_class, model, cost_usd FROM run_telemetry")
    row = await cur.fetchone()
    assert row == ("error", "RuntimeError", "gemini-2.5-flash-lite", None)


@pytest.mark.asyncio
async def test_telemetry_insert_failure_does_not_fail_run(db, tmp_path, monkeypatch):
    job_id, f = await _setup(db, tmp_path, kind=None)

    async def _boom(self, conn, rec):
        raise RuntimeError("telemetry db broken")

    monkeypatch.setattr(RunTelemetryRepo, "insert", _boom)
    await run_job(job_id=job_id, **_run_kwargs(db, {101: f}, FakeGemini()))
    items = await JobsRepo().list_items(db, job_id)
    assert items[0].status == "review_ready"  # run still succeeded


class FakeGeminiNonJson:
    def annotate(self, *, file_ref, prompt, schema, model):
        return {
            "text": "definitely not json",
            "raw": {"usageMetadata": USAGE, "candidates": [{"finishReason": "STOP"}]},
        }


@pytest.mark.asyncio
async def test_studio_non_json_records_error_telemetry(db, tmp_path):
    job_id, f = await _setup(db, tmp_path, kind="studio")
    await run_job(job_id=job_id, **_run_kwargs(db, {101: f}, FakeGeminiNonJson()))
    cur = await db.execute(
        "SELECT kind, status, error_class, cost_usd, tokens_in FROM run_telemetry"
    )
    rows = await cur.fetchall()
    assert len(rows) == 1  # exactly one row — no double-record
    assert rows[0] == ("studio", "error", "NonJsonOutput", None, 3000)
