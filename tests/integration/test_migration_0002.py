from pathlib import Path

import pytest

from backend.app.db import open_db
from backend.app.migrations_runner import apply_migrations

MIGRATIONS = Path(__file__).resolve().parents[2] / "backend" / "migrations"


@pytest.mark.asyncio
async def test_migration_0002_renames_gcs_files_and_backfills_store_id(tmp_path):
    db_path = tmp_path / "test.db"
    # Step 1: apply only the initial migration manually so we can seed rows.
    async with open_db(db_path) as conn:
        sql = (MIGRATIONS / "0001_initial.sql").read_text()
        await conn.executescript(sql)
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                name TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        await conn.execute("INSERT INTO schema_migrations(name) VALUES ('0001_initial.sql')")
        await conn.commit()

        # Seed a row in the old shape.
        await conn.execute(
            """
            INSERT INTO gcs_files
              (catdv_clip_id, gcs_uri, mime_type, size_bytes, sha256,
               uploaded_at, last_used_at)
            VALUES (42, 'gs://my-bucket/clips/42.mov', 'video/quicktime',
                    100, 'abc', '2026-05-19T00:00:00Z', '2026-05-19T00:00:00Z')
            """
        )
        await conn.commit()

    # Step 2: now run the full migrations chain (0002 should run).
    async with open_db(db_path) as conn:
        applied = await apply_migrations(conn, MIGRATIONS)
        assert "0002_ai_store_files.sql" in applied

        # Old table is gone.
        cur = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='gcs_files'"
        )
        assert await cur.fetchone() is None

        # New table exists with the row migrated.
        cur = await conn.execute(
            "SELECT store_id, catdv_clip_id, gcs_uri, sha256, expires_at "
            "FROM ai_store_files WHERE catdv_clip_id = 42"
        )
        row = await cur.fetchone()
        assert row is not None
        store_id, clip_id, uri, sha, expires = row
        assert store_id == "gcs:my-bucket"
        assert clip_id == 42
        assert uri == "gs://my-bucket/clips/42.mov"
        assert sha == "abc"
        assert expires is None


@pytest.mark.asyncio
async def test_migration_0002_is_idempotent_when_gcs_files_empty(tmp_path):
    db_path = tmp_path / "test.db"
    async with open_db(db_path) as conn:
        applied = await apply_migrations(conn, MIGRATIONS)
        assert "0001_initial.sql" in applied
        assert "0002_ai_store_files.sql" in applied

        cur = await conn.execute("SELECT COUNT(*) FROM ai_store_files")
        assert (await cur.fetchone())[0] == 0
