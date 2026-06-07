"""app_meta — tiny key/value store for install-scoped facts.

Today it holds exactly one key: ``install_id``, a uuid4 generated on
first read and stable for the lifetime of the data dir. Telemetry rows
carry it so records stay attributable if they ever leave this machine
(Phase 2 collector). Repos are leaves: no service imports here.
"""

import uuid

import aiosqlite

_INSTALL_ID_KEY = "install_id"


async def get_or_create_install_id(conn: aiosqlite.Connection) -> str:
    cur = await conn.execute(
        "SELECT value FROM app_meta WHERE key = ?", (_INSTALL_ID_KEY,)
    )
    row = await cur.fetchone()
    if row is not None:
        return row[0]
    value = str(uuid.uuid4())
    # INSERT OR IGNORE + re-read guards the (unlikely) concurrent first call.
    await conn.execute(
        "INSERT OR IGNORE INTO app_meta(key, value) VALUES (?, ?)",
        (_INSTALL_ID_KEY, value),
    )
    await conn.commit()
    cur = await conn.execute(
        "SELECT value FROM app_meta WHERE key = ?", (_INSTALL_ID_KEY,)
    )
    row = await cur.fetchone()
    assert row is not None
    return row[0]
