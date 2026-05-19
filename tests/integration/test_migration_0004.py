from pathlib import Path

import pytest

from backend.app.db import open_db
from backend.app.migrations_runner import apply_migrations

MIGRATIONS = Path(__file__).resolve().parents[2] / "backend" / "migrations"


async def _columns(conn, table: str) -> set[str]:
    cur = await conn.execute(f"PRAGMA table_info({table})")
    return {row[1] for row in await cur.fetchall()}


@pytest.mark.asyncio
async def test_pending_operations_table_has_expected_columns(tmp_path):
    db = tmp_path / "test.db"
    async with open_db(db) as conn:
        await apply_migrations(conn, MIGRATIONS)
        cols = await _columns(conn, "pending_operations")
    assert {
        "id",
        "provider_id",
        "provider_clip_id",
        "op_kind",
        "op_json",
        "origin_annotation_id",
        "origin_review_item_ids",
        "expected_etag",
        "status",
        "attempts",
        "last_error",
        "enqueued_at",
        "attempted_at",
        "applied_at",
    }.issubset(cols)


@pytest.mark.asyncio
async def test_pending_operations_index_on_status_enqueued_at_exists(tmp_path):
    db = tmp_path / "test.db"
    async with open_db(db) as conn:
        await apply_migrations(conn, MIGRATIONS)
        cur = await conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='index' AND tbl_name='pending_operations'"
        )
        names = {r[0] for r in await cur.fetchall()}
    assert "idx_pending_ops_status" in names


@pytest.mark.asyncio
async def test_connection_events_table_has_expected_columns(tmp_path):
    db = tmp_path / "test.db"
    async with open_db(db) as conn:
        await apply_migrations(conn, MIGRATIONS)
        cols = await _columns(conn, "connection_events")
    assert {"id", "state", "detail", "at"}.issubset(cols)
