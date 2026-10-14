"""Cached catalog list (#124): TTL refresh, offline, failure, synthetic active."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from backend.app.archive.errors import ProviderError
from backend.app.archive.model import CatalogInfo
from backend.app.repositories.catalogs import CatalogsRepo
from backend.app.services.active_catalog import catalog_name, list_catalogs
from backend.app.services.connection_monitor import ConnectionState

NOW = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)
A = CatalogInfo("881507", "AI katalog")
B = CatalogInfo("913722", "vyber pro AI", True)
C = CatalogInfo("1", "Third")


class _Archive:
    def __init__(self, result=None, exc=None):
        self.result, self.exc, self.calls = result, exc, 0

    async def list_catalogs(self):
        self.calls += 1
        if self.exc is not None:
            raise self.exc
        return self.result


class _Mon:
    is_forced = False

    def current_state(self):
        return ConnectionState.online


def _core(db):
    return SimpleNamespace(
        db=db,
        settings=SimpleNamespace(archive_provider="catdv", catdv_catalog_id=881507),
        catalogs_repo=CatalogsRepo(),
    )


def _live(archive):
    return SimpleNamespace(archive=archive, connection_monitor=_Mon())


async def _seed(db, rows, age):
    await CatalogsRepo().replace_all(
        db, provider_id="catdv", catalogs=rows, fetched_at=(NOW - age).isoformat()
    )


@pytest.mark.asyncio
async def test_fresh_rows_skip_provider(db):
    await _seed(db, [A, B], timedelta(minutes=10))
    archive = _Archive(exc=AssertionError("must not be called"))
    assert await list_catalogs(_core(db), _live(archive), "881507", now=NOW) == [A, B]
    assert archive.calls == 0


@pytest.mark.asyncio
async def test_stale_rows_refresh_when_online(db):
    await _seed(db, [A, B], timedelta(hours=2))
    got = await list_catalogs(_core(db), _live(_Archive(result=[A, B, C])), "881507", now=NOW)
    assert {c.id for c in got} == {"881507", "913722", "1"}
    assert len(await CatalogsRepo().list(db, provider_id="catdv")) == 3


@pytest.mark.asyncio
async def test_empty_table_fetches_when_online(db):
    archive = _Archive(result=[A, B])
    assert await list_catalogs(_core(db), _live(archive), "881507", now=NOW) == [A, B]
    assert archive.calls == 1


@pytest.mark.asyncio
async def test_stale_rows_served_offline(db):
    await _seed(db, [A, B], timedelta(hours=2))
    assert await list_catalogs(_core(db), None, "881507", now=NOW) == [A, B]


@pytest.mark.asyncio
async def test_refresh_failure_serves_stale(db):
    await _seed(db, [A, B], timedelta(hours=2))
    got = await list_catalogs(_core(db), _live(_Archive(exc=ProviderError("boom"))), "881507", now=NOW)
    assert got == [A, B]


@pytest.mark.asyncio
async def test_empty_offline_gives_synthetic_active(db):
    got = await list_catalogs(_core(db), None, "913722", now=NOW)
    assert got == [CatalogInfo("913722", "Catalog #913722", False)]


@pytest.mark.asyncio
async def test_unknown_active_appended(db):
    await _seed(db, [A, B], timedelta(minutes=1))
    got = await list_catalogs(_core(db), None, "999", now=NOW)
    assert got == [A, B, CatalogInfo("999", "Catalog #999", False)]


@pytest.mark.asyncio
async def test_catalog_name_lookup_and_fallback(db):
    await _seed(db, [A, B], timedelta(minutes=1))
    assert await catalog_name(_core(db), "913722") == "vyber pro AI"
    assert await catalog_name(_core(db), "42") == "Catalog #42"


@pytest.mark.asyncio
async def test_empty_refresh_keeps_cached_list(db):
    await _seed(db, [A, B], timedelta(hours=2))
    got = await list_catalogs(_core(db), _live(_Archive(result=[])), "881507", now=NOW)
    assert got == [A, B]
    assert await CatalogsRepo().list(db, provider_id="catdv") == [A, B]


@pytest.mark.asyncio
async def test_failed_refresh_backs_off_for_a_ttl(db):
    await _seed(db, [A, B], timedelta(hours=2))
    failing = _Archive(exc=ProviderError("boom"))
    await list_catalogs(_core(db), _live(failing), "881507", now=NOW)
    await list_catalogs(_core(db), _live(failing), "881507", now=NOW + timedelta(minutes=5))
    assert failing.calls == 1
