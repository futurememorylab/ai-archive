"""user_roles persistence — the app-side authorization layer (spec
2026-06-14-iap-roles-admin-console-design.md). Google owns the gate; this
table owns roles."""
import pytest

from backend.app.db import open_db
from backend.app.migrations_runner import apply_migrations
from backend.app.context import MIGRATIONS


@pytest.fixture
async def conn(tmp_path):
    cm = open_db(tmp_path / "t.db")
    c = await cm.__aenter__()
    await apply_migrations(c, MIGRATIONS)
    yield c
    await cm.__aexit__(None, None, None)


async def test_user_roles_table_exists(conn):
    cur = await conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='user_roles'"
    )
    assert await cur.fetchone() is not None


async def test_role_check_constraint_rejects_bad_role(conn):
    import aiosqlite
    with pytest.raises(aiosqlite.IntegrityError):
        await conn.execute(
            "INSERT INTO user_roles(email, role) VALUES ('x@y.com', 'wizard')"
        )
        await conn.commit()
