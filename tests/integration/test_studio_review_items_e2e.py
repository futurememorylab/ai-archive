"""End-to-end: nested Gemini JSON → annotator → review_items → /studio/_run.

Confirms Option A wiring: the studio output card renders markers/fields
sourced from review_items (linked by studio_run_id), through the same
build_draft_view pipeline clip-detail uses.
"""

import asyncio
import importlib
import json
from unittest.mock import AsyncMock, MagicMock

import aiosqlite
import pytest
from fastapi.testclient import TestClient


def _new_event_loop_run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@pytest.fixture
def app_and_client(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.delenv("CATDV_PASSWORD", raising=False)
    from backend.app import main as main_mod

    importlib.reload(main_mod)
    with TestClient(main_mod.app) as c:
        yield main_mod.app, c


def test_studio_render_after_run_shows_markers_and_fields(app_and_client):
    app, client = app_and_client
    ctx = app.state.core_ctx
    db_path = ctx.settings.data_dir / "app.db"

    # Seed: prompt with a markers + field + note target_map; a draft version
    async def _seed():
        async with aiosqlite.connect(db_path) as db:
            await db.execute(
                "INSERT INTO prompts(id, name, description, archived, "
                "created_at, updated_at) "
                "VALUES (9001, 'studio-e2e', NULL, 0, '2026-05-28T00:00:00Z', "
                "'2026-05-28T00:00:00Z')"
            )
            await db.execute(
                "INSERT INTO prompt_versions(id, prompt_id, version_num, state, body, "
                "target_map, output_schema, model, created_at, updated_at) "
                "VALUES (9001, 9001, 1, 'draft', 'do x', ?, '{}', 'gemini-2.5-pro', "
                "'2026-05-28T00:00:00Z', '2026-05-28T00:00:00Z')",
                (
                    json.dumps(
                        {
                            "scenes": {"kind": "markers"},
                            "decade": {"kind": "field", "identifier": "pragafilm.dekada"},
                            "summary_cz": {"kind": "note", "target": "pragafilm.popis"},
                        }
                    ),
                ),
            )
            cur = await db.execute(
                "INSERT INTO studio_run(prompt_version_id, clip_id, status, model) "
                "VALUES (9001, 42, 'pending', 'gemini-2.5-pro')"
            )
            run_id = cur.lastrowid
            cur2 = await db.execute(
                "INSERT INTO jobs(prompt_version_id, status, kind, created_at, "
                "total_clips) VALUES (9001, 'pending', 'studio', "
                "'2026-05-28T00:00:00Z', 1)"
            )
            job_id = cur2.lastrowid
            await db.execute(
                "INSERT INTO job_items(job_id, catdv_clip_id, status) VALUES (?, 42, 'pending')",
                (job_id,),
            )
            await db.execute("UPDATE studio_run SET job_id = ? WHERE id = ?", (job_id, run_id))
            await db.commit()
            return run_id, job_id

    run_id, job_id = _new_event_loop_run(_seed())

    # Stub the externals: AI store says "already uploaded"; gemini returns
    # the REAL nested-secs shape; archive returns a fake clip with duration.
    ctx.ai_store = MagicMock()
    ctx.ai_store.status = AsyncMock(return_value=MagicMock(handle="gs://x"))
    ctx.ai_store.reference_for_gemini = AsyncMock(return_value={"uri": "gs://x"})
    ctx.archive = MagicMock()
    ctx.archive.get_clip = AsyncMock(
        return_value=MagicMock(
            provider_data={"name": "clip-42"},
            duration_secs=10.0,
            fps=25.0,
        )
    )
    ctx.gemini = MagicMock()
    ctx.gemini.annotate = MagicMock(
        return_value={
            "text": json.dumps(
                {
                    "scenes": [
                        {"name": "scene-a", "in": {"secs": 1.0}, "out": {"secs": 4.0}},
                        {"name": "scene-b", "in": {"secs": 5.0}, "out": {"secs": 9.0}},
                    ],
                    "decade": {"value": "30.léta"},
                    "summary_cz": {"value": "krátký souhrn"},
                }
            ),
            "raw": {"usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 20}},
        }
    )
    ctx.proxy_resolver = MagicMock()

    # Run the annotator job synchronously
    from backend.app.services.annotator import run_job

    _new_event_loop_run(
        run_job(
            db=ctx.db,
            job_id=job_id,
            archive=ctx.archive,
            proxy_resolver=ctx.proxy_resolver,
            ai_store=ctx.ai_store,
            gemini=ctx.gemini,
            event_bus=ctx.event_bus,
            annotations_repo=ctx.annotations_repo,
            review_items_repo=ctx.review_items_repo,
            jobs_repo=ctx.jobs_repo,
            prompts_repo=ctx.prompts_repo,
            studio_runs_repo=ctx.studio_runs_repo,
            run_telemetry_repo=ctx.run_telemetry_repo,
            telemetry_ctx=ctx.telemetry_ctx,
        )
    )

    # Hit the studio output endpoint and assert markers + field render
    r = client.get("/studio/_run?prompt_version_id=9001&clip_id=42")
    assert r.status_code == 200
    html = r.text
    assert "scene-a" in html, "first marker name missing from rendered output"
    assert "scene-b" in html, "second marker name missing from rendered output"
    assert "pragafilm.dekada" in html, "field identifier missing"
    assert "30.léta" in html, "unwrapped field value missing"
    # _anno_panels marker articles include @click="seek(N)" — confirms
    # the shared partial rendered, not the bespoke ro-scene markup.
    assert '@click="seek(' in html
