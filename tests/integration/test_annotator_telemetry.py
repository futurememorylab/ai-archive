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


async def _setup(db, tmp_path, *, kind=None, media_resolution=None, model="gemini-2.5-flash-lite"):
    prompts = PromptsRepo()
    _, vid = await prompts.create_with_initial_version(
        db,
        name="t",
        description=None,
        body="describe scenes",
        target_map={"scenes": {"kind": "markers"}},
        output_schema={"type": "object"},
        model=model,
        media_resolution=media_resolution,
    )
    jobs = JobsRepo()
    f = tmp_path / "c1.mov"
    f.write_bytes(b"fake")
    job_id = await jobs.create_job(db, prompt_version_id=vid, clip_ids=[101], kind=kind)
    if kind == "studio":
        sruns = StudioRunsRepo()
        rid = await sruns.create_pending(
            db, prompt_version_id=vid, clip_id=101, model=model
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
    # both the gemini call and the telemetry row. Uses an IMAGE clip because
    # HIGH is only valid for stills — on video it would (correctly) downgrade.
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

    f = tmp_path / "c1.jpg"
    f.write_bytes(b"fake")
    job_id, _ = await _setup(db, tmp_path, kind=None)
    archive = FakeArchiveMedia(
        {101: {"name": "clip", "media_path": "clip-101.jpg", "mime_type": "image/jpeg"}}
    )
    gemini = FakeGeminiCapturing()
    await run_job(job_id=job_id, **_high_default_kwargs(db, {101: f}, gemini, archive))

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
async def test_force_resolution_invalid_value_falls_back_not_keyerror(db, tmp_path):
    # L1: a bogus force_resolution (e.g. a stale/garbage calibration value)
    # must be routed through the validator, falling back to the default
    # 'medium' — NOT assigned raw, which would KeyError at the gemini SDK
    # map (_SDK_MEDIA_RESOLUTION[media_resolution]) outside any try/except.
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

    job_id, f = await _setup(db, tmp_path, kind=None)
    gemini = FakeGeminiCapturing()
    # Must not raise — a valid resolution must reach gemini, not "bogus".
    await run_job(
        job_id=job_id,
        **_run_kwargs(db, {101: f}, gemini),
        force_resolution="bogus",
    )

    assert gemini.media_resolution == "medium"  # validated fallback, not "bogus"

    cur = await db.execute("SELECT media_resolution_setting FROM run_telemetry")
    row = await cur.fetchone()
    assert row == ("medium",)  # records a valid setting, never the raw bad value


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


@pytest.mark.asyncio
async def test_record_only_non_json_output_recorded_as_error(db, tmp_path):
    # Bug M1: a record_only (calibration) run whose Gemini output does not
    # parse to JSON (structured is None) must be recorded as status='error'
    # with the SAME error_class the studio finalize path uses — NOT 'ok'.
    # Otherwise calibration per-resolution stats / the estimator's learned
    # output-rates (which only count status='ok' rows) count a garbage run as
    # a clean sample.
    job_id, f = await _setup(db, tmp_path, kind="studio")
    await run_job(
        job_id=job_id,
        **_run_kwargs(db, {101: f}, FakeGeminiNonJson()),
        record_only=True,
    )

    cur = await db.execute(
        "SELECT status, error_class FROM run_telemetry WHERE job_id = ?", (job_id,)
    )
    rows = await cur.fetchall()
    assert len(rows) == 1
    assert rows[0] == ("error", "NonJsonOutput")

    # The per-resolution ok-count must exclude this run.
    cur = await db.execute(
        "SELECT COUNT(*) FROM run_telemetry WHERE job_id = ? AND status = 'ok'",
        (job_id,),
    )
    assert (await cur.fetchone())[0] == 0

    # The item is marked failed, not review_ready.
    items = await JobsRepo().list_items(db, job_id)
    assert items[0].status == "error"


@pytest.mark.asyncio
async def test_record_only_error_row_carries_resolution(db, tmp_path):
    # Bug M3: when the Gemini call RAISES mid-call, the error telemetry row
    # recorded by run_job's except block must still carry the resolved
    # media_resolution_setting (here forced to 'low'), not NULL — otherwise
    # calibration error rows are resolution-blind.
    job_id, f = await _setup(db, tmp_path, kind="studio")
    await run_job(
        job_id=job_id,
        **_run_kwargs(db, {101: f}, FakeGemini(fail=True)),
        record_only=True,
        force_resolution="low",
    )

    cur = await db.execute(
        "SELECT status, media_resolution_setting FROM run_telemetry WHERE job_id = ?",
        (job_id,),
    )
    row = await cur.fetchone()
    assert row == ("error", "low")


# --- Fix 1/2/3 regression coverage --------------------------------------


class FakeArchiveMedia:
    """Like FakeArchive but lets each clip declare its media path, so the
    worker classifies the media kind (image vs video) the way it would in
    production. ``upstream_handle`` drives ``classify_media_kind``."""

    def __init__(self, clips: dict[int, dict]):
        self.clips = clips

    async def get_clip(self, clip_id_str: str):
        import datetime as _dt

        from backend.app.archive.model import CanonicalClip, MediaRef

        clip = self.clips[int(clip_id_str)]
        return CanonicalClip(
            key=("catdv", clip_id_str),
            name=clip.get("name", ""),
            duration_secs=float(clip.get("duration_secs") or 0.0),
            fps=float(clip.get("fps") or 25.0),
            markers=tuple(),
            fields={},
            notes={},
            media=MediaRef(
                mime_type=clip.get("mime_type", "video/quicktime"),
                size_bytes=None,
                cached_path=None,
                upstream_handle=clip.get("media_path", clip_id_str),
            ),
            provider_data=clip,
            fetched_at=_dt.datetime.now(_dt.UTC),
        )


def _high_default_kwargs(db, files, gemini, archive):
    kw = _run_kwargs(db, files, gemini)
    kw["archive"] = archive
    return kw


async def _seed_high_model(db):
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


@pytest.mark.asyncio
async def test_high_resolution_downgraded_for_video(db, tmp_path):
    # Model default = 'high', a VIDEO clip, normal run (no force_resolution).
    # Vertex rejects HIGH for non-image media → the worker must downgrade to
    # 'medium' before the gemini call AND record 'medium' in telemetry.
    await _seed_high_model(db)
    f = tmp_path / "c1.mov"
    f.write_bytes(b"fake")
    job_id, _ = await _setup(db, tmp_path, kind=None)
    archive = FakeArchiveMedia({101: {"name": "clip", "media_path": "clip-101.mov"}})
    gemini = FakeGeminiCapturing()
    await run_job(job_id=job_id, **_high_default_kwargs(db, {101: f}, gemini, archive))

    assert gemini.media_resolution == "medium"
    cur = await db.execute("SELECT media_resolution_setting FROM run_telemetry")
    assert (await cur.fetchone()) == ("medium",)


@pytest.mark.asyncio
async def test_high_resolution_kept_for_image(db, tmp_path):
    # Same 'high' model default, but an IMAGE clip — HIGH is valid for stills,
    # so it must pass through untouched to gemini and telemetry.
    await _seed_high_model(db)
    f = tmp_path / "c1.jpg"
    f.write_bytes(b"fake")
    job_id, _ = await _setup(db, tmp_path, kind=None)
    archive = FakeArchiveMedia(
        {101: {"name": "clip", "media_path": "clip-101.jpg", "mime_type": "image/jpeg"}}
    )
    gemini = FakeGeminiCapturing()
    await run_job(job_id=job_id, **_high_default_kwargs(db, {101: f}, gemini, archive))

    assert gemini.media_resolution == "high"
    cur = await db.execute("SELECT media_resolution_setting FROM run_telemetry")
    assert (await cur.fetchone()) == ("high",)


@pytest.mark.asyncio
async def test_inrun_estimate_uses_resolved_resolution(db, tmp_path):
    # The pre-call estimate must run AFTER media_resolution is resolved and be
    # passed the resolved value. We assert no regression from the reorder: the
    # run completes green, telemetry carries both the est_* fields and the
    # (downgraded) media_resolution_setting, and the estimate received it.
    import backend.app.services.run_estimator as run_estimator

    captured: dict = {}
    orig = run_estimator.estimate_clips

    async def _spy(*args, **kwargs):
        captured["media_resolution"] = kwargs.get("media_resolution")
        return await orig(*args, **kwargs)

    run_estimator.estimate_clips = _spy
    try:
        await _seed_high_model(db)
        f = tmp_path / "c1.mov"
        f.write_bytes(b"fake")
        job_id, _ = await _setup(db, tmp_path, kind=None)
        archive = FakeArchiveMedia({101: {"name": "clip", "media_path": "clip-101.mov"}})
        gemini = FakeGeminiCapturing()
        await run_job(job_id=job_id, **_high_default_kwargs(db, {101: f}, gemini, archive))
    finally:
        run_estimator.estimate_clips = orig

    # The estimate saw the resolved (downgraded) resolution, not None.
    assert captured["media_resolution"] == "medium"

    cur = await db.execute(
        "SELECT est_tokens_in, media_resolution_setting, status FROM run_telemetry"
    )
    row = await cur.fetchone()
    assert row[0] is not None and row[0] > 0  # est stamped
    assert row[1] == "medium"
    assert row[2] == "ok"


@pytest.mark.asyncio
async def test_finalize_studio_null_cost_when_no_rate_card(db, tmp_path):
    # A studio run for a model with NO rate card → compute_cost returns None →
    # studio_run.cost_usd must be NULL, not 0.0 (which would be indistinguishable
    # from a genuinely free run). 'no-card-model' is absent from SEED_RATE_CARDS.
    job_id, f = await _setup(db, tmp_path, kind="studio", model="no-card-model")
    await run_job(job_id=job_id, **_run_kwargs(db, {101: f}, FakeGemini()))
    cur = await db.execute("SELECT cost_usd FROM studio_run")
    assert (await cur.fetchone())[0] is None
