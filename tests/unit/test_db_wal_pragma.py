"""Litestream (deploy/litestream.yml) requires WAL journaling; open_db
must keep setting it. If this fails, cloud persistence silently breaks."""

from backend.app.db import open_db


async def test_open_db_sets_wal(tmp_path):
    async with open_db(tmp_path / "t.db") as conn:
        cur = await conn.execute("PRAGMA journal_mode")
        row = await cur.fetchone()
    assert row[0].lower() == "wal"
