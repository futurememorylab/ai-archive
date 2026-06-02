import asyncio
import importlib
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from backend.app.repositories.jobs import JobsRepo
from backend.app.repositories.prompts import PromptsRepo
from tests._helpers.live_ctx import install_live_ctx


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


async def _seed_batch(ctx):
    prompts = PromptsRepo()
    _, vid = await prompts.create_with_initial_version(
        ctx.db, name="Scénické značky CZ", description=None, body="p",
        target_map={"x": {"kind": "markers"}}, output_schema={}, model="gemini-2.5-pro",
    )
    jobs = JobsRepo()
    jid = await jobs.create_job(ctx.db, prompt_version_id=vid, clip_ids=[101, 102], run_group="rg-1")
    its = await jobs.list_items(ctx.db, jid)
    await jobs.update_item_status(ctx.db, its[0].id, "review_ready")
    await jobs.update_item_status(ctx.db, its[1].id, "error", error="ProxyNotFound")
    return jid


def test_batches_page_renders(monkeypatch, tmp_path):
    with _make_client(monkeypatch, tmp_path) as client:
        asyncio.run(_seed_batch(client.app.state.core_ctx))
        r = client.get("/batches")
        assert r.status_code == 200
        assert "<!doctype html>" in r.text.lower()
        assert "Scénické značky CZ" in r.text
        assert "gemini-2.5-pro" in r.text
        # rail marks Batches active
        assert 'title="Batches"' in r.text
        assert "rail-btn active" in r.text
        # failed count surfaced
        assert "1 failed" in r.text


def test_batches_table_partial(monkeypatch, tmp_path):
    with _make_client(monkeypatch, tmp_path) as client:
        asyncio.run(_seed_batch(client.app.state.core_ctx))
        r = client.get("/batches/table")
        assert r.status_code == 200
        assert "<!doctype html>" not in r.text.lower()
        assert "Scénické značky CZ" in r.text


def test_batches_page_empty_state(monkeypatch, tmp_path):
    with _make_client(monkeypatch, tmp_path) as client:
        r = client.get("/batches")
        assert r.status_code == 200
        assert "No batches yet" in r.text


def test_retry_failed_503_when_offline(monkeypatch, tmp_path):
    with _make_client(monkeypatch, tmp_path) as client:
        jid = asyncio.run(_seed_batch(client.app.state.core_ctx))
        # No live_ctx installed → get_live_ctx raises 503
        r = client.post("/batches/retry-failed", json={"job_ids": [jid]})
        assert r.status_code == 503


def test_retry_failed_starts_only_jobs_with_failures(monkeypatch, tmp_path):
    with _make_client(monkeypatch, tmp_path) as client:
        jid = asyncio.run(_seed_batch(client.app.state.core_ctx))
        install_live_ctx(client.app, proxy_resolver=MagicMock())  # online + resolver present

        started: list[int] = []
        import backend.app.routes.batches as batches_mod

        monkeypatch.setattr(
            batches_mod, "start_job_in_background",
            lambda core, live, job_id, **kw: started.append(job_id),
        )
        r = client.post("/batches/retry-failed", json={"job_ids": [jid]})
        assert r.status_code == 200
        assert started == [jid]
        assert r.json()["started"] == [jid]
