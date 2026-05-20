import pytest


@pytest.mark.asyncio
async def test_prefetch_queue_columns(db):
    cur = await db.execute("PRAGMA table_info(prefetch_queue)")
    cols = {row[1]: row[2] for row in await cur.fetchall()}
    assert cols == {
        "id":                "INTEGER",
        "provider_id":       "TEXT",
        "provider_clip_id":  "TEXT",
        "status":            "TEXT",
        "requested_by":      "TEXT",
        "requested_at":      "TEXT",
        "started_at":        "TEXT",
        "finished_at":       "TEXT",
        "error":             "TEXT",
        "bytes_downloaded":  "INTEGER",
    }


@pytest.mark.asyncio
async def test_prefetch_queue_indexes(db):
    cur = await db.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='index' AND tbl_name='prefetch_queue'"
    )
    names = {row[0] for row in await cur.fetchall()}
    assert "idx_prefetch_queue_status_requested_at" in names
    assert "idx_prefetch_queue_clip_status" in names
