import asyncio
import importlib

from fastapi.testclient import TestClient

from backend.app.repositories.jobs import JobsRepo
from backend.app.repositories.prompts import PromptsRepo
from backend.app.repositories.review_items import ReviewItemsRepo


def _make_client(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("CATDV_BASE_URL", "http://localhost:0")
    monkeypatch.setenv("CATDV_USERNAME", "")
    monkeypatch.setenv("CATDV_PASSWORD", "p")
    monkeypatch.setenv("CATDV_CATALOG_ID", "7")
    monkeypatch.setenv("GCP_PROJECT_ID", "p")
    monkeypatch.setenv("GCS_BUCKET_NAME", "b")
    monkeypatch.setenv("PROXY_SOURCE", "rest")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from backend.app import main as main_mod

    importlib.reload(main_mod)
    return TestClient(main_mod.app)


async def _seed(ctx):
    prompts = PromptsRepo()
    _, vid = await prompts.create_with_initial_version(
        ctx.db, name="P", description=None, body="b",
        target_map={"x": {"kind": "markers"}}, output_schema={}, model="m",
    )
    jobs = JobsRepo()
    jid = await jobs.create_job(ctx.db, prompt_version_id=vid, clip_ids=[101, 102], run_group="rg-1")
    # annotation + pending review item for clip 101 (102 has none -> not pending)
    cur = await ctx.db.execute(
        "INSERT INTO annotations (catdv_clip_id, catdv_clip_name, prompt_version_id, job_id, "
        " model, prompt_used, raw_response, structured_output, clip_snapshot, created_at) "
        "VALUES (101, 'C101', ?, ?, 'm', 'p', '{}', '{}', '{}', '2026-06-02T00:00:00')",
        (vid, jid),
    )
    ann = cur.lastrowid
    await ctx.db.execute(
        "INSERT INTO review_items (annotation_id, studio_run_id, catdv_clip_id, kind, "
        " target_identifier, proposed_value, edited_value, decision, applied_at) "
        "VALUES (?, NULL, 101, 'marker', NULL, '{}', NULL, 'pending', NULL)",
        (ann,),
    )
    await ctx.db.commit()
    return jid


def test_pending_clip_ids_for_jobs(monkeypatch, tmp_path):
    with _make_client(monkeypatch, tmp_path) as client:
        ctx = client.app.state.core_ctx
        jid = asyncio.run(_seed(ctx))
        ids = asyncio.run(ReviewItemsRepo().pending_clip_ids_for_jobs(ctx.db, [jid]))
        assert ids == [101]
        assert asyncio.run(ReviewItemsRepo().pending_clip_ids_for_jobs(ctx.db, [])) == []


def test_review_queue_route(monkeypatch, tmp_path):
    with _make_client(monkeypatch, tmp_path) as client:
        jid = asyncio.run(_seed(client.app.state.core_ctx))
        r = client.get(f"/batches/review-queue?job_ids={jid}")
        assert r.status_code == 200
        assert r.json() == {"clip_ids": [101]}


def test_draft_data_route(monkeypatch, tmp_path):
    with _make_client(monkeypatch, tmp_path) as client:
        asyncio.run(_seed(client.app.state.core_ctx))
        r = client.get("/api/review/clips/101/draft-data")
        assert r.status_code == 200
        body = r.json()
        assert set(body.keys()) == {"markers", "fields", "notes"}
        # the seeded pending marker item is present as a "proposed" card
        assert any(m["status"] == "proposed" for m in body["markers"])
