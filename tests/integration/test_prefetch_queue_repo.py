import pytest

from backend.app.repositories.prefetch_queue import PrefetchQueueRepo


@pytest.mark.asyncio
async def test_enqueue_returns_id_and_idempotent(db):
    repo = PrefetchQueueRepo()
    a = await repo.enqueue(db, key=("catdv", "1"), who="request")
    assert isinstance(a, int) and a > 0
    # A second enqueue for the same clip while still active returns the
    # existing row id (no duplicate work).
    b = await repo.enqueue(db, key=("catdv", "1"), who="request")
    assert b == a


@pytest.mark.asyncio
async def test_claim_next_is_fifo_and_atomic(db):
    repo = PrefetchQueueRepo()
    id1 = await repo.enqueue(db, key=("catdv", "1"), who="request")
    id2 = await repo.enqueue(db, key=("catdv", "2"), who="request")
    claimed_a = await repo.claim_next(db)
    claimed_b = await repo.claim_next(db)
    assert claimed_a["id"] == id1 and claimed_a["status"] == "downloading"
    assert claimed_b["id"] == id2
    # No more queued rows
    assert await repo.claim_next(db) is None


@pytest.mark.asyncio
async def test_mark_done_records_bytes_and_finished_at(db):
    repo = PrefetchQueueRepo()
    rid = await repo.enqueue(db, key=("catdv", "1"), who="request")
    await repo.claim_next(db)
    await repo.mark_done(db, rid, bytes_downloaded=12345)
    row = await repo.get(db, rid)
    assert row["status"] == "done"
    assert row["bytes_downloaded"] == 12345
    assert row["finished_at"] is not None


@pytest.mark.asyncio
async def test_mark_error_stores_message(db):
    repo = PrefetchQueueRepo()
    rid = await repo.enqueue(db, key=("catdv", "1"), who="request")
    await repo.claim_next(db)
    await repo.mark_error(db, rid, "VPN timeout after 1800s")
    row = await repo.get(db, rid)
    assert row["status"] == "error"
    assert row["error"] == "VPN timeout after 1800s"


@pytest.mark.asyncio
async def test_cancel_blocks_downloading(db):
    repo = PrefetchQueueRepo()
    rid = await repo.enqueue(db, key=("catdv", "1"), who="request")
    await repo.claim_next(db)
    out = await repo.mark_cancelled(db, rid)
    assert out is False, "cancel must reject downloading rows"
    row = await repo.get(db, rid)
    assert row["status"] == "downloading"


@pytest.mark.asyncio
async def test_cancel_succeeds_for_queued(db):
    repo = PrefetchQueueRepo()
    rid = await repo.enqueue(db, key=("catdv", "1"), who="request")
    out = await repo.mark_cancelled(db, rid)
    assert out is True
    row = await repo.get(db, rid)
    assert row["status"] == "cancelled"


@pytest.mark.asyncio
async def test_count_by_status(db):
    repo = PrefetchQueueRepo()
    await repo.enqueue(db, key=("catdv", "1"), who="request")
    await repo.enqueue(db, key=("catdv", "2"), who="request")
    rid3 = await repo.enqueue(db, key=("catdv", "3"), who="request")
    await repo.claim_next(db)  # 1 -> downloading
    await repo.mark_cancelled(db, rid3)
    counts = await repo.count_by_status(db)
    assert counts == {"downloading": 1, "queued": 1, "cancelled": 1}


@pytest.mark.asyncio
async def test_list_active_excludes_terminal(db):
    repo = PrefetchQueueRepo()
    a = await repo.enqueue(db, key=("catdv", "1"), who="request")
    b = await repo.enqueue(db, key=("catdv", "2"), who="request")
    await repo.claim_next(db)
    await repo.mark_done(db, a, bytes_downloaded=1)
    rows = await repo.list_active(db)
    assert [r["id"] for r in rows] == [b]
