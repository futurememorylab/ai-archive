"""Migration 0009 — prompts + prompt_versions, rewire annotations + jobs.

Boots a DB at the pre-0009 schema (migrations 0001-0008 only), seeds two
templates rows with referencing annotations + jobs, then applies 0009 and
asserts the new shape end-to-end.
"""
import json
from pathlib import Path

import aiosqlite
import pytest

from backend.app.db import open_db
from backend.app.migrations_runner import apply_migrations

MIGRATIONS = Path(__file__).resolve().parents[2] / "backend" / "migrations"


async def _apply_through_0008(db: aiosqlite.Connection) -> None:
    for path in sorted(MIGRATIONS.glob("*.sql")):
        if path.name >= "0009_":
            continue
        await db.executescript(path.read_text())
        await db.execute(
            "INSERT OR IGNORE INTO schema_migrations(name) VALUES (?)",
            (path.name,),
        )
    await db.commit()


@pytest.mark.asyncio
async def test_migration_0009_creates_tables_and_backfills(tmp_path: Path):
    db_path = tmp_path / "test.db"
    async with open_db(db_path) as db:
        # Pre-create the migrations meta table so direct executescript above
        # has somewhere to record names.
        await db.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations "
            "(name TEXT PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT (datetime('now')))"
        )
        await _apply_through_0008(db)

        # Seed two templates rows with all required fields.
        await db.execute(
            "INSERT INTO templates(name, description, prompt, output_schema, target_map, "
            "model, created_at, updated_at, archived) "
            "VALUES ('p1', 'd1', 'body1', ?, ?, 'gemini-2.5-pro', "
            "'2026-05-01T00:00:00+00:00', '2026-05-01T00:00:00+00:00', 0)",
            (json.dumps({"type": "object"}), json.dumps({"scenes": {"kind": "markers"}})),
        )
        await db.execute(
            "INSERT INTO templates(name, description, prompt, output_schema, target_map, "
            "model, created_at, updated_at, archived) "
            "VALUES ('p2', 'd2', 'body2', ?, ?, 'gemini-2.5-flash', "
            "'2026-05-02T00:00:00+00:00', '2026-05-02T00:00:00+00:00', 0)",
            (json.dumps({"type": "object"}), json.dumps({"scenes": {"kind": "markers"}})),
        )
        # Annotation referencing template id 1.
        await db.execute(
            "INSERT INTO annotations(catdv_clip_id, catdv_clip_name, template_id, "
            "model, prompt_used, raw_response, structured_output, clip_snapshot, created_at) "
            "VALUES (12041, 'c', 1, 'gemini-2.5-pro', 'body1', '{}', '{}', '{}', "
            "'2026-05-10T00:00:00+00:00')"
        )
        # Job referencing template id 2.
        await db.execute(
            "INSERT INTO jobs(template_id, status, created_at, total_clips) "
            "VALUES (2, 'pending', '2026-05-10T00:00:00+00:00', 0)"
        )
        await db.commit()

        # Now apply 0009.
        sql = (MIGRATIONS / "0009_prompts_and_versions.sql").read_text()
        await db.executescript(sql)
        await db.commit()

        # Assert prompts table.
        cur = await db.execute("SELECT id, name, description, archived FROM prompts ORDER BY id")
        rows = await cur.fetchall()
        assert rows == [(1, "p1", "d1", 0), (2, "p2", "d2", 0)]

        # Assert prompt_versions table — each prompt has v1@production.
        cur = await db.execute(
            "SELECT prompt_id, version_num, state, body, model FROM prompt_versions ORDER BY prompt_id"
        )
        rows = await cur.fetchall()
        assert rows == [
            (1, 1, "production", "body1", "gemini-2.5-pro"),
            (2, 1, "production", "body2", "gemini-2.5-flash"),
        ]

        # Partial unique index is in place.
        cur = await db.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_one_prod_per_prompt'"
        )
        assert (await cur.fetchone()) is not None

        # Annotation now points at prompt_versions.id.
        cur = await db.execute(
            "SELECT a.prompt_version_id, pv.prompt_id FROM annotations a "
            "JOIN prompt_versions pv ON pv.id = a.prompt_version_id"
        )
        row = await cur.fetchone()
        assert row is not None
        version_id, prompt_id = row
        assert prompt_id == 1

        # Job now points at prompt_versions.id.
        cur = await db.execute(
            "SELECT j.prompt_version_id, pv.prompt_id FROM jobs j "
            "JOIN prompt_versions pv ON pv.id = j.prompt_version_id"
        )
        row = await cur.fetchone()
        assert row is not None
        _, prompt_id = row
        assert prompt_id == 2

        # templates table is gone.
        cur = await db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='templates'"
        )
        assert (await cur.fetchone()) is None

        # Pre-migration annotations must still be searchable.
        cur = await db.execute(
            "SELECT rowid FROM annotations_fts WHERE annotations_fts MATCH 'c'"
        )
        assert await cur.fetchone() is not None

        # annotations_fts still works after the rebuild.
        await db.execute(
            "INSERT INTO annotations(catdv_clip_id, catdv_clip_name, prompt_version_id, "
            "model, prompt_used, raw_response, structured_output, clip_snapshot, created_at) "
            "VALUES (12042, 'searchable', ?, 'm', 'p', '{}', '{}', '{}', "
            "'2026-05-11T00:00:00+00:00')",
            (version_id,),
        )
        await db.commit()
        cur = await db.execute("SELECT rowid FROM annotations_fts WHERE annotations_fts MATCH 'searchable'")
        assert await cur.fetchone() is not None


@pytest.mark.asyncio
async def test_migration_0009_partial_unique_index_rejects_two_production(tmp_path: Path):
    db_path = tmp_path / "test.db"
    async with open_db(db_path) as db:
        await db.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations "
            "(name TEXT PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT (datetime('now')))"
        )
        await _apply_through_0008(db)
        sql = (MIGRATIONS / "0009_prompts_and_versions.sql").read_text()
        await db.executescript(sql)
        await db.commit()

        await db.execute(
            "INSERT INTO prompts(name, description, archived, created_at, updated_at) "
            "VALUES ('p', '', 0, '2026-05-01T00:00:00+00:00', '2026-05-01T00:00:00+00:00')"
        )
        await db.execute(
            "INSERT INTO prompt_versions(prompt_id, version_num, state, body, target_map, "
            "output_schema, model, created_at, updated_at) "
            "VALUES (1, 1, 'production', 'b', '{}', '{}', 'm', "
            "'2026-05-01T00:00:00+00:00', '2026-05-01T00:00:00+00:00')"
        )
        await db.commit()

        with pytest.raises(aiosqlite.IntegrityError):
            await db.execute(
                "INSERT INTO prompt_versions(prompt_id, version_num, state, body, target_map, "
                "output_schema, model, created_at, updated_at) "
                "VALUES (1, 2, 'production', 'b', '{}', '{}', 'm', "
                "'2026-05-01T00:00:00+00:00', '2026-05-01T00:00:00+00:00')"
            )
