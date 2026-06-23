"""Both finalize paths write run_telemetry; studio_run.tokens_out is
billable (candidates + thinking); cost_usd computed; errors recorded."""

import json

import pytest

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

    def annotate(self, *, file_ref, prompt, schema, model, media_resolution=None):
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


async def _setup(db, tmp_path, *, kind=None, media_resolution=None):
    prompts = PromptsRepo()
    _, vid = await prompts.create_with_initial_version(
        db,
        name="t",
        description=None,
        body="describe scenes",
        target_map={"scenes": {"kind": "markers"}},
        output_schema={"type": "object"},
        model="gemini-2.5-flash-lite",
        media_resolution=media_resolution,
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
        model_config_repo=ModelConfigRepo(),
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


class FakeGeminiCapturing:
    """Records the media_resolution it was called with."""

    def __init__(self):
        self.media_resolution = "UNSET"

    def annotate(self, *, file_ref, prompt, schema, model, media_resolution=None):
        self.media_resolution = media_resolution
        return {
            "text": json.dumps({"scenes": []}),
            "raw": {"usageMetadata": USAGE, "candidates": [{"finishReason": "STOP"}]},
        }


@pytest.mark.asyncio
async def test_media_resolution_setting_from_model_default(db, tmp_path):
    # Seed the model's default media resolution to 'high'; the version has no
    # override, so the effective resolution should resolve to 'high' and reach
    # both the gemini call and the telemetry row.
    mc = ModelConfigRepo()
    await mc.set_rates(
        db,
        "gemini-2.5-flash-lite",
        input_text_video_image_per_1m=0.1,
        input_audio_per_1m=0.1,
        input_cached_per_1m=0.1,
        output_per_1m=0.1,
        pricing_version="test",
        commit=True,
    )
    await mc.set_resolution(db, "gemini-2.5-flash-lite", "high", commit=True)

    job_id, f = await _setup(db, tmp_path, kind=None)
    gemini = FakeGeminiCapturing()
    await run_job(job_id=job_id, **_run_kwargs(db, {101: f}, gemini))

    # The fake gemini received the resolved resolution.
    assert gemini.media_resolution == "high"

    # And the telemetry row captured it.
    cur = await db.execute("SELECT media_resolution_setting FROM run_telemetry")
    row = await cur.fetchone()
    assert row == ("high",)


@pytest.mark.asyncio
async def test_media_resolution_override_beats_model_default(db, tmp_path):
    # Seed the model default to 'high', but give the prompt VERSION an explicit
    # 'low' override. The override must win all the way through to the gemini
    # call AND the telemetry row — the 'high' model default is shadowed.
    mc = ModelConfigRepo()
    await mc.set_rates(
        db,
        "gemini-2.5-flash-lite",
        input_text_video_image_per_1m=0.1,
        input_audio_per_1m=0.1,
        input_cached_per_1m=0.1,
        output_per_1m=0.1,
        pricing_version="test",
        commit=True,
    )
    await mc.set_resolution(db, "gemini-2.5-flash-lite", "high", commit=True)

    job_id, f = await _setup(db, tmp_path, kind=None, media_resolution="low")
    gemini = FakeGeminiCapturing()
    await run_job(job_id=job_id, **_run_kwargs(db, {101: f}, gemini))

    # The override reached the gemini SDK call.
    assert gemini.media_resolution == "low"

    # And the telemetry row captured the override, not the model default.
    cur = await db.execute("SELECT media_resolution_setting FROM run_telemetry")
    row = await cur.fetchone()
    assert row == ("low",)


@pytest.mark.asyncio
async def test_force_resolution_overrides_resolver(db, tmp_path):
    # Model default = 'high', version override = 'low'; force_resolution="medium"
    # must beat BOTH and reach the gemini call AND the telemetry row.
    mc = ModelConfigRepo()
    await mc.set_rates(
        db,
        "gemini-2.5-flash-lite",
        input_text_video_image_per_1m=0.1,
        input_audio_per_1m=0.1,
        input_cached_per_1m=0.1,
        output_per_1m=0.1,
        pricing_version="test",
        commit=True,
    )
    await mc.set_resolution(db, "gemini-2.5-flash-lite", "high", commit=True)

    job_id, f = await _setup(db, tmp_path, kind=None, media_resolution="low")
    gemini = FakeGeminiCapturing()
    await run_job(
        job_id=job_id,
        **_run_kwargs(db, {101: f}, gemini),
        force_resolution="medium",
    )

    # The forced resolution reached the gemini SDK call.
    assert gemini.media_resolution == "medium"

    # And the telemetry row captured the forced value.
    cur = await db.execute("SELECT media_resolution_setting FROM run_telemetry")
    row = await cur.fetchone()
    assert row == ("medium",)


@pytest.mark.asyncio
async def test_record_only_writes_telemetry_but_no_studio_or_review(db, tmp_path):
    # record_only=True on a studio-kind job: after the Gemini call we record
    # telemetry + mark the item done, but write NO studio-run completion and
    # NO review_items.
    job_id, f = await _setup(db, tmp_path, kind="studio")
    gemini = FakeGeminiCapturing()
    await run_job(
        job_id=job_id,
        **_run_kwargs(db, {101: f}, gemini),
        record_only=True,
    )

    # Exactly one telemetry row for the job, status 'ok', resolution set.
    cur = await db.execute(
        "SELECT status, media_resolution_setting FROM run_telemetry WHERE job_id = ?",
        (job_id,),
    )
    rows = await cur.fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "ok"
    assert rows[0][1] is not None

    # No review_items written for this clip.
    cur = await db.execute(
        "SELECT COUNT(*) FROM review_items WHERE catdv_clip_id = ?", (101,)
    )
    assert (await cur.fetchone())[0] == 0

    # The studio_run for this job/clip was NOT completed — it stays pending
    # (the finalize path that writes output_json + flips status to 'ok' was
    # skipped entirely).
    cur = await db.execute(
        "SELECT status, output_json FROM studio_run WHERE job_id = ?", (job_id,)
    )
    sr = await cur.fetchone()
    assert sr == ("pending", None)

    # The item was still marked successfully done.
    items = await JobsRepo().list_items(db, job_id)
    assert items[0].status == "review_ready"


class FakeGeminiNonJson:
    def annotate(self, *, file_ref, prompt, schema, model, media_resolution=None):
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
