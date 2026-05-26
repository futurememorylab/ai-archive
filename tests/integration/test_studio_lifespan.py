"""Tests for Studio lifespan startup: uploads dir creation + transient sweep."""
import importlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.repositories.studio_runs import StudioRunsRepo
from backend.app.repositories.testbenches import TestbenchesRepo
from backend.app.repositories.prompts import PromptsRepo


def _make_client(monkeypatch, tmp_path, uploads_subdir="uploads"):
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("CATDV_OFFLINE", "true")
    monkeypatch.setenv("CATDV_BASE_URL", "http://localhost:0")
    monkeypatch.setenv("CATDV_USERNAME", "")
    monkeypatch.setenv("CATDV_PASSWORD", "p")
    monkeypatch.setenv("CATDV_CATALOG_ID", "881507")
    monkeypatch.setenv("GCP_PROJECT_ID", "p")
    monkeypatch.setenv("GCS_BUCKET_NAME", "b")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("STUDIO_UPLOADS_DIR", str(tmp_path / uploads_subdir))
    from backend.app import main as main_mod
    importlib.reload(main_mod)
    return TestClient(main_mod.app)


def test_lifespan_creates_uploads_dir(monkeypatch, tmp_path):
    uploads = tmp_path / "uploads-x"
    assert not uploads.exists()
    with _make_client(monkeypatch, tmp_path, uploads_subdir="uploads-x") as client:
        # entering the context manager fired the lifespan
        ctx = client.app.state.ctx
        assert ctx.settings.studio_uploads_dir == uploads
        assert uploads.exists() and uploads.is_dir()


def test_lifespan_sweeps_running_studio_runs(monkeypatch, tmp_path):
    """Pre-seed a 'running' run + a transient item, restart the app, assert
    the run is now 'failed' and the item is 'error'."""
    import asyncio
    import aiosqlite

    # Stage 1: boot app once, leave a 'running' run + transient item, then close.
    with _make_client(monkeypatch, tmp_path) as client:
        ctx = client.app.state.ctx

        async def _seed():
            prompts = PromptsRepo()
            _, pv = await prompts.create_with_initial_version(
                ctx.db, name="p", description=None, body="b",
                target_map={}, output_schema={}, model="m",
                initial_state="production",
            )
            tb = await TestbenchesRepo().create(ctx.db, name="t", description=None)
            runs = StudioRunsRepo()
            rid = await runs.create(ctx.db, testbench_id=tb, prompt_version_id=pv)
            await runs.update_status(ctx.db, rid, "running", started=True)
            # Direct insert of a transient item via SQL — no helper for inserting
            # without a testbench_items reference.
            f = await TestbenchesRepo().create_folder(
                ctx.db, testbench_id=tb, parent_id=None, name="r"
            )
            from backend.app.repositories.testbench_items import TestbenchItemsRepo
            (tmp_path / "x.mp4").write_bytes(b"")  # placeholder; not actually loaded
            tb_item = await TestbenchItemsRepo().add_upload(
                ctx.db, folder_id=f, upload_path="x.mp4", original_name="x.mp4"
            )
            iid = await runs.upsert_item(ctx.db, run_id=rid, testbench_item_id=tb_item)
            await runs.update_item_status(ctx.db, iid, "prompting")
            return rid, iid

        rid, iid = asyncio.run(_seed())

    # Stage 2: boot a NEW app pointing at the same DB. The lifespan sweep runs.
    with _make_client(monkeypatch, tmp_path) as client:
        ctx = client.app.state.ctx

        async def _check():
            runs = StudioRunsRepo()
            run = await runs.get(ctx.db, rid)
            items = await runs.list_items(ctx.db, rid)
            return run.status, items[0].status, items[0].error

        status, item_status, item_error = asyncio.run(_check())
        assert status == "failed"
        assert item_status == "error"
        assert "interrupted" in (item_error or "")
