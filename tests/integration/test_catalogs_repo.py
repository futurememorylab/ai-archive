"""CatalogsRepo (#124) — DB cache of the provider's catalog list."""

import pytest

from backend.app.archive.model import CatalogInfo
from backend.app.repositories.catalogs import CatalogsRepo

A = CatalogInfo("881507", "AI katalog", False)
B = CatalogInfo("913722", "vyber pro AI", True)


@pytest.mark.asyncio
async def test_replace_all_then_list_round_trip(db):
    repo = CatalogsRepo()
    await repo.replace_all(
        db, provider_id="catdv", catalogs=[B, A], fetched_at="2026-10-14T10:00:00+00:00"
    )
    assert await repo.list(db, provider_id="catdv") == [A, B]
    assert await repo.fetched_at(db, provider_id="catdv") == "2026-10-14T10:00:00+00:00"


@pytest.mark.asyncio
async def test_replace_all_drops_vanished_catalogs(db):
    repo = CatalogsRepo()
    await repo.replace_all(db, provider_id="catdv", catalogs=[A, B], fetched_at="t1")
    await repo.replace_all(db, provider_id="catdv", catalogs=[B], fetched_at="t2")
    assert await repo.list(db, provider_id="catdv") == [B]


@pytest.mark.asyncio
async def test_empty_provider_fetched_at_is_none(db):
    assert await CatalogsRepo().fetched_at(db, provider_id="catdv") is None


@pytest.mark.asyncio
async def test_list_is_scoped_by_provider(db):
    repo = CatalogsRepo()
    await repo.replace_all(db, provider_id="fs", catalogs=[A], fetched_at="t1")
    assert await repo.list(db, provider_id="catdv") == []


@pytest.mark.asyncio
async def test_failed_replace_rolls_back(db):
    import sqlite3

    repo = CatalogsRepo()
    await repo.replace_all(db, provider_id="catdv", catalogs=[A, B], fetched_at="t1")
    with pytest.raises(sqlite3.IntegrityError):
        await repo.replace_all(db, provider_id="catdv", catalogs=[A, A], fetched_at="t2")
    assert not db.in_transaction
    assert await repo.list(db, provider_id="catdv") == [A, B]
