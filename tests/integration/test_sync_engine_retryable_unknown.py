"""sync_engine.tick must NOT mark a pending_op 'failed' for unknown
exceptions on the first attempt. Such errors are usually transient
(transport, adapter bug); permanent-fail wipes recoverable writes."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from backend.app.db import open_db
from backend.app.migrations_runner import apply_migrations
from backend.app.repositories.pending_operations import PendingOperationsRepo
from backend.app.repositories.write_log import WriteLogRepo
from backend.app.services.connection_monitor import ConnectionMonitor, ConnectionState
from backend.app.services.sync_engine import SyncEngine

MIGRATIONS = Path(__file__).resolve().parents[2] / "backend" / "migrations"


def _make_monitor():
    monitor = AsyncMock(spec=ConnectionMonitor)
    monitor.current_state = lambda: ConnectionState.online
    return monitor


async def _seed_pending_op(conn) -> int:
    repo = PendingOperationsRepo()
    ids = await repo.insert_many(
        conn,
        rows=[{
            "provider_id": "catdv",
            "provider_clip_id": "42",
            "op_kind": "AddMarkers",
            "op_json": '{"kind": "AddMarkers", "markers": []}',
            "origin_annotation_id": None,
            "origin_review_item_ids": None,
            "expected_etag": None,
        }],
    )
    return ids[0]


@pytest.mark.asyncio
async def test_unknown_exception_marks_retryable_not_failed(tmp_path):
    db_path = tmp_path / "test.db"
    async with open_db(db_path) as conn:
        await apply_migrations(conn, MIGRATIONS)
        op_id = await _seed_pending_op(conn)

        provider = AsyncMock()
        provider.id = "catdv"
        provider.apply_changes = AsyncMock(side_effect=RuntimeError("never seen this"))

        engine = SyncEngine(
            provider=provider,
            pending_ops_repo=PendingOperationsRepo(),
            write_log_repo=WriteLogRepo(),
            connection_monitor=_make_monitor(),
            db_provider=lambda: conn,
        )
        await engine.drain_once()

        repo = PendingOperationsRepo()
        row = await repo.get(conn, op_id)
        assert row["status"] == "pending", f"expected retryable; got {row['status']}"
        assert row["attempts"] == 1


@pytest.mark.asyncio
async def test_unknown_exception_eventually_fails_at_max_attempts(tmp_path):
    db_path = tmp_path / "test.db"
    async with open_db(db_path) as conn:
        await apply_migrations(conn, MIGRATIONS)
        op_id = await _seed_pending_op(conn)

        provider = AsyncMock()
        provider.id = "catdv"
        provider.apply_changes = AsyncMock(side_effect=RuntimeError("persistent"))

        engine = SyncEngine(
            provider=provider,
            pending_ops_repo=PendingOperationsRepo(),
            write_log_repo=WriteLogRepo(),
            connection_monitor=_make_monitor(),
            db_provider=lambda: conn,
            tick_interval_s=0.01,
            retry_base_s=0.001,
            retry_max_s=0.001,
        )
        # Ten drains, each separated by enough wall time to clear backoff.
        # retry_base_s=0.001 means backoff is 1ms; sleep 5ms to ensure the
        # wall clock advances past the backoff window on every iteration.
        for _ in range(10):
            await asyncio.sleep(0.005)
            await engine.drain_once()

        repo = PendingOperationsRepo()
        row = await repo.get(conn, op_id)
        # After max_attempts (default 10), the row should be terminal-failed.
        assert row["status"] == "failed", row
        assert row["attempts"] >= 10
