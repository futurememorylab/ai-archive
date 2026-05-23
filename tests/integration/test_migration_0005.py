from pathlib import Path

import pytest

from backend.app.db import open_db
from backend.app.migrations_runner import apply_migrations

MIGRATIONS = Path(__file__).resolve().parents[2] / "backend" / "migrations"


async def _columns(conn, table: str) -> set[str]:
    cur = await conn.execute(f"PRAGMA table_info({table})")
    return {row[1] for row in await cur.fetchall()}


async def _fk_targets(conn, table: str) -> list[tuple[str, str, str]]:
    """Returns list of (target_table, from_col, to_col)."""
    cur = await conn.execute(f"PRAGMA foreign_key_list({table})")
    return [(row[2], row[3], row[4]) for row in await cur.fetchall()]


@pytest.mark.asyncio
async def test_workspaces_table_columns(tmp_path: Path):
    db = tmp_path / "t.db"
    async with open_db(db) as conn:
        await apply_migrations(conn, MIGRATIONS)
        cols = await _columns(conn, "workspaces")
    assert cols == {"id", "name", "provider_id", "catalog_id", "created_at", "description"}


@pytest.mark.asyncio
async def test_workspace_clips_table_columns(tmp_path: Path):
    db = tmp_path / "t.db"
    async with open_db(db) as conn:
        await apply_migrations(conn, MIGRATIONS)
        cols = await _columns(conn, "workspace_clips")
    assert cols == {
        "workspace_id",
        "provider_id",
        "provider_clip_id",
        "added_at",
        "cache_state",
        "cache_error",
    }


@pytest.mark.asyncio
async def test_workspace_clips_fk_to_workspaces(tmp_path: Path):
    db = tmp_path / "t.db"
    async with open_db(db) as conn:
        await apply_migrations(conn, MIGRATIONS)
        fks = await _fk_targets(conn, "workspace_clips")
    assert ("workspaces", "workspace_id", "id") in fks


@pytest.mark.asyncio
async def test_clip_cache_pinned_to_workspace_fk_exists(tmp_path: Path):
    db = tmp_path / "t.db"
    async with open_db(db) as conn:
        await apply_migrations(conn, MIGRATIONS)
        fks = await _fk_targets(conn, "clip_cache")
    assert ("workspaces", "pinned_to_workspace_id", "id") in fks


@pytest.mark.asyncio
async def test_clip_cache_rebuild_preserves_existing_rows(tmp_path: Path):
    """Apply 0001–0004, insert a clip_cache row, then apply 0005 → row survives."""
    db = tmp_path / "t.db"
    migrations_partial = tmp_path / "mig_partial"
    migrations_partial.mkdir()
    for p in sorted(MIGRATIONS.glob("*.sql")):
        if p.name == "0005_workspaces.sql":
            continue
        (migrations_partial / p.name).write_text(p.read_text())

    async with open_db(db) as conn:
        await apply_migrations(conn, migrations_partial)
        await conn.execute(
            """
            INSERT INTO clip_cache
              (provider_id, provider_clip_id, name, catalog_id,
               duration_secs, fps, canonical_json, provider_etag,
               fetched_at, pinned_to_workspace_id)
            VALUES ('catdv', '42', 'preserved', 'cat-1', 12.5, 25.0, '{}',
                    NULL, '2026-05-19', NULL)
            """
        )
        await conn.commit()
        # now apply 0005
        await apply_migrations(conn, MIGRATIONS)
        cur = await conn.execute(
            "SELECT name, catalog_id FROM clip_cache "
            "WHERE provider_id='catdv' AND provider_clip_id='42'"
        )
        row = await cur.fetchone()
    assert row is not None
    assert row[0] == "preserved"
    assert row[1] == "cat-1"


@pytest.mark.asyncio
async def test_clip_cache_catalog_index_recreated(tmp_path: Path):
    db = tmp_path / "t.db"
    async with open_db(db) as conn:
        await apply_migrations(conn, MIGRATIONS)
        cur = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='clip_cache'"
        )
        idx_names = {r[0] for r in await cur.fetchall()}
    assert "idx_clip_cache_catalog" in idx_names
