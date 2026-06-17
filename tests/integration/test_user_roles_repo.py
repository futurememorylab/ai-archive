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


async def test_upsert_gate_state_and_seed(conn):
    repo = UserRolesRepo()
    # seed admins (idempotent; never downgrades)
    await repo.seed_admins(conn, ["Boss@X.com", "boss@x.com"])
    # gate_state returns (role, status) in ONE read so the gate can both admit
    # and decide whether a first-sight flip is due — without writing.
    assert await repo.get_gate_state(conn, "boss@x.com") == ("admin", "active")
    assert await repo.count_admins(conn) == 1
    # re-seed is a no-op and does not duplicate
    await repo.seed_admins(conn, ["boss@x.com"])
    assert await repo.count_admins(conn) == 1

    # invited admits at the gate (and is flagged so the gate flips it); requested does not
    await repo.upsert_role(conn, "inv@x.com", "member", status="invited", granted_by="boss@x.com")
    assert await repo.get_gate_state(conn, "inv@x.com") == ("member", "invited")
    await repo.record_request(conn, "req@x.com", display_name="Req")
    assert await repo.get_gate_state(conn, "req@x.com") is None  # denied until granted
    row = await repo.get(conn, "req@x.com")
    assert row["status"] == "requested"


async def test_seed_admins_promotes_existing_non_admin_row(conn):
    """Break-glass: an email in ADMIN_EMAILS must end up active/admin even if it
    already has a row (e.g. a prior 'requested' from the denial page, or a
    console demotion). The old INSERT-OR-IGNORE seeding could not promote a stuck
    row, which locked the owners out of prod with no recovery on reboot."""
    repo = UserRolesRepo()
    # A would-be admin clicked "Request access" first → stuck 'requested' row.
    await repo.record_request(conn, "owner@x.com", display_name="Owner")
    assert await repo.get_gate_state(conn, "owner@x.com") is None  # denied
    # The deploy seeds them as a bootstrap admin → must take effect.
    await repo.seed_admins(conn, ["owner@x.com"])
    assert await repo.get_gate_state(conn, "owner@x.com") == ("admin", "active")
    row = await repo.get(conn, "owner@x.com")
    assert row["status"] == "active"
    assert row["display_name"] == "Owner"  # human-set fields are preserved


async def test_seed_admins_does_not_rewrite_an_already_correct_row(conn):
    """Steady state: once a listed admin is active/admin, a later boot's re-seed
    must NOT touch the row. Proven via a sentinel granted_at — if the no-op WHERE
    guard failed, datetime('now') would overwrite it (= a write on every boot =
    Litestream churn). The fix is permanent, not per-boot mutation."""
    repo = UserRolesRepo()
    await repo.seed_admins(conn, ["owner@x.com"])
    # Stamp a sentinel; a correct row must survive subsequent boots untouched.
    await conn.execute(
        "UPDATE user_roles SET granted_at='2000-01-01 00:00:00' WHERE email='owner@x.com'"
    )
    await conn.commit()
    await repo.seed_admins(conn, ["owner@x.com"])  # a "later boot"
    row = await repo.get(conn, "owner@x.com")
    assert row["granted_at"] == "2000-01-01 00:00:00"  # untouched → no write
    assert row["role"] == "admin" and row["status"] == "active"


async def test_seed_admins_leaves_non_listed_rows_untouched(conn):
    repo = UserRolesRepo()
    await repo.upsert_role(conn, "m@x.com", "member", status="active", granted_by="a@x.com")
    await repo.seed_admins(conn, ["owner@x.com"])
    row = await repo.get(conn, "m@x.com")
    assert row["role"] == "member" and row["status"] == "active"


async def test_activate_on_first_sight_flips_invited_to_active(conn):
    repo = UserRolesRepo()
    await repo.upsert_role(conn, "inv@x.com", "member", status="invited", granted_by="b@x.com")
    await repo.activate_on_first_sight(conn, "inv@x.com")
    row = await repo.get(conn, "inv@x.com")
    assert row["status"] == "active"
    # last_seen tracking was removed (ADR 0090): the column is gone, so it must
    # not surface as a member field.
    assert "last_seen_at" not in row


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
