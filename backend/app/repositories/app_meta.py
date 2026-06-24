"""app_meta — tiny key/value store for install-scoped facts.

Keys include ``install_id`` (a uuid4 stable for the lifetime of the data
dir, carried on telemetry rows) and ``vpn_desired`` (opt-in WireGuard
auto-connect preference). Repos are leaves: no service imports here.
"""

import uuid

import aiosqlite

_INSTALL_ID_KEY = "install_id"


class AppMetaRepo:
    """Generic key/value accessor over the app_meta table.

    Repos are leaves (no service imports). Generic on purpose — callers own
    the key namespace (e.g. UsageService owns ``budget_monthly_usd``)."""

    async def get(self, conn: aiosqlite.Connection, key: str) -> str | None:
        cur = await conn.execute("SELECT value FROM app_meta WHERE key = ?", (key,))
        row = await cur.fetchone()
        return row[0] if row is not None else None

    async def set(self, conn: aiosqlite.Connection, key: str, value: str) -> None:
        await conn.execute(
            "INSERT INTO app_meta(key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        await conn.commit()

    async def delete(self, conn: aiosqlite.Connection, key: str) -> None:
        await conn.execute("DELETE FROM app_meta WHERE key = ?", (key,))
        await conn.commit()


async def get_or_create_install_id(conn: aiosqlite.Connection) -> str:
    cur = await conn.execute("SELECT value FROM app_meta WHERE key = ?", (_INSTALL_ID_KEY,))
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
    cur = await conn.execute("SELECT value FROM app_meta WHERE key = ?", (_INSTALL_ID_KEY,))
    row = await cur.fetchone()
    assert row is not None
    return row[0]


_VPN_DESIRED_KEY = "vpn_desired"
_VPN_VALUES = ("on", "off")


async def get_vpn_desired(conn: aiosqlite.Connection) -> str:
    """Return the persisted desired VPN state, defaulting to 'off' (opt-in;
    keeps the cloud from grabbing the shared WG peer key on boot)."""
    cur = await conn.execute(
        "SELECT value FROM app_meta WHERE key = ?", (_VPN_DESIRED_KEY,)
    )
    row = await cur.fetchone()
    return row[0] if row is not None and row[0] in _VPN_VALUES else "off"


async def set_vpn_desired(conn: aiosqlite.Connection, value: str) -> None:
    if value not in _VPN_VALUES:
        raise ValueError(f"vpn_desired must be 'on'|'off', got {value!r}")
    await conn.execute(
        "INSERT INTO app_meta(key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (_VPN_DESIRED_KEY, value),
    )
    await conn.commit()
