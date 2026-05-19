import pytest

from backend.app.repositories.cache_actions_log import CacheActionsLogRepo


@pytest.mark.asyncio
async def test_append_and_list(db):
    repo = CacheActionsLogRepo()
    a = await repo.append(
        db, who="system", action="lru_evict",
        clip_keys=[("catdv", "1")], result="ok", bytes_freed=100,
    )
    b = await repo.append(
        db, who="request", action="evict_local_media",
        clip_keys=[("catdv", "2"), ("catdv", "3")], result="skipped",
        detail="pinned_by_workspaces=[5]",
    )
    assert b > a
    recent = await repo.list_recent(db)
    assert len(recent) == 2
    # most-recent first
    assert recent[0]["id"] == b
    assert recent[0]["result"] == "skipped"
    assert recent[0]["detail"] == "pinned_by_workspaces=[5]"
    # bulk row's clip_keys JSON survived round-trip
    import json
    assert json.loads(recent[0]["clip_keys"]) == [["catdv", "2"], ["catdv", "3"]]
    assert recent[1]["bytes_freed"] == 100


@pytest.mark.asyncio
async def test_list_recent_limit(db):
    repo = CacheActionsLogRepo()
    for i in range(5):
        await repo.append(
            db, who="system", action="lru_evict",
            clip_keys=[("catdv", str(i))], result="ok",
        )
    rows = await repo.list_recent(db, limit=3)
    assert len(rows) == 3
