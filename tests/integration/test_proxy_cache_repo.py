import pytest

from backend.app.repositories.proxy_cache import ProxyCacheRepo


@pytest.mark.asyncio
async def test_record_get_and_touch(db):
    repo = ProxyCacheRepo()
    await repo.record(db, clip_id=1, file_path="cache/1.mov", size_bytes=1000, etag=None)
    row = await repo.get(db, 1)
    assert row is not None
    assert row["file_path"] == "cache/1.mov"

    await repo.touch(db, clip_id=1)
    row2 = await repo.get(db, 1)
    assert row2["last_used_at"] >= row["last_used_at"]


@pytest.mark.asyncio
async def test_pick_lru_for_eviction(db):
    repo = ProxyCacheRepo()
    import asyncio

    for i in range(3):
        await repo.record(db, clip_id=i, file_path=f"cache/{i}.mov", size_bytes=1000, etag=None)
        await asyncio.sleep(0.01)
    await repo.touch(db, clip_id=0)

    victims = await repo.lru_candidates(db, max_bytes=1500)
    victim_ids = [v["catdv_clip_id"] for v in victims]
    assert 1 in victim_ids
    assert 2 in victim_ids
    assert 0 not in victim_ids
