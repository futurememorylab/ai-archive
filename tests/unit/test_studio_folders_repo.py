"""StudioFoldersRepo — create/list/rename/delete folders + clip membership."""

import aiosqlite
import pytest
from pathlib import Path

from backend.app.db import open_db
from backend.app.migrations_runner import apply_migrations
from backend.app.repositories.studio_folders import StudioFoldersRepo


@pytest.fixture
async def db(tmp_path: Path):
    db_path = tmp_path / "test.db"
    cm = open_db(db_path)
    conn = await cm.__aenter__()
    await apply_migrations(conn, Path("backend/migrations"))
    yield conn
    await cm.__aexit__(None, None, None)


@pytest.mark.asyncio
async def test_create_and_list_folder(db: aiosqlite.Connection):
    repo = StudioFoldersRepo()
    fid = await repo.create_folder(db, name="edge_cases")
    rows = await repo.list_folders_with_counts(db)
    assert len(rows) == 1
    assert rows[0]["id"] == fid
    assert rows[0]["name"] == "edge_cases"
    assert rows[0]["clip_count"] == 0


@pytest.mark.asyncio
async def test_unique_folder_name(db):
    repo = StudioFoldersRepo()
    await repo.create_folder(db, name="x")
    with pytest.raises(aiosqlite.IntegrityError):
        await repo.create_folder(db, name="x")


@pytest.mark.asyncio
async def test_add_and_list_clips(db):
    repo = StudioFoldersRepo()
    fid = await repo.create_folder(db, name="f1")
    added = await repo.add_clips(db, fid, clip_ids=[12041, 12042, 12041])  # dedupe
    assert added == 2
    clips = await repo.list_clips(db, fid)
    assert sorted(c["clip_id"] for c in clips) == [12041, 12042]


@pytest.mark.asyncio
async def test_remove_clip(db):
    repo = StudioFoldersRepo()
    fid = await repo.create_folder(db, name="f1")
    await repo.add_clips(db, fid, clip_ids=[12041, 12042])
    await repo.remove_clip(db, fid, clip_id=12041)
    clips = await repo.list_clips(db, fid)
    assert [c["clip_id"] for c in clips] == [12042]


@pytest.mark.asyncio
async def test_rename_folder(db):
    repo = StudioFoldersRepo()
    fid = await repo.create_folder(db, name="old")
    await repo.rename_folder(db, fid, name="new")
    rows = await repo.list_folders_with_counts(db)
    assert rows[0]["name"] == "new"


@pytest.mark.asyncio
async def test_delete_folder_cascades_clips(db):
    repo = StudioFoldersRepo()
    fid = await repo.create_folder(db, name="f1")
    await repo.add_clips(db, fid, clip_ids=[12041])
    await repo.delete_folder(db, fid)
    rows = await repo.list_folders_with_counts(db)
    assert rows == []
    cur = await db.execute("SELECT COUNT(*) FROM studio_folder_clip WHERE folder_id = ?", (fid,))
    assert (await cur.fetchone())[0] == 0


@pytest.mark.asyncio
async def test_clip_count_reflects_membership(db):
    repo = StudioFoldersRepo()
    fid = await repo.create_folder(db, name="f1")
    await repo.add_clips(db, fid, clip_ids=[1, 2, 3])
    rows = await repo.list_folders_with_counts(db)
    assert rows[0]["clip_count"] == 3
