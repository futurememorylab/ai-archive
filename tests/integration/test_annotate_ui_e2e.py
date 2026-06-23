"""End-to-end test for Task 17: run the real `run_job` orchestrator with fakes
and assert the rendered Draft on /clips/{id} reflects the canned Gemini output.

The fakes (FakeArchive, FakeResolver, FakeAIStore) are imported from
``tests.integration.test_annotator_worker`` to avoid duplication. The
``FakeGeminiStructured`` here is the same shape as the inline one in that
module's first test, lifted to module scope so it can take a canned payload.

The HTTP-side pattern mirrors ``tests/integration/test_clip_detail_draft.py``:
build a ``TestClient`` (which sets up ``ctx.db`` against a tmp ``DATA_DIR``),
do all seeding + ``run_job`` against that same ``ctx.db`` via an
``asyncio.new_event_loop`` helper, then GET ``/clips/101`` and assert.
"""

import asyncio
import importlib
import json

from fastapi.testclient import TestClient

from backend.app.services.annotator import run_job
from backend.app.services.events import EventBus
from tests.integration.test_annotator_worker import (
    FakeAIStore,
    FakeArchive,
    FakeResolver,
)


class FakeGeminiStructured:
    """Returns a canned structured-output payload as if Gemini produced it."""

    def __init__(self, structured: dict):
        self._structured = structured

    def annotate(self, *, file_ref, prompt, schema, model, media_resolution=None):
        text = json.dumps(self._structured)
        return {"text": text, "raw": {"candidates": [{"text": text}]}}


def _setenv(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("CATDV_BASE_URL", "http://localhost:0")
    monkeypatch.setenv("CATDV_USERNAME", "")
    monkeypatch.setenv("CATDV_PASSWORD", "p")
    monkeypatch.setenv("CATDV_CATALOG_ID", "881507")
    monkeypatch.setenv("GCP_PROJECT_ID", "p")
    monkeypatch.setenv("GCS_BUCKET_NAME", "b")
    monkeypatch.setenv("PROXY_SOURCE", "rest")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))


def _make_app(monkeypatch, tmp_path):
    _setenv(monkeypatch, tmp_path)
    from backend.app import main as main_mod

    importlib.reload(main_mod)
    return main_mod.app


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


async def _seed_and_run(ctx, archive, proxy_path):
    """Seed a prompt + production version, create a one-clip job, and run it."""
    prompt_id, vid = await ctx.prompts_repo.create_with_initial_version(
        ctx.db,
        name="Decade tagger",
        description=None,
        body="describe scenes and tag the decade",
        target_map={
            "scenes": {"kind": "markers"},
            "decade": {
                "kind": "field",
                "identifier": "pragafilm.dekáda.natočení",
            },
        },
        output_schema={"type": "object"},
        model="gemini-2.5-pro",
    )
    await ctx.prompts_repo.promote_version(ctx.db, prompt_id=prompt_id, version_id=vid)

    job_id = await ctx.jobs_repo.create_job(ctx.db, prompt_version_id=vid, clip_ids=[101])

    structured = {
        "scenes": [
            {
                "name": "Scene-1",
                "in": {"frm": 0, "secs": 0.0},
                "out": {"frm": 25, "secs": 1.0},
            },
        ],
        "decade": "30.léta",
    }

    await run_job(
        db=ctx.db,
        job_id=job_id,
        archive=archive,
        proxy_resolver=FakeResolver({101: proxy_path}),
        ai_store=FakeAIStore(),
        gemini=FakeGeminiStructured(structured),
        event_bus=EventBus(),
        annotations_repo=ctx.annotations_repo,
        review_items_repo=ctx.review_items_repo,
        jobs_repo=ctx.jobs_repo,
        prompts_repo=ctx.prompts_repo,
        studio_runs_repo=ctx.studio_runs_repo,
        uploaded_clips_repo=ctx.uploaded_clips_repo,
        run_telemetry_repo=ctx.run_telemetry_repo,
        telemetry_ctx=ctx.telemetry_ctx,
        model_config_repo=ctx.model_config_repo,
    )


def test_end_to_end_renders_draft_with_gemini_output(monkeypatch, tmp_path):
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        from tests._helpers.live_ctx import install_live_ctx

        ctx = client.app.state.core_ctx
        archive = FakeArchive({101: {"ID": 101, "name": "Clip_101", "markers": []}})
        install_live_ctx(client.app, archive=archive)

        proxy = tmp_path / "101.mov"
        proxy.write_bytes(b"X" * 100)

        _run(_seed_and_run(ctx, archive, proxy))

        r = client.get("/clips/101")
        assert r.status_code == 200
        # The page loads without error and the clip name appears in the title
        assert "Clip_101" in r.text
        # Marker name appears in the inlined x-data JSON (ASCII — no escaping)
        assert "Scene-1" in r.text

        # The redesigned Draft panel is Alpine-data-driven: the server page inlines
        # draft arrays as JSON in x-data, and after an annotate run swapDraft()
        # calls reviewMixin.refreshDraft() which hits this JSON endpoint.
        # Assert the draft-data endpoint carries the canned Gemini output —
        # this is the canonical path for both initial render and post-run refresh.
        rd = client.get("/api/review/clips/101/draft-data")
        assert rd.status_code == 200
        data = rd.json()
        # Marker from the canned structured output
        marker_names = [m["name"] for m in data["markers"]]
        assert "Scene-1" in marker_names
        # Field identifier + value from the canned structured output
        field_ids = [f["identifier"] for f in data["fields"]]
        assert "pragafilm.dekáda.natočení" in field_ids
        field_values = [f["value"] for f in data["fields"]]
        assert "30.léta" in field_values
        # Prompt name is stored on the annotation and retrievable; the draft-data
        # arrays are non-empty, meaning the annotation was persisted correctly.
        assert len(data["markers"]) > 0 or len(data["fields"]) > 0
