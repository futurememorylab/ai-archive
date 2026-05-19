import json
from datetime import UTC, datetime, timedelta

import pytest

from backend.app.archive.change_set_json import change_op_to_json
from backend.app.archive.errors import FatalProviderError, RetryableError
from backend.app.archive.model import (
    ConflictDetail,
    SetField,
    WriteResult,
)
from backend.app.repositories.pending_operations import PendingOperationsRepo
from backend.app.repositories.write_log import WriteLogRepo
from backend.app.services.connection_monitor import ConnectionState
from backend.app.services.sync_engine import SyncEngine


class FakeProvider:
    id = "catdv"

    def __init__(self) -> None:
        self.calls: list = []
        self.next_result: WriteResult | None = None
        self.next_exc: Exception | None = None

    async def apply_changes(self, change_set):
        self.calls.append(change_set)
        if self.next_exc is not None:
            exc = self.next_exc
            self.next_exc = None
            raise exc
        return self.next_result or WriteResult(
            status="ok", upstream_response={"ID": int(change_set.clip_key[1])}
        )


class AlwaysOnlineMonitor:
    def current_state(self) -> ConnectionState:
        return ConnectionState.online


async def _enqueue_one(db, *, clip_id: str = "1", attempts: int = 0):
    repo = PendingOperationsRepo()
    op = SetField(identifier="x", value=1)
    rows = [
        {
            "provider_id": "catdv",
            "provider_clip_id": clip_id,
            "op_kind": "SetField",
            "op_json": change_op_to_json(op),
            "origin_annotation_id": None,
            "origin_review_item_ids": None,
            "expected_etag": None,
        }
    ]
    ids = await repo.insert_many(db, rows=rows)
    if attempts:
        # simulate prior retry
        await repo.mark_retryable(db, ids, error="prev")
        for _ in range(attempts - 1):
            await repo.mark_retryable(db, ids, error="prev")
    return ids


def _make_engine(db, *, provider, monitor=None, **kwargs):
    return SyncEngine(
        provider=provider,
        pending_ops_repo=PendingOperationsRepo(),
        write_log_repo=WriteLogRepo(),
        connection_monitor=monitor or AlwaysOnlineMonitor(),
        db_provider=lambda: db,
        **kwargs,
    )


@pytest.mark.asyncio
async def test_drain_once_applies_pending_op(db):
    await _enqueue_one(db)
    provider = FakeProvider()
    engine = _make_engine(db, provider=provider)
    n = await engine.drain_once()
    assert n == 1
    rows = await PendingOperationsRepo().list_pending(db, status="applied")
    assert len(rows) == 1
    assert len(provider.calls) == 1


@pytest.mark.asyncio
async def test_drain_once_batches_ops_per_clip(db):
    await _enqueue_one(db, clip_id="1")
    await _enqueue_one(db, clip_id="1")
    await _enqueue_one(db, clip_id="2")
    provider = FakeProvider()
    engine = _make_engine(db, provider=provider)
    n = await engine.drain_once()
    assert n == 2   # 2 clips processed
    assert len(provider.calls) == 2
    by_clip = {cs.clip_key[1]: cs for cs in provider.calls}
    assert len(by_clip["1"].ops) == 2
    assert len(by_clip["2"].ops) == 1


@pytest.mark.asyncio
async def test_drain_once_skips_when_offline(db):
    await _enqueue_one(db)

    class OfflineMonitor:
        def current_state(self):
            return ConnectionState.offline

    provider = FakeProvider()
    engine = _make_engine(db, provider=provider, monitor=OfflineMonitor())
    n = await engine.drain_once()
    assert n == 0
    assert provider.calls == []
    pending = await PendingOperationsRepo().list_pending(db)
    assert len(pending) == 1


@pytest.mark.asyncio
async def test_drain_once_marks_conflict(db):
    await _enqueue_one(db)
    provider = FakeProvider()
    provider.next_result = WriteResult(
        status="conflict",
        upstream_response={},
        new_etag="v2",
        conflict_detail=ConflictDetail(
            kind="modified", expected_etag="v1", actual_etag="v2"
        ),
    )
    engine = _make_engine(db, provider=provider)
    await engine.drain_once()
    rows = await PendingOperationsRepo().list_pending(db, status="conflict")
    assert len(rows) == 1
    detail = json.loads(rows[0]["last_error"])
    assert detail["expected_etag"] == "v1"
    assert detail["actual_etag"] == "v2"


@pytest.mark.asyncio
async def test_drain_once_retries_on_retryable_error(db):
    await _enqueue_one(db)
    provider = FakeProvider()
    provider.next_exc = RetryableError("server busy")
    engine = _make_engine(db, provider=provider)
    await engine.drain_once()
    rows = await PendingOperationsRepo().list_pending(db)
    assert len(rows) == 1
    assert rows[0]["status"] == "pending"
    assert rows[0]["attempts"] == 1
    assert "busy" in (rows[0]["last_error"] or "")


@pytest.mark.asyncio
async def test_drain_once_marks_failed_on_fatal_error(db):
    await _enqueue_one(db)
    provider = FakeProvider()
    provider.next_exc = FatalProviderError("nope")
    engine = _make_engine(db, provider=provider)
    await engine.drain_once()
    rows = await PendingOperationsRepo().list_pending(db, status="failed")
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_drain_once_writes_write_log_on_success(db):
    await _enqueue_one(db, clip_id="42")
    provider = FakeProvider()
    engine = _make_engine(db, provider=provider)
    await engine.drain_once()

    cur = await db.execute(
        "SELECT catdv_clip_id, provider_id, provider_clip_id, status "
        "FROM write_log"
    )
    rows = await cur.fetchall()
    assert len(rows) == 1
    assert rows[0][0] == 42
    assert rows[0][1] == "catdv"
    assert rows[0][2] == "42"
    assert rows[0][3] == "ok"


@pytest.mark.asyncio
async def test_drain_skips_pending_within_backoff(db):
    # one op with attempts=1, attempted_at=now -> backoff = retry_base_s
    ids = await _enqueue_one(db)
    # PendingOperationsRepo.mark_retryable sets attempted_at=now; reuse
    provider = FakeProvider()
    engine = _make_engine(
        db,
        provider=provider,
        retry_base_s=60.0,
        retry_max_s=300.0,
    )
    # bump attempts via direct repo
    repo = PendingOperationsRepo()
    await repo.mark_retryable(db, ids, error="x")
    n = await engine.drain_once()
    assert n == 0  # skipped due to backoff


@pytest.mark.asyncio
async def test_drain_processes_when_backoff_elapsed(db):
    ids = await _enqueue_one(db)
    repo = PendingOperationsRepo()
    # stamp attempted_at far in the past
    past = (datetime.now(tz=UTC) - timedelta(hours=1)).isoformat()
    await repo.mark_retryable(db, ids, error="x", attempted_at=past)
    provider = FakeProvider()
    engine = _make_engine(
        db, provider=provider, retry_base_s=2.0, retry_max_s=300.0
    )
    n = await engine.drain_once()
    assert n == 1
    rows = await repo.list_pending(db, status="applied")
    assert len(rows) == 1
