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


from backend.app.repositories.user_roles import UserRolesRepo


async def test_upsert_get_active_role_and_seed(conn):
    repo = UserRolesRepo()
    # seed admins (idempotent; never downgrades)
    await repo.seed_admins(conn, ["Boss@X.com", "boss@x.com"])
    assert await repo.get_active_role(conn, "boss@x.com") == "admin"
    assert await repo.count_admins(conn) == 1
    # re-seed is a no-op and does not duplicate
    await repo.seed_admins(conn, ["boss@x.com"])
    assert await repo.count_admins(conn) == 1

    # invited admits at the gate; requested does not
    await repo.upsert_role(conn, "inv@x.com", "member", status="invited", granted_by="boss@x.com")
    assert await repo.get_active_role(conn, "inv@x.com") == "member"
    await repo.record_request(conn, "req@x.com", display_name="Req")
    assert await repo.get_active_role(conn, "req@x.com") is None  # denied until granted
    row = await repo.get(conn, "req@x.com")
    assert row["status"] == "requested"


async def test_mark_seen_flips_invited_to_active(conn):
    repo = UserRolesRepo()
    await repo.upsert_role(conn, "inv@x.com", "member", status="invited", granted_by="b@x.com")
    await repo.mark_seen(conn, "inv@x.com")
    row = await repo.get(conn, "inv@x.com")
    assert row["status"] == "active"
    assert row["last_seen_at"] is not None


async def test_list_filter_and_delete(conn):
    repo = UserRolesRepo()
    await repo.upsert_role(conn, "a@x.com", "admin", status="active", granted_by=None)
    await repo.upsert_role(conn, "v@x.com", "member", status="active", granted_by="a@x.com")
    admins = await repo.list_members(conn, role="admin")
    assert [m["email"] for m in admins] == ["a@x.com"]
    found = await repo.list_members(conn, query="v@")
    assert [m["email"] for m in found] == ["v@x.com"]
    assert await repo.delete(conn, "v@x.com") == 1
    assert await repo.get(conn, "v@x.com") is None
