from pathlib import Path

import aiosqlite
import pytest

from backend.app.migrations_runner import apply_migrations

MIGRATIONS = Path(__file__).resolve().parents[2] / "backend" / "migrations"


@pytest.mark.asyncio
async def test_studio_tables_exist(tmp_path):
    db = tmp_path / "t.db"
    async with aiosqlite.connect(db) as conn:
        await apply_migrations(conn, MIGRATIONS)
        cur = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name IN ('testbenches','testbench_folders','testbench_items',"
            "'studio_runs','studio_run_items')"
        )
        names = {r[0] for r in await cur.fetchall()}
    assert names == {
        "testbenches", "testbench_folders", "testbench_items",
        "studio_runs", "studio_run_items",
    }


@pytest.mark.asyncio
async def test_studio_indexes_present(tmp_path):
    db = tmp_path / "t.db"
    async with aiosqlite.connect(db) as conn:
        await apply_migrations(conn, MIGRATIONS)
        cur = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND name IN ('idx_tb_folders_parent','idx_tb_items_folder',"
            "'idx_studio_runs_testbench','idx_studio_run_items_run')"
        )
        idxs = {r[0] for r in await cur.fetchall()}
    assert idxs == {
        "idx_tb_folders_parent", "idx_tb_items_folder",
        "idx_studio_runs_testbench", "idx_studio_run_items_run",
    }


@pytest.mark.asyncio
async def test_testbench_items_source_kind_check(tmp_path):
    """upload_path XOR catdv_provider_clip_id, mirrored against source_kind."""
    db = tmp_path / "t.db"
    async with aiosqlite.connect(db) as conn:
        await apply_migrations(conn, MIGRATIONS)
        await conn.execute(
            "INSERT INTO testbenches (id, name, archived, created_at, updated_at) "
            "VALUES (1, 'tb', 0, '2026-01-01', '2026-01-01')"
        )
        await conn.execute(
            "INSERT INTO testbench_folders (id, testbench_id, parent_id, name, sort_index, created_at) "
            "VALUES (1, 1, NULL, 'root', 0, '2026-01-01')"
        )
        await conn.execute(
            "INSERT INTO testbench_items (folder_id, source_kind, upload_path, upload_orig_name, "
            "display_name, created_at) VALUES (1, 'upload', 'a.mp4', 'a.mp4', 'a', '2026-01-01')"
        )
        with pytest.raises(aiosqlite.IntegrityError):
            await conn.execute(
                "INSERT INTO testbench_items (folder_id, source_kind, upload_path, "
                "catdv_provider_clip_id, display_name, created_at) "
                "VALUES (1, 'upload', 'b.mp4', '999', 'b', '2026-01-01')"
            )
