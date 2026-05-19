import pytest

from backend.app.repositories.workspaces import WorkspacesRepo


@pytest.mark.asyncio
async def test_create_and_get_workspace(db):
    repo = WorkspacesRepo()
    ws_id = await repo.create(
        db,
        name="train trip",
        provider_id="catdv",
        catalog_id="881507",
        description="30s home movies",
    )
    ws = await repo.get(db, ws_id)
    assert ws is not None
    assert ws["name"] == "train trip"
    assert ws["provider_id"] == "catdv"
    assert ws["description"] == "30s home movies"


@pytest.mark.asyncio
async def test_list_workspaces(db):
    repo = WorkspacesRepo()
    a = await repo.create(db, name="a", provider_id="catdv", catalog_id="1")
    b = await repo.create(db, name="b", provider_id="catdv", catalog_id="1")
    rows = await repo.list(db)
    ids = [r["id"] for r in rows]
    assert ids == [a, b]


@pytest.mark.asyncio
async def test_add_clips_upserts_no_duplicate(db):
    repo = WorkspacesRepo()
    ws_id = await repo.create(db, name="w", provider_id="catdv", catalog_id="1")
    added = await repo.add_clips(db, ws_id, [("catdv", "1"), ("catdv", "2")])
    assert added == 2
    again = await repo.add_clips(db, ws_id, [("catdv", "1"), ("catdv", "3")])
    assert again == 1  # only "3" was new
    rows = await repo.list_clips(db, ws_id)
    keys = sorted((r["provider_id"], r["provider_clip_id"]) for r in rows)
    assert keys == [("catdv", "1"), ("catdv", "2"), ("catdv", "3")]


@pytest.mark.asyncio
async def test_remove_clips(db):
    repo = WorkspacesRepo()
    ws_id = await repo.create(db, name="w", provider_id="catdv", catalog_id="1")
    await repo.add_clips(db, ws_id, [("catdv", "1"), ("catdv", "2")])
    removed = await repo.remove_clips(db, ws_id, [("catdv", "2"), ("catdv", "99")])
    assert removed == 1
    rows = await repo.list_clips(db, ws_id)
    assert [(r["provider_id"], r["provider_clip_id"]) for r in rows] == [("catdv", "1")]


@pytest.mark.asyncio
async def test_workspaces_pinning_lists_all(db):
    repo = WorkspacesRepo()
    a = await repo.create(db, name="a", provider_id="catdv", catalog_id="1")
    b = await repo.create(db, name="b", provider_id="catdv", catalog_id="1")
    await repo.add_clips(db, a, [("catdv", "7")])
    await repo.add_clips(db, b, [("catdv", "7")])
    pinning = await repo.workspaces_pinning(db, ("catdv", "7"))
    assert pinning == [a, b]


@pytest.mark.asyncio
async def test_set_cache_state(db):
    repo = WorkspacesRepo()
    ws_id = await repo.create(db, name="w", provider_id="catdv", catalog_id="1")
    await repo.add_clips(db, ws_id, [("catdv", "1")])
    await repo.set_cache_state(db, ws_id, ("catdv", "1"), "ready")
    rows = await repo.list_clips(db, ws_id)
    assert rows[0]["cache_state"] == "ready"
    assert rows[0]["cache_error"] is None
    await repo.set_cache_state(
        db, ws_id, ("catdv", "1"), "error", error="404 missing proxy"
    )
    rows = await repo.list_clips(db, ws_id)
    assert rows[0]["cache_state"] == "error"
    assert rows[0]["cache_error"] == "404 missing proxy"


@pytest.mark.asyncio
async def test_delete_cascades_workspace_clips(db):
    repo = WorkspacesRepo()
    # SQLite foreign keys are off by default in aiosqlite; turn them on
    # so ON DELETE CASCADE actually fires for this test.
    await db.execute("PRAGMA foreign_keys = ON")
    ws_id = await repo.create(db, name="w", provider_id="catdv", catalog_id="1")
    await repo.add_clips(db, ws_id, [("catdv", "1")])
    await repo.delete(db, ws_id)
    rows = await repo.list_clips(db, ws_id)
    assert rows == []


@pytest.mark.asyncio
async def test_set_primary_pin_updates_clip_cache(db):
    # seed a clip_cache row first
    from datetime import UTC, datetime
    await db.execute(
        """
        INSERT INTO clip_cache (provider_id, provider_clip_id, name, catalog_id,
                                duration_secs, fps, canonical_json,
                                fetched_at)
        VALUES ('catdv', '1', 'n', '1', 1.0, 25.0, '{}', ?)
        """,
        (datetime.now(UTC).isoformat(),),
    )
    await db.commit()

    repo = WorkspacesRepo()
    ws_id = await repo.create(db, name="w", provider_id="catdv", catalog_id="1")
    await repo.set_primary_pin(db, ("catdv", "1"), ws_id)
    cur = await db.execute(
        "SELECT pinned_to_workspace_id FROM clip_cache "
        "WHERE provider_id='catdv' AND provider_clip_id='1'"
    )
    row = await cur.fetchone()
    assert row[0] == ws_id

    await repo.set_primary_pin(db, ("catdv", "1"), None)
    cur = await db.execute(
        "SELECT pinned_to_workspace_id FROM clip_cache "
        "WHERE provider_id='catdv' AND provider_clip_id='1'"
    )
    assert (await cur.fetchone())[0] is None
