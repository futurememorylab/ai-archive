"""0014 migration: review_items gets studio_run_id + nullable annotation_id."""

from pathlib import Path

import pytest

from backend.app.db import open_db
from backend.app.migrations_runner import apply_migrations


@pytest.mark.asyncio
async def test_0014_adds_studio_run_id_column(tmp_path: Path):
    async with open_db(tmp_path / "t.db") as conn:
        await apply_migrations(conn, Path("backend/migrations"))

        cur = await conn.execute("PRAGMA table_info(review_items)")
        rows = await cur.fetchall()
        col_by_name = {r[1]: r for r in rows}

        assert "studio_run_id" in col_by_name, "studio_run_id column missing"
        # annotation_id must allow NULL now (notnull flag = 0)
        assert col_by_name["annotation_id"][3] == 0, "annotation_id must be nullable"


@pytest.mark.asyncio
async def test_0014_check_constraint_enforces_exactly_one_owner(tmp_path: Path):
    """A row must have exactly one of (annotation_id, studio_run_id) set."""
    import aiosqlite

    async with open_db(tmp_path / "t.db") as conn:
        await apply_migrations(conn, Path("backend/migrations"))

        # Seed prerequisite rows to satisfy FKs in subsequent insert tests.
        await conn.execute(
            "INSERT INTO prompts(id, name, description, archived, created_at, updated_at) "
            "VALUES (1, 'p', NULL, 0, '2026-05-28T00:00:00Z', '2026-05-28T00:00:00Z')"
        )
        await conn.execute(
            "INSERT INTO prompt_versions(id, prompt_id, version_num, state, "
            "body, target_map, output_schema, model, created_at, updated_at) "
            "VALUES (1, 1, 1, 'draft', 'x', '{}', '{}', 'm', "
            "'2026-05-28T00:00:00Z', '2026-05-28T00:00:00Z')"
        )
        await conn.execute(
            "INSERT INTO annotations(id, catdv_clip_id, catdv_clip_name, "
            "prompt_version_id, model, prompt_used, raw_response, "
            "structured_output, clip_snapshot, created_at) "
            "VALUES (1, 1, 'c', 1, 'm', 'p', '{}', '{}', '{}', '2026-05-28T00:00:00Z')"
        )
        await conn.execute(
            "INSERT INTO studio_run(id, prompt_version_id, clip_id, status) "
            "VALUES (1, 1, 1, 'ok')"
        )
        await conn.commit()

        # Both NULL → reject
        with pytest.raises(aiosqlite.IntegrityError):
            await conn.execute(
                "INSERT INTO review_items(annotation_id, studio_run_id, "
                "catdv_clip_id, kind, proposed_value, decision) "
                "VALUES (NULL, NULL, 1, 'marker', '{}', 'pending')"
            )
            await conn.commit()
        await conn.rollback()

        # Both set → reject
        with pytest.raises(aiosqlite.IntegrityError):
            await conn.execute(
                "INSERT INTO review_items(annotation_id, studio_run_id, "
                "catdv_clip_id, kind, proposed_value, decision) "
                "VALUES (1, 1, 1, 'marker', '{}', 'pending')"
            )
            await conn.commit()
        await conn.rollback()

        # annotation_id only → ok
        await conn.execute(
            "INSERT INTO review_items(annotation_id, studio_run_id, "
            "catdv_clip_id, kind, proposed_value, decision) "
            "VALUES (1, NULL, 1, 'marker', '{}', 'pending')"
        )
        # studio_run_id only → ok
        await conn.execute(
            "INSERT INTO review_items(annotation_id, studio_run_id, "
            "catdv_clip_id, kind, proposed_value, decision) "
            "VALUES (NULL, 1, 1, 'marker', '{}', 'pending')"
        )
        await conn.commit()
