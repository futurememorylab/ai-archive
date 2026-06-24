"""AppMetaRepo: generic key/value get/set/overwrite/delete round-trip."""

import pytest

from backend.app.repositories.app_meta import AppMetaRepo


@pytest.mark.asyncio
async def test_get_missing_returns_none(db):
    repo = AppMetaRepo()
    assert await repo.get(db, "nope") is None


@pytest.mark.asyncio
async def test_set_then_get_roundtrip(db):
    repo = AppMetaRepo()
    await repo.set(db, "k", "v1")
    assert await repo.get(db, "k") == "v1"


@pytest.mark.asyncio
async def test_set_overwrites_existing(db):
    repo = AppMetaRepo()
    await repo.set(db, "k", "v1")
    await repo.set(db, "k", "v2")
    assert await repo.get(db, "k") == "v2"


@pytest.mark.asyncio
async def test_delete_removes_key(db):
    repo = AppMetaRepo()
    await repo.set(db, "k", "v")
    await repo.delete(db, "k")
    assert await repo.get(db, "k") is None
    # Deleting an absent key is a no-op (no error).
    await repo.delete(db, "k")
