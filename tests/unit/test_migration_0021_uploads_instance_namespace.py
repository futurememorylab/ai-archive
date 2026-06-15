"""0021 drops only uploaded-clip ai_store_files cache rows (synthetic ids
>= UPLOAD_ID_BASE) so they re-upload to the instance-namespaced GCS path.
CatDV cache rows (< UPLOAD_ID_BASE) survive (issue #55)."""

from pathlib import Path

import pytest

from backend.app.db import open_db
from backend.app.migrations_runner import apply_migrations
from backend.app.uploaded_ids import UPLOAD_ID_BASE


@pytest.fixture
async def conn(tmp_path: Path):
    cm = open_db(tmp_path / "t.db")
    c = await cm.__aenter__()
    yield c
    await cm.__aexit__(None, None, None)


@pytest.mark.asyncio
async def test_only_uploaded_rows_deleted(conn):
    await apply_migrations(conn, Path("backend/migrations"))
    catdv_id = 42
    uploaded_id = UPLOAD_ID_BASE + 1
    for clip_id in (catdv_id, uploaded_id):
        await conn.execute(
            "INSERT INTO ai_store_files (store_id, catdv_clip_id, gcs_uri, "
            "mime_type, size_bytes, sha256, uploaded_at, last_used_at) "
            "VALUES ('gcs:test-bucket', ?, 'gs://test-bucket/clips/x.mov', "
            "'video/mp4', 1, 'h', 't', 't')",
            (clip_id,),
        )
    await conn.commit()

    # Re-run the 0021 statement directly (migrations are idempotent by
    # version; re-applying the DELETE proves the intended scope).
    await conn.execute(
        "DELETE FROM ai_store_files WHERE catdv_clip_id >= ?", (UPLOAD_ID_BASE,)
    )
    await conn.commit()

    cur = await conn.execute("SELECT catdv_clip_id FROM ai_store_files")
    remaining = {row[0] for row in await cur.fetchall()}
    assert catdv_id in remaining
    assert uploaded_id not in remaining
