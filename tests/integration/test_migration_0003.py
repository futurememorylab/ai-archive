from pathlib import Path

import pytest

from backend.app.db import open_db
from backend.app.migrations_runner import apply_migrations

MIGRATIONS = Path(__file__).resolve().parents[2] / "backend" / "migrations"

TABLES_WITH_PROVIDER_COLUMNS = [
    "annotations",
    "review_items",
    "job_items",
    "proxy_cache",
    "ai_store_files",
    "write_log",
]


async def _columns(conn, table: str) -> set[str]:
    cur = await conn.execute(f"PRAGMA table_info({table})")
    return {row[1] for row in await cur.fetchall()}


@pytest.mark.asyncio
async def test_migration_0003_adds_provider_columns_to_all_clip_tables(tmp_path):
    db_path = tmp_path / "test.db"
    async with open_db(db_path) as conn:
        await apply_migrations(conn, MIGRATIONS)
        for table in TABLES_WITH_PROVIDER_COLUMNS:
            cols = await _columns(conn, table)
            assert "provider_id" in cols, f"missing provider_id in {table}"
            assert "provider_clip_id" in cols, f"missing provider_clip_id in {table}"


@pytest.mark.asyncio
async def test_migration_0003_backfills_existing_rows(tmp_path):
    db_path = tmp_path / "test.db"
    # Apply 0001 and 0002 manually, seed rows, then run full chain (0003).
    async with open_db(db_path) as conn:
        for name in ("0001_initial.sql", "0002_ai_store_files.sql"):
            await conn.executescript((MIGRATIONS / name).read_text())
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                name TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        for name in ("0001_initial.sql", "0002_ai_store_files.sql"):
            await conn.execute("INSERT INTO schema_migrations(name) VALUES (?)", (name,))

        # Seed: one row in each clip-keyed table.
        await conn.execute(
            """
            INSERT INTO templates(id, name, prompt, output_schema, target_map,
                                  model, created_at, updated_at)
            VALUES (1, 't', 'p', '{}', '{}', 'g', '2026', '2026')
            """
        )
        await conn.execute(
            """
            INSERT INTO jobs(id, template_id, status, created_at, total_clips)
            VALUES (1, 1, 'queued', '2026', 0)
            """
        )
        await conn.execute(
            """
            INSERT INTO job_items(job_id, catdv_clip_id, status)
            VALUES (1, 11, 'queued')
            """
        )
        await conn.execute(
            """
            INSERT INTO proxy_cache(catdv_clip_id, file_path, size_bytes,
                                    downloaded_at, last_used_at)
            VALUES (12, '/p', 0, '2026', '2026')
            """
        )
        await conn.execute(
            """
            INSERT INTO ai_store_files(store_id, catdv_clip_id, gcs_uri,
                mime_type, size_bytes, sha256, uploaded_at, last_used_at)
            VALUES ('gcs:b', 13, 'gs://b/clips/13.mov', 'video/quicktime', 1,
                    'x', '2026', '2026')
            """
        )
        await conn.execute(
            """
            INSERT INTO annotations(id, catdv_clip_id, catdv_clip_name,
                template_id, model, prompt_used, raw_response,
                structured_output, clip_snapshot, created_at)
            VALUES (1, 14, 'n', 1, 'g', 'p', '{}', '{}', '{}', '2026')
            """
        )
        await conn.execute(
            """
            INSERT INTO review_items(annotation_id, catdv_clip_id, kind,
                proposed_value, decision)
            VALUES (1, 14, 'field', 'v', 'pending')
            """
        )
        await conn.execute(
            """
            INSERT INTO write_log(catdv_clip_id, payload, response, status,
                                  written_at)
            VALUES (15, '{}', '{}', 'ok', '2026')
            """
        )
        await conn.commit()

    async with open_db(db_path) as conn:
        applied = await apply_migrations(conn, MIGRATIONS)
        assert "0003_provider_id_and_caches.sql" in applied

        for table, expected_clip_id in [
            ("job_items", "11"),
            ("proxy_cache", "12"),
            ("ai_store_files", "13"),
            ("annotations", "14"),
            ("review_items", "14"),
            ("write_log", "15"),
        ]:
            cur = await conn.execute(f"SELECT provider_id, provider_clip_id FROM {table}")
            row = await cur.fetchone()
            assert row is not None, f"no row in {table}"
            assert row[0] == "catdv", f"provider_id wrong in {table}: {row}"
            assert row[1] == expected_clip_id, f"provider_clip_id wrong in {table}: {row}"


@pytest.mark.asyncio
async def test_migration_0003_creates_clip_cache_table(tmp_path):
    db_path = tmp_path / "test.db"
    async with open_db(db_path) as conn:
        await apply_migrations(conn, MIGRATIONS)
        cols = await _columns(conn, "clip_cache")
        assert {
            "provider_id",
            "provider_clip_id",
            "name",
            "catalog_id",
            "duration_secs",
            "fps",
            "canonical_json",
            "provider_etag",
            "fetched_at",
            "pinned_to_workspace_id",
        }.issubset(cols)
        cur = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='clip_cache'"
        )
        idx = {row[0] for row in await cur.fetchall()}
        assert "idx_clip_cache_catalog" in idx


@pytest.mark.asyncio
async def test_migration_0003_creates_field_def_cache_table(tmp_path):
    db_path = tmp_path / "test.db"
    async with open_db(db_path) as conn:
        await apply_migrations(conn, MIGRATIONS)
        cols = await _columns(conn, "field_def_cache")
        assert {"provider_id", "identifier", "json", "fetched_at"}.issubset(cols)
