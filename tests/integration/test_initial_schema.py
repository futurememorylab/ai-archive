from pathlib import Path

import pytest

from backend.app.db import open_db
from backend.app.migrations_runner import apply_migrations

MIGRATIONS = Path(__file__).resolve().parents[2] / "backend" / "migrations"

EXPECTED_TABLES = {
    "templates",
    "jobs",
    "job_items",
    "proxy_cache",
    "gcs_files",
    "annotations",
    "annotations_fts",
    "review_items",
    "write_log",
    "embeddings",
    "tags",
    "schema_migrations",
}


async def _seed_template(conn):
    await conn.execute(
        """
        INSERT INTO templates (id, name, prompt, output_schema, target_map, model,
                               created_at, updated_at)
        VALUES (1, 't', 'p', '{}', '{}', 'gemini-2.5-pro', '2026-05-18', '2026-05-18')
        """
    )
    await conn.commit()


@pytest.mark.asyncio
async def test_initial_migration_creates_all_tables(tmp_path):
    db = tmp_path / "test.db"
    async with open_db(db) as conn:
        await apply_migrations(conn, MIGRATIONS)
        cur = await conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table','virtual')")
        names = {row[0] for row in await cur.fetchall()}
    assert EXPECTED_TABLES.issubset(names), f"missing: {EXPECTED_TABLES - names}"


@pytest.mark.asyncio
async def test_fts5_handles_czech_diacritics(tmp_path):
    db = tmp_path / "test.db"
    async with open_db(db) as conn:
        await apply_migrations(conn, MIGRATIONS)
        await _seed_template(conn)
        await conn.execute(
            """
            INSERT INTO annotations
              (catdv_clip_id, catdv_clip_name, template_id, model, prompt_used,
               raw_response, structured_output, clip_snapshot, created_at)
            VALUES (1, 'Polčakovi rodina', 1, 'gemini-2.5-pro',
                    'popiš scénu', '{}', '{}', '{}', '2026-05-18')
            """
        )
        await conn.commit()

        cur = await conn.execute(
            "SELECT count(*) FROM annotations_fts WHERE annotations_fts MATCH 'Polcakovi'"
        )
        assert (await cur.fetchone())[0] == 1
