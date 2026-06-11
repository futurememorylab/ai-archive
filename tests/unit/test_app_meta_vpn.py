import aiosqlite
import pytest
from backend.app.repositories.app_meta import get_vpn_desired, set_vpn_desired


@pytest.fixture
async def conn():
    c = await aiosqlite.connect(":memory:")
    await c.execute("CREATE TABLE app_meta (key TEXT PRIMARY KEY, value TEXT)")
    await c.commit()
    yield c
    await c.close()


async def test_default_is_off_when_absent(conn):
    assert await get_vpn_desired(conn) == "off"


async def test_set_then_get_roundtrip(conn):
    await set_vpn_desired(conn, "on")
    assert await get_vpn_desired(conn) == "on"
    await set_vpn_desired(conn, "off")
    assert await get_vpn_desired(conn) == "off"


async def test_set_rejects_bad_value(conn):
    with pytest.raises(ValueError):
        await set_vpn_desired(conn, "maybe")


async def test_corrupt_value_in_db_falls_back_to_off(conn):
    """An unrecognised value already in the DB should return 'off' defensively."""
    await conn.execute(
        "INSERT INTO app_meta(key, value) VALUES ('vpn_desired', 'maybe')"
    )
    await conn.commit()
    assert await get_vpn_desired(conn) == "off"
