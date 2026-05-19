import json

import pytest

from backend.app.repositories.pending_operations import PendingOperationsRepo


def _row(op_kind: str = "SetField", op_json: str | None = None) -> dict:
    return {
        "provider_id": "catdv",
        "provider_clip_id": "1",
        "op_kind": op_kind,
        "op_json": op_json or json.dumps({"kind": op_kind, "identifier": "x", "value": 1}),
        "origin_annotation_id": None,
        "origin_review_item_ids": [10, 11],
        "expected_etag": "v1",
    }


@pytest.mark.asyncio
async def test_insert_many_assigns_ids_and_defaults(db):
    repo = PendingOperationsRepo()
    ids = await repo.insert_many(db, rows=[_row(), _row()])
    assert len(ids) == 2
    rows = await repo.list_pending(db)
    assert len(rows) == 2
    for r in rows:
        assert r["status"] == "pending"
        assert r["attempts"] == 0
        assert r["enqueued_at"]
        assert r["attempted_at"] is None
        assert r["applied_at"] is None
        assert json.loads(r["origin_review_item_ids"]) == [10, 11]


@pytest.mark.asyncio
async def test_list_pending_orders_by_enqueued_at_then_id(db):
    repo = PendingOperationsRepo()
    await repo.insert_many(db, rows=[_row(), _row(), _row()])
    rows = await repo.list_pending(db)
    ids = [r["id"] for r in rows]
    assert ids == sorted(ids)


@pytest.mark.asyncio
async def test_list_pending_for_clip_filters(db):
    repo = PendingOperationsRepo()
    a = _row()
    b = {**_row(), "provider_clip_id": "2"}
    await repo.insert_many(db, rows=[a, b])
    rows = await repo.list_pending_for_clip(
        db, provider_id="catdv", provider_clip_id="1"
    )
    assert [r["provider_clip_id"] for r in rows] == ["1"]


@pytest.mark.asyncio
async def test_mark_in_flight_then_applied(db):
    repo = PendingOperationsRepo()
    ids = await repo.insert_many(db, rows=[_row()])
    await repo.mark_in_flight(db, ids)
    [r] = await repo.list_pending(db, status="in_flight")
    assert r["attempted_at"] is not None

    await repo.mark_applied(db, ids)
    [r] = await repo.list_pending(db, status="applied")
    assert r["applied_at"] is not None
    assert r["last_error"] is None


@pytest.mark.asyncio
async def test_mark_conflict_stores_detail_json(db):
    repo = PendingOperationsRepo()
    ids = await repo.insert_many(db, rows=[_row()])
    detail = {"kind": "modified", "expected_etag": "v1", "actual_etag": "v2"}
    await repo.mark_conflict(db, ids, conflict_detail=detail)
    [r] = await repo.list_pending(db, status="conflict")
    assert json.loads(r["last_error"]) == detail


@pytest.mark.asyncio
async def test_mark_retryable_increments_attempts_and_stays_pending(db):
    repo = PendingOperationsRepo()
    ids = await repo.insert_many(db, rows=[_row()])
    await repo.mark_retryable(db, ids, error="boom")
    [r] = await repo.list_pending(db)
    assert r["attempts"] == 1
    assert r["last_error"] == "boom"
    assert r["attempted_at"] is not None

    await repo.mark_retryable(db, ids, error="boom2")
    [r] = await repo.list_pending(db)
    assert r["attempts"] == 2


@pytest.mark.asyncio
async def test_mark_failed_sets_terminal_state(db):
    repo = PendingOperationsRepo()
    ids = await repo.insert_many(db, rows=[_row()])
    await repo.mark_failed(db, ids, error="fatal")
    [r] = await repo.list_pending(db, status="failed")
    assert r["last_error"] == "fatal"


@pytest.mark.asyncio
async def test_reset_in_flight_to_pending(db):
    repo = PendingOperationsRepo()
    ids = await repo.insert_many(db, rows=[_row(), _row()])
    await repo.mark_in_flight(db, ids)
    n = await repo.reset_in_flight_to_pending(db)
    assert n == 2
    rows = await repo.list_pending(db)
    assert len(rows) == 2
    for r in rows:
        assert r["attempted_at"] is None
