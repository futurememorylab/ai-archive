import aiosqlite
import pytest

from backend.app.repositories.testbenches import TestbenchesRepo


@pytest.mark.asyncio
async def test_create_and_get(db):
    repo = TestbenchesRepo()
    tb_id = await repo.create(db, name="my-tb", description="d")
    tb = await repo.get(db, tb_id)
    assert tb.name == "my-tb"
    assert tb.archived is False


@pytest.mark.asyncio
async def test_unique_name(db):
    repo = TestbenchesRepo()
    await repo.create(db, name="dup", description=None)
    with pytest.raises(aiosqlite.IntegrityError):
        await repo.create(db, name="dup", description=None)


@pytest.mark.asyncio
async def test_archive_hides_from_list_active(db):
    repo = TestbenchesRepo()
    a = await repo.create(db, name="a", description=None)
    b = await repo.create(db, name="b", description=None)
    await repo.archive(db, a)
    listed = await repo.list_active(db)
    assert [t.id for t in listed] == [b]


@pytest.mark.asyncio
async def test_folder_tree_round_trip(db):
    repo = TestbenchesRepo()
    tb = await repo.create(db, name="x", description=None)
    root = await repo.create_folder(db, testbench_id=tb, parent_id=None, name="root")
    sub = await repo.create_folder(db, testbench_id=tb, parent_id=root, name="sub")
    folders = await repo.list_folders(db, tb)
    by_id = {f.id: f for f in folders}
    assert by_id[root].parent_id is None
    assert by_id[sub].parent_id == root


@pytest.mark.asyncio
async def test_delete_folder_only_when_empty(db):
    """Folder with subfolder or items present → must refuse."""
    from backend.app.repositories.testbench_items import TestbenchItemsRepo
    repo = TestbenchesRepo()
    items = TestbenchItemsRepo()
    tb = await repo.create(db, name="t", description=None)
    f1 = await repo.create_folder(db, testbench_id=tb, parent_id=None, name="f1")
    await items.add_upload(db, folder_id=f1, upload_path="a.mp4", original_name="a.mp4")
    with pytest.raises(ValueError, match="not empty"):
        await repo.delete_folder(db, f1)


@pytest.mark.asyncio
async def test_archive_flag_persists(db):
    repo = TestbenchesRepo()
    tb = await repo.create(db, name="t", description=None)
    await repo.archive(db, tb)
    assert (await repo.get(db, tb)).archived is True
