# tests/unit/test_migration_0015_studio_sets.py
"""0017 renames studio_folder→studio_set, adds source, preserves rows,
and enforces UNIQUE(source, name).

Note: the migration is named 0017_studio_sets.sql (not 0015) because the
on-disk tree already had 0015/0016 migrations when this work landed; the
runner applies in lexical order so the next free number is 0017. The test
file keeps its planned name for traceability."""

from pathlib import Path

import aiosqlite
import pytest

from backend.app.db import open_db
from backend.app.migrations_runner import apply_migrations

MIG = Path("backend/migrations")
STUDIO_SETS = "0017_studio_sets.sql"


async def _apply_through(conn, *, stop_before: str) -> None:
    """Apply every *.sql migration in lexical order, stopping before
    `stop_before`. Replicates the runner's per-file executescript so later
    migrations see the schema their predecessors built (e.g. 0013's
    `ALTER TABLE jobs` needs the `jobs` table from an earlier file)."""
    for p in sorted(MIG.glob("*.sql")):
        if p.name == stop_before:
            return
        await conn.executescript(p.read_text())
    await conn.commit()


@pytest.fixture
async def conn(tmp_path: Path):
    cm = open_db(tmp_path / "t.db")
    c = await cm.__aenter__()
    yield c
    await cm.__aexit__(None, None, None)


@pytest.mark.asyncio
async def test_0015_preserves_rows_and_defaults_source(conn):
    # Build the full pre-0017 schema, seed the old tables, then run 0017.
    await _apply_through(conn, stop_before=STUDIO_SETS)
    await conn.execute(
        "INSERT INTO studio_folder(id, name, created_at) VALUES (1, 'keep', '2026-01-01')"
    )
    await conn.execute(
        "INSERT INTO studio_folder_clip(folder_id, clip_id, added_at) "
        "VALUES (1, 999, '2026-01-02')"
    )
    await conn.commit()
    await conn.executescript((MIG / STUDIO_SETS).read_text())
    await conn.commit()

    cur = await conn.execute("SELECT id, name, source FROM studio_set")
    assert await cur.fetchone() == (1, "keep", "archive")
    cur = await conn.execute("SELECT set_id, clip_id FROM studio_set_clip")
    assert await cur.fetchone() == (1, 999)


@pytest.mark.asyncio
async def test_0015_unique_per_source(conn):
    await apply_migrations(conn, Path("backend/migrations"))
    await conn.execute(
        "INSERT INTO studio_set(name, source, created_at) VALUES ('dup','archive','t')"
    )
    await conn.commit()
    # Same name, same source → reject.
    with pytest.raises(aiosqlite.IntegrityError):
        await conn.execute(
            "INSERT INTO studio_set(name, source, created_at) VALUES ('dup','archive','t')"
        )
        await conn.commit()
    # Same name, different source → allowed.
    await conn.execute(
        "INSERT INTO studio_set(name, source, created_at) VALUES ('dup','uploaded','t')"
    )
    await conn.commit()
    cur = await conn.execute("SELECT COUNT(*) FROM studio_set WHERE name='dup'")
    assert (await cur.fetchone())[0] == 2
