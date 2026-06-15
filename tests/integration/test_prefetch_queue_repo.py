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


@pytest.mark.asyncio
async def test_list_active_includes_clip_name_via_join(db):
    repo = PrefetchQueueRepo()
    await db.execute(
        "INSERT INTO clip_cache "
        "(provider_id, provider_clip_id, name, catalog_id, "
        " duration_secs, fps, canonical_json, fetched_at) "
        "VALUES ('catdv', '888839', 'ARNOLD Bogdan Sis 09.mov', '881507', "
        "        300.0, 25.0, '{}', '2026-05-20T07:26:27+00:00')"
    )
    await db.commit()
    await repo.enqueue(db, key=("catdv", "888839"), who="request")

    active = await repo.list_active(db)
    assert len(active) == 1
    assert active[0]["clip_name"] == "ARNOLD Bogdan Sis 09.mov"
    assert active[0]["provider_clip_id"] == "888839"


@pytest.mark.asyncio
async def test_list_recent_clip_name_null_when_metadata_absent(db):
    repo = PrefetchQueueRepo()
    await repo.enqueue(db, key=("catdv", "999999"), who="request")

    recent = await repo.list_recent(db, limit=10)
    assert len(recent) == 1
    assert recent[0]["clip_name"] is None


@pytest.mark.asyncio
async def test_requeue_orphans_resets_downloading_rows(db):
    # A row left `downloading` when the process died (e.g. SIGKILL or a
    # crash mid-download) is an orphan: claim_next only picks up `queued`
    # rows, so it would otherwise hang forever. requeue_orphans flips it
    # back so the worker re-claims it on the next boot.
    repo = PrefetchQueueRepo()
    rid = await repo.enqueue(db, key=("catdv", "888894"), who="request")
    await repo.claim_next(db)  # -> downloading, started_at set
    before = await repo.get(db, rid)
    assert before["status"] == "downloading" and before["started_at"] is not None

    n = await repo.requeue_orphans(db)
    assert n == 1
    after = await repo.get(db, rid)
    assert after["status"] == "queued"
    assert after["started_at"] is None
    # It is now claimable again.
    claimed = await repo.claim_next(db)
    assert claimed is not None and claimed["id"] == rid


@pytest.mark.asyncio
async def test_requeue_orphans_leaves_terminal_and_queued_rows_untouched(db):
    repo = PrefetchQueueRepo()
    queued = await repo.enqueue(db, key=("catdv", "1"), who="request")
    done = await repo.enqueue(db, key=("catdv", "2"), who="request")
    await repo.claim_next(db)  # claims `queued` (id 1) -> downloading
    await repo.mark_done(db, queued, bytes_downloaded=1)
    # id 2 is still queued; id 1 is now done. No downloading rows remain.
    n = await repo.requeue_orphans(db)
    assert n == 0
    assert (await repo.get(db, queued))["status"] == "done"
    assert (await repo.get(db, done))["status"] == "queued"
