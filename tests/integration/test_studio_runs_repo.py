import json

import pytest
import pytest_asyncio

from backend.app.repositories.prompts import PromptsRepo
from backend.app.repositories.studio_runs import StudioRunsRepo
from backend.app.repositories.testbench_items import TestbenchItemsRepo
from backend.app.repositories.testbenches import TestbenchesRepo


@pytest_asyncio.fixture
async def setup(db):
    prompts = PromptsRepo()
    _, pv_id = await prompts.create_with_initial_version(
        db, name="p", description=None, body="hi",
        target_map={}, output_schema={}, model="m", initial_state="production",
    )
    tb = await TestbenchesRepo().create(db, name="tb", description=None)
    folder = await TestbenchesRepo().create_folder(
        db, testbench_id=tb, parent_id=None, name="r"
    )
    item = await TestbenchItemsRepo().add_upload(
        db, folder_id=folder, upload_path="u.mp4", original_name="u.mp4"
    )
    return dict(pv_id=pv_id, tb=tb, item=item)


@pytest.mark.asyncio
async def test_create_run_starts_pending(db, setup):
    repo = StudioRunsRepo()
    rid = await repo.create(db, testbench_id=setup["tb"], prompt_version_id=setup["pv_id"])
    run = await repo.get(db, rid)
    assert run.status == "pending"
    assert run.started_at is None


@pytest.mark.asyncio
async def test_status_transitions_and_timestamps(db, setup):
    repo = StudioRunsRepo()
    rid = await repo.create(db, testbench_id=setup["tb"], prompt_version_id=setup["pv_id"])
    await repo.update_status(db, rid, "running", started=True)
    r = await repo.get(db, rid)
    assert r.status == "running" and r.started_at is not None
    await repo.update_status(db, rid, "completed", finished=True)
    r = await repo.get(db, rid)
    assert r.status == "completed" and r.finished_at is not None


@pytest.mark.asyncio
async def test_upsert_run_item_and_status(db, setup):
    repo = StudioRunsRepo()
    rid = await repo.create(db, testbench_id=setup["tb"], prompt_version_id=setup["pv_id"])
    item_id = await repo.upsert_item(db, run_id=rid, testbench_item_id=setup["item"])
    await repo.update_item_status(db, item_id, "resolving")
    items = await repo.list_items(db, rid)
    assert items[0].status == "resolving"


@pytest.mark.asyncio
async def test_attach_output(db, setup):
    repo = StudioRunsRepo()
    rid = await repo.create(db, testbench_id=setup["tb"], prompt_version_id=setup["pv_id"])
    iid = await repo.upsert_item(db, run_id=rid, testbench_item_id=setup["item"])
    await repo.attach_output(
        db, iid,
        structured_json=json.dumps({"k": "v"}),
        raw_text='{"k":"v"}', prompt_used="rendered", model="m", latency_ms=1234,
    )
    items = await repo.list_items(db, rid)
    assert items[0].structured_json == '{"k": "v"}'
    assert items[0].latency_ms == 1234
    assert items[0].status == "done"


@pytest.mark.asyncio
async def test_mark_unacceptable(db, setup):
    repo = StudioRunsRepo()
    rid = await repo.create(db, testbench_id=setup["tb"], prompt_version_id=setup["pv_id"])
    iid = await repo.upsert_item(db, run_id=rid, testbench_item_id=setup["item"])
    await repo.update_item_status(db, iid, "unacceptable", unacceptable_reason="no media")
    items = await repo.list_items(db, rid)
    assert items[0].status == "unacceptable"
    assert items[0].unacceptable_reason == "no media"


@pytest.mark.asyncio
async def test_reset_transient_sweeps_running_runs(db, setup):
    repo = StudioRunsRepo()
    rid = await repo.create(db, testbench_id=setup["tb"], prompt_version_id=setup["pv_id"])
    iid = await repo.upsert_item(db, run_id=rid, testbench_item_id=setup["item"])
    await repo.update_status(db, rid, "running", started=True)
    await repo.update_item_status(db, iid, "prompting")

    swept = await repo.reset_transient(db)
    assert swept >= 1
    r = await repo.get(db, rid)
    assert r.status == "failed"
    items = await repo.list_items(db, rid)
    assert items[0].status == "error"
    assert "interrupted" in (items[0].error or "")


@pytest.mark.asyncio
async def test_unique_run_item_pair(db, setup):
    repo = StudioRunsRepo()
    rid = await repo.create(db, testbench_id=setup["tb"], prompt_version_id=setup["pv_id"])
    await repo.upsert_item(db, run_id=rid, testbench_item_id=setup["item"])
    await repo.upsert_item(db, run_id=rid, testbench_item_id=setup["item"])
    items = await repo.list_items(db, rid)
    assert len(items) == 1


@pytest.mark.asyncio
async def test_list_for_testbench_orders_newest_first(db, setup):
    repo = StudioRunsRepo()
    a = await repo.create(db, testbench_id=setup["tb"], prompt_version_id=setup["pv_id"])
    b = await repo.create(db, testbench_id=setup["tb"], prompt_version_id=setup["pv_id"])
    runs = await repo.list_for_testbench(db, setup["tb"])
    assert [r.id for r in runs[:2]] == [b, a]
