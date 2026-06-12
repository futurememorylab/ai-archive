"""StudioSetsRepo — create/list/rename/delete sets + clip membership,
partitioned by source."""

from pathlib import Path

import aiosqlite
import pytest

from backend.app.db import open_db
from backend.app.migrations_runner import apply_migrations
from backend.app.repositories.studio_sets import DEFAULT_UPLOADED_SET_NAME, StudioSetsRepo


@pytest.fixture
async def db(tmp_path: Path):
    db_path = tmp_path / "test.db"
    cm = open_db(db_path)
    conn = await cm.__aenter__()
    await apply_migrations(conn, Path("backend/migrations"))
    yield conn
    await cm.__aexit__(None, None, None)


@pytest.mark.asyncio
async def test_create_and_list_set(db: aiosqlite.Connection):
    repo = StudioSetsRepo()
    sid = await repo.create_set(db, name="edge_cases")
    rows = await repo.list_sets_with_counts(db, source="archive")
    assert len(rows) == 1
    assert rows[0]["id"] == sid
    assert rows[0]["name"] == "edge_cases"
    assert rows[0]["source"] == "archive"
    assert rows[0]["clip_count"] == 0


@pytest.mark.asyncio
async def test_list_partitions_by_source(db):
    repo = StudioSetsRepo()
    await repo.create_set(db, name="a", source="archive")
    await repo.create_set(db, name="u", source="uploaded")
    archive = await repo.list_sets_with_counts(db, source="archive")
    uploaded = await repo.list_sets_with_counts(db, source="uploaded")
    assert [r["name"] for r in archive] == ["a"]
    assert [r["name"] for r in uploaded] == ["u"]


@pytest.mark.asyncio
async def test_unique_set_name_per_source(db):
    repo = StudioSetsRepo()
    await repo.create_set(db, name="x", source="archive")
    with pytest.raises(aiosqlite.IntegrityError):
        await repo.create_set(db, name="x", source="archive")
    # Same name in another source is allowed.
    await repo.create_set(db, name="x", source="uploaded")


@pytest.mark.asyncio
async def test_clip_total_for_source(db):
    repo = StudioSetsRepo()
    sid = await repo.create_set(db, name="a", source="archive")
    await repo.add_clips(db, sid, clip_ids=[1, 2, 3])
    assert await repo.clip_total_for_source(db, source="archive") == 3
    assert await repo.clip_total_for_source(db, source="uploaded") == 0


@pytest.mark.asyncio
async def test_add_and_list_clips(db):
    repo = StudioSetsRepo()
    sid = await repo.create_set(db, name="f1")
    added = await repo.add_clips(db, sid, clip_ids=[12041, 12042, 12041])  # dedupe
    assert added == 2
    clips = await repo.list_clips(db, sid)
    assert sorted(c["clip_id"] for c in clips) == [12041, 12042]


@pytest.mark.asyncio
async def test_remove_clip(db):
    repo = StudioSetsRepo()
    sid = await repo.create_set(db, name="f1")
    await repo.add_clips(db, sid, clip_ids=[12041, 12042])
    await repo.remove_clip(db, sid, clip_id=12041)
    clips = await repo.list_clips(db, sid)
    assert [c["clip_id"] for c in clips] == [12042]


@pytest.mark.asyncio
async def test_rename_set(db):
    repo = StudioSetsRepo()
    sid = await repo.create_set(db, name="old")
    await repo.rename_set(db, sid, name="new")
    rows = await repo.list_sets_with_counts(db, source="archive")
    assert rows[0]["name"] == "new"


@pytest.mark.asyncio
async def test_delete_set_cascades_clips(db):
    repo = StudioSetsRepo()
    sid = await repo.create_set(db, name="f1")
    await repo.add_clips(db, sid, clip_ids=[12041])
    await repo.delete_set(db, sid)
    rows = await repo.list_sets_with_counts(db, source="archive")
    assert rows == []
    cur = await db.execute("SELECT COUNT(*) FROM studio_set_clip WHERE set_id = ?", (sid,))
    assert (await cur.fetchone())[0] == 0


@pytest.mark.asyncio
async def test_set_id_for_clip(db):
    repo = StudioSetsRepo()
    sid = await repo.create_set(db, name="f1")
    await repo.add_clips(db, sid, clip_ids=[42])
    assert await repo.set_id_for_clip(db, 42) == sid
    assert await repo.set_id_for_clip(db, 999) is None


@pytest.mark.asyncio
async def test_get_or_create_default_uploaded_set_is_idempotent(db):
    repo = StudioSetsRepo()
    a = await repo.get_or_create_default_uploaded_set(db)
    b = await repo.get_or_create_default_uploaded_set(db)
    assert a == b
    sets = await repo.list_sets_with_counts(db, source="uploaded")
    assert [s["name"] for s in sets] == [DEFAULT_UPLOADED_SET_NAME]
