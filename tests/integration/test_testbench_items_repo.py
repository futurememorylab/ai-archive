import json

import pytest
import pytest_asyncio

from backend.app.repositories.testbench_items import TestbenchItemsRepo
from backend.app.repositories.testbenches import TestbenchesRepo


@pytest_asyncio.fixture
async def folder(db):
    tb = await TestbenchesRepo().create(db, name="t", description=None)
    return await TestbenchesRepo().create_folder(db, testbench_id=tb, parent_id=None, name="root")


@pytest.mark.asyncio
async def test_add_upload_and_catdv(db, folder):
    repo = TestbenchItemsRepo()
    u = await repo.add_upload(db, folder_id=folder, upload_path="u.mp4", original_name="orig.mp4")
    c = await repo.add_catdv(db, folder_id=folder, provider_clip_id="999", name="catdv-999")
    items = await repo.list_for_folder(db, folder)
    by_id = {it.id: it for it in items}
    assert by_id[u].source_kind == "upload"
    assert by_id[u].upload_path == "u.mp4"
    assert by_id[c].source_kind == "catdv_clip"
    assert by_id[c].catdv_provider_clip_id == "999"


@pytest.mark.asyncio
async def test_set_gold_round_trip_preserves_unknown_keys(db, folder):
    repo = TestbenchItemsRepo()
    item = await repo.add_upload(db, folder_id=folder, upload_path="u.mp4", original_name="u.mp4")
    await repo.set_gold(db, item, {"description": "old", "future_field": [1, 2, 3]})
    fetched = (await repo.list_for_folder(db, folder))[0]
    assert json.loads(fetched.gold_json) == {"description": "old", "future_field": [1, 2, 3]}
    existing = json.loads(fetched.gold_json)
    existing["description"] = "new"
    await repo.set_gold(db, item, existing)
    fetched2 = (await repo.list_for_folder(db, folder))[0]
    assert json.loads(fetched2.gold_json) == {"description": "new", "future_field": [1, 2, 3]}


@pytest.mark.asyncio
async def test_clear_gold(db, folder):
    repo = TestbenchItemsRepo()
    item = await repo.add_upload(db, folder_id=folder, upload_path="u.mp4", original_name="u.mp4")
    await repo.set_gold(db, item, {"description": "x"})
    await repo.set_gold(db, item, None)
    fetched = (await repo.list_for_folder(db, folder))[0]
    assert fetched.gold_json is None


@pytest.mark.asyncio
async def test_list_for_testbench_tree_order(db):
    """Items return in folder DFS order, then sort_index within folder."""
    tb_repo = TestbenchesRepo()
    repo = TestbenchItemsRepo()
    tb = await tb_repo.create(db, name="t", description=None)
    root = await tb_repo.create_folder(db, testbench_id=tb, parent_id=None, name="root")
    sub = await tb_repo.create_folder(db, testbench_id=tb, parent_id=root, name="sub")
    a = await repo.add_upload(db, folder_id=root, upload_path="a.mp4", original_name="a.mp4")
    b = await repo.add_upload(db, folder_id=sub, upload_path="b.mp4", original_name="b.mp4")
    c = await repo.add_upload(db, folder_id=root, upload_path="c.mp4", original_name="c.mp4")
    items = await repo.list_for_testbench(db, tb)
    assert [it.id for it in items] == [a, c, b]


@pytest.mark.asyncio
async def test_remove(db, folder):
    repo = TestbenchItemsRepo()
    item = await repo.add_upload(db, folder_id=folder, upload_path="u.mp4", original_name="u.mp4")
    await repo.remove(db, item)
    assert await repo.list_for_folder(db, folder) == []
