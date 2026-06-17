import pytest


@pytest.mark.asyncio
async def test_clip_versions_table_exists(db):
    cur = await db.execute("PRAGMA table_info(clip_versions)")
    cols = {row[1] for row in await cur.fetchall()}
    assert {
        "id",
        "provider_id",
        "catdv_clip_id",
        "version_num",
        "parent_version_id",
        "snapshot",
        "diff",
        "origin",
        "model",
        "prompt_version_id",
        "annotation_id",
        "author",
        "publish_state",
        "expected_etag",
        "failed_reason",
        "synced_at",
        "created_at",
    } <= cols


@pytest.mark.asyncio
async def test_pending_operations_has_origin_clip_version_id(db):
    cur = await db.execute("PRAGMA table_info(pending_operations)")
    cols = {row[1] for row in await cur.fetchall()}
    assert "origin_clip_version_id" in cols
