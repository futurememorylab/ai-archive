"""Retry-ceiling and conflict-etag behaviour of the SyncEngine.

Two guarantees beyond the unknown-exception ceiling
(test_sync_engine_retryable_unknown.py):

- An *explicit* RetryableError, and a WriteResult(status="retryable"), are
  also bounded by max_attempts — they must eventually flip to `failed`
  rather than retry forever (ADR 0091).
- When several apply batches for one clip are merged into a single
  ChangeSet, the conflict check uses the *freshest* (last-enqueued)
  expected_etag, not the oldest (ADR 0091).
"""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from backend.app.archive.errors import RetryableError
from backend.app.archive.model import ChangeSet, WriteResult
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


async def _seed_op(conn, *, clip_id: str = "42", expected_etag: str | None = None) -> int:
    repo = PendingOperationsRepo()
    ids = await repo.insert_many(
        conn,
        rows=[
            {
                "provider_id": "catdv",
                "provider_clip_id": clip_id,
                "op_kind": "AddMarkers",
                "op_json": '{"kind": "AddMarkers", "markers": []}',
                "origin_annotation_id": None,
                "origin_review_item_ids": None,
                "expected_etag": expected_etag,
            }
        ],
    )
    return ids[0]


def _engine(conn, provider) -> SyncEngine:
    return SyncEngine(
        provider=provider,
        pending_ops_repo=PendingOperationsRepo(),
        write_log_repo=WriteLogRepo(),
        connection_monitor=_make_monitor(),
        db_provider=lambda: conn,
        tick_interval_s=0.01,
        retry_base_s=0.001,
        retry_max_s=0.001,
        max_attempts=3,
    )


@pytest.mark.asyncio
async def test_explicit_retryable_error_fails_at_max_attempts(tmp_path):
    db_path = tmp_path / "test.db"
    async with open_db(db_path) as conn:
        await apply_migrations(conn, MIGRATIONS)
        op_id = await _seed_op(conn)

        provider = AsyncMock()
        provider.id = "catdv"
        provider.apply_changes = AsyncMock(side_effect=RetryableError("server busy"))

        engine = _engine(conn, provider)
        for _ in range(3):
            await asyncio.sleep(0.005)
            await engine.drain_once()

        row = await PendingOperationsRepo().get(conn, op_id)
        assert row["status"] == "failed", row
        assert row["attempts"] == 3, row
        assert "max_attempts" in (row["last_error"] or "")


@pytest.mark.asyncio
async def test_writeresult_retryable_fails_at_max_attempts(tmp_path):
    db_path = tmp_path / "test.db"
    async with open_db(db_path) as conn:
        await apply_migrations(conn, MIGRATIONS)
        op_id = await _seed_op(conn)

        provider = AsyncMock()
        provider.id = "catdv"
        provider.apply_changes = AsyncMock(
            return_value=WriteResult(status="retryable", upstream_response={"detail": "busy"})
        )

        engine = _engine(conn, provider)
        for _ in range(3):
            await asyncio.sleep(0.005)
            await engine.drain_once()

        row = await PendingOperationsRepo().get(conn, op_id)
        assert row["status"] == "failed", row
        assert row["attempts"] == 3, row


@pytest.mark.asyncio
async def test_one_retryable_then_success_does_not_fail(tmp_path):
    # Guard: a transient blip that clears before the ceiling still applies.
    db_path = tmp_path / "test.db"
    async with open_db(db_path) as conn:
        await apply_migrations(conn, MIGRATIONS)
        op_id = await _seed_op(conn)

        provider = AsyncMock()
        provider.id = "catdv"
        provider.apply_changes = AsyncMock(
            side_effect=[
                RetryableError("blip"),
                WriteResult(status="ok", upstream_response={}),
            ]
        )

        engine = _engine(conn, provider)
        for _ in range(2):
            await asyncio.sleep(0.005)
            await engine.drain_once()

        row = await PendingOperationsRepo().get(conn, op_id)
        assert row["status"] == "applied", row


@pytest.mark.asyncio
async def test_ok_stamps_synced_on_originating_review_items(tmp_path):
    # On a successful write-back the engine stamps synced_at on the review_items
    # that produced the op (origin_review_item_ids), so the UI can show "applied"
    # only once a change is truly on CatDV. See ADR 0093.
    db_path = tmp_path / "test.db"
    async with open_db(db_path) as conn:
        await apply_migrations(conn, MIGRATIONS)
        repo = PendingOperationsRepo()
        await repo.insert_many(
            conn,
            rows=[
                {
                    "provider_id": "catdv",
                    "provider_clip_id": "42",
                    "op_kind": "AddMarkers",
                    "op_json": '{"kind": "AddMarkers", "markers": []}',
                    "origin_annotation_id": None,
                    "origin_review_item_ids": [5, 6],
                    "expected_etag": None,
                }
            ],
        )

        provider = AsyncMock()
        provider.id = "catdv"
        provider.apply_changes = AsyncMock(
            return_value=WriteResult(status="ok", upstream_response={"ID": 42, "modifyDate": "x"})
        )
        review_items_repo = AsyncMock()

        engine = SyncEngine(
            provider=provider,
            pending_ops_repo=PendingOperationsRepo(),
            write_log_repo=WriteLogRepo(),
            connection_monitor=_make_monitor(),
            db_provider=lambda: conn,
            review_items_repo=review_items_repo,
        )
        await engine.drain_once()

        review_items_repo.mark_synced.assert_awaited_once()
        # called with (conn, [5, 6])
        item_ids = review_items_repo.mark_synced.await_args.args[1]
        assert sorted(item_ids) == [5, 6]


@pytest.mark.asyncio
async def test_merged_group_uses_freshest_expected_etag(tmp_path):
    db_path = tmp_path / "test.db"
    async with open_db(db_path) as conn:
        await apply_migrations(conn, MIGRATIONS)
        # Two batches for the same clip, enqueued oldest-first with different
        # etags (an external CatDV edit happened between them).
        await _seed_op(conn, clip_id="42", expected_etag="E1")
        await _seed_op(conn, clip_id="42", expected_etag="E2")

        captured: dict = {}

        async def capture(cs: ChangeSet) -> WriteResult:
            captured["etag"] = cs.expected_etag
            return WriteResult(status="ok", upstream_response={})

        provider = AsyncMock()
        provider.id = "catdv"
        provider.apply_changes = AsyncMock(side_effect=capture)

        engine = _engine(conn, provider)
        await engine.drain_once()

        assert captured["etag"] == "E2"
