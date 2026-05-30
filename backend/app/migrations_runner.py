"""Simple SQL migrations runner — applies `*.sql` files under a directory
in lexical order, tracking applied names in `schema_migrations`.

Refuses to apply a `.sql` file whose numeric prefix collides with a
`.txt` sentinel in the same directory. Sentinels mark numbers that
were used and reverted (see ADR 0044, sentinel `0011_REVERTED.txt`).
"""

import logging
import re
from pathlib import Path

import aiosqlite

log = logging.getLogger(__name__)

META_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    name TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT (datetime('now'))
)
"""

_NUM_PREFIX = re.compile(r"^(\d+)_")


def _num_prefix(name: str) -> str | None:
    m = _NUM_PREFIX.match(name)
    return m.group(1) if m else None


async def apply_migrations(conn: aiosqlite.Connection, migrations_dir: Path) -> list[str]:
    """Apply any *.sql files under migrations_dir not already in schema_migrations.

    Refuses to apply a `.sql` file whose numeric prefix matches a `.txt`
    sentinel in the same directory.

    Also warns (does NOT fail) about entries in `schema_migrations` whose
    source files are no longer on disk — surfaces the dev-DB state from
    the PR #9 revert.

    Returns the names that were applied this run.
    """
    await conn.execute(META_TABLE_SQL)
    await conn.commit()

    # Build the sentinel set.
    sentinel_nums = {
        _num_prefix(p.name)
        for p in migrations_dir.glob("*.txt")
        if _num_prefix(p.name) is not None
    }

    # Detect collisions before applying anything.
    for path in sorted(migrations_dir.glob("*.sql")):
        num = _num_prefix(path.name)
        if num is not None and num in sentinel_nums:
            raise RuntimeError(
                f"migration {path.name} collides with reserved number {num} "
                f"(sentinel exists at {num}_REVERTED.txt or similar). "
                f"Use the next available number instead. See ADR 0044."
            )

    cur = await conn.execute("SELECT name FROM schema_migrations")
    applied = {row[0] for row in await cur.fetchall()}

    sql_files = sorted(p for p in migrations_dir.glob("*.sql"))
    sql_file_names = {p.name for p in sql_files}

    # Warn about orphan entries (file deleted but row remains).
    for name in applied - sql_file_names:
        log.warning(
            "schema_migrations contains %s but no matching file on disk; "
            "this is expected for reverted migrations (e.g. 0011_studio.sql). "
            "If unexpected, investigate.",
            name,
        )

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
