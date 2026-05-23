"""Simple SQL migrations runner — applies `*.sql` files under a directory
in lexical order, tracking applied names in `schema_migrations`."""

from pathlib import Path

import aiosqlite

META_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    name TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT (datetime('now'))
)
"""


async def apply_migrations(conn: aiosqlite.Connection, migrations_dir: Path) -> list[str]:
    """Apply any *.sql files under migrations_dir not already in schema_migrations.

    Returns the names that were applied this run.
    """
    await conn.execute(META_TABLE_SQL)
    await conn.commit()

    cur = await conn.execute("SELECT name FROM schema_migrations")
    applied = {row[0] for row in await cur.fetchall()}

    sql_files = sorted(p for p in migrations_dir.glob("*.sql"))
    newly_applied: list[str] = []
    for path in sql_files:
        if path.name in applied:
            continue
        sql = path.read_text()
        await conn.executescript(sql)
        await conn.execute("INSERT INTO schema_migrations(name) VALUES (?)", (path.name,))
        await conn.commit()
        newly_applied.append(path.name)
    return newly_applied
