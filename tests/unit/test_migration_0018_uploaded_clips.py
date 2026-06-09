"""0018 adds the uploaded_clip table without touching existing tables."""

from pathlib import Path

import pytest

from backend.app.db import open_db
from backend.app.migrations_runner import apply_migrations


@pytest.fixture
async def conn(tmp_path: Path):
    cm = open_db(tmp_path / "t.db")
    c = await cm.__aenter__()
    yield c
    await cm.__aexit__(None, None, None)


@pytest.mark.asyncio
async def test_uploaded_clip_table_columns(conn):
    await apply_migrations(conn, Path("backend/migrations"))
    cur = await conn.execute("PRAGMA table_info(uploaded_clip)")
    cols = {row[1] for row in await cur.fetchall()}
    assert {
        "id", "original_filename", "stored_filename", "mime",
        "size_bytes", "duration_secs", "width", "height", "created_at",
    } <= cols


@pytest.mark.asyncio
async def test_autoincrement_never_reuses_ids(conn):
    await apply_migrations(conn, Path("backend/migrations"))
    await conn.execute(
        "INSERT INTO uploaded_clip(original_filename, stored_filename, mime, "
        "size_bytes, created_at) VALUES ('a.mp4','x','video/mp4',1,'t')"
    )
    await conn.execute("DELETE FROM uploaded_clip")
    await conn.execute(
        "INSERT INTO uploaded_clip(original_filename, stored_filename, mime, "
        "size_bytes, created_at) VALUES ('b.mp4','y','video/mp4',1,'t')"
    )
    await conn.commit()
    cur = await conn.execute("SELECT id FROM uploaded_clip")
    # AUTOINCREMENT → second row gets id 2, not a reused 1.
    assert (await cur.fetchone())[0] == 2


@pytest.mark.asyncio
async def test_studio_set_untouched(conn):
    await apply_migrations(conn, Path("backend/migrations"))
    cur = await conn.execute("PRAGMA table_info(studio_set)")
    assert {row[1] for row in await cur.fetchall()} >= {"id", "name", "source"}
