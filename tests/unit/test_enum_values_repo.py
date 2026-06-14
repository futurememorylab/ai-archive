from pathlib import Path

import aiosqlite
import pytest

from backend.app.db import open_db
from backend.app.migrations_runner import apply_migrations
from backend.app.repositories.enum_values import EnumValuesRepo


@pytest.fixture
async def db(tmp_path: Path):
    cm = open_db(tmp_path / "t.db")
    conn = await cm.__aenter__()
    await apply_migrations(conn, Path("backend/migrations"))
    yield conn
    await cm.__aexit__(None, None, None)


@pytest.mark.asyncio
async def test_add_list_live_and_soft_delete(db: aiosqlite.Connection):
    repo = EnumValuesRepo()
    await repo.add_value(db, "k", "a", label=None, commit=True)
    await repo.add_value(db, "k", "b", label="Bee", commit=True)
    live = await repo.live_values(db, "k")
    assert [r.value for r in live] == ["a", "b"]

    await repo.soft_delete(db, "k", "a", commit=True)
    live = await repo.live_values(db, "k")
    assert [r.value for r in live] == ["b"]
    # tombstone still present in all_rows
    allrows = await repo.all_rows(db, "k")
    assert {r.value: r.removed for r in allrows} == {"a": 1, "b": 0}


@pytest.mark.asyncio
async def test_add_duplicate_raises(db: aiosqlite.Connection):
    repo = EnumValuesRepo()
    await repo.add_value(db, "k", "a", label=None, commit=True)
    with pytest.raises(aiosqlite.IntegrityError):
        await repo.add_value(db, "k", "a", label=None, commit=True)


@pytest.mark.asyncio
async def test_upsert_seed_idempotent_and_no_resurrection(db: aiosqlite.Connection):
    repo = EnumValuesRepo()
    from backend.app.enums.registry import EnumValueSpec

    spec = EnumValueSpec("a", default=True)
    await repo.upsert_seed(db, "k", spec, sort_order=0, commit=True)
    await repo.upsert_seed(db, "k", spec, sort_order=0, commit=True)  # idempotent
    assert len(await repo.all_rows(db, "k")) == 1

    await repo.soft_delete(db, "k", "a", commit=True)
    await repo.upsert_seed(db, "k", spec, sort_order=0, commit=True)  # must NOT revive
    live = await repo.live_values(db, "k")
    assert live == []


@pytest.mark.asyncio
async def test_count_enabled(db: aiosqlite.Connection):
    repo = EnumValuesRepo()
    await repo.add_value(db, "k", "a", label=None, commit=True)
    await repo.add_value(db, "k", "b", label=None, commit=True)
    await repo.set_enabled(db, "k", "b", enabled=False, commit=True)
    assert await repo.count_enabled(db, "k") == 1
