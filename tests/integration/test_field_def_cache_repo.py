import pytest

from backend.app.archive.model import FieldDef
from backend.app.repositories.field_def_cache import FieldDefCacheRepo


def _fd(identifier: str = "pragafilm.barva", *, name: str = "Barva") -> FieldDef:
    return FieldDef(
        identifier=identifier,
        name=name,
        type="bool",
        is_multi=False,
        is_editable=True,
        picklist_values=None,
        provider_data={"raw": True},
    )


@pytest.mark.asyncio
async def test_upsert_and_get_round_trip(db):
    repo = FieldDefCacheRepo()
    await repo.upsert(db, provider_id="catdv", field_def=_fd())
    got = await repo.get_by_key(
        db, provider_id="catdv", identifier="pragafilm.barva"
    )
    assert got is not None
    assert got.identifier == "pragafilm.barva"
    assert got.name == "Barva"
    assert got.type == "bool"
    assert got.provider_data == {"raw": True}


@pytest.mark.asyncio
async def test_upsert_picklist_values_round_trip(db):
    repo = FieldDefCacheRepo()
    fd = FieldDef(
        identifier="t.theme",
        name="Theme",
        type="multi-picklist",
        is_multi=True,
        is_editable=True,
        picklist_values=("a", "b"),
    )
    await repo.upsert(db, provider_id="catdv", field_def=fd)
    got = await repo.get_by_key(db, provider_id="catdv", identifier="t.theme")
    assert got is not None
    assert got.picklist_values == ("a", "b")
    assert got.is_multi is True


@pytest.mark.asyncio
async def test_list_for_provider(db):
    repo = FieldDefCacheRepo()
    await repo.upsert(db, provider_id="catdv", field_def=_fd("a"))
    await repo.upsert(db, provider_id="catdv", field_def=_fd("b"))
    await repo.upsert(db, provider_id="fs", field_def=_fd("c"))
    rows = await repo.list_for_provider(db, provider_id="catdv")
    assert {fd.identifier for fd in rows} == {"a", "b"}


@pytest.mark.asyncio
async def test_replace_all_for_provider_overwrites_existing(db):
    repo = FieldDefCacheRepo()
    await repo.upsert(db, provider_id="catdv", field_def=_fd("old"))
    await repo.replace_all_for_provider(
        db, provider_id="catdv", field_defs=[_fd("new1"), _fd("new2")]
    )
    rows = await repo.list_for_provider(db, provider_id="catdv")
    assert {fd.identifier for fd in rows} == {"new1", "new2"}


@pytest.mark.asyncio
async def test_delete_by_key(db):
    repo = FieldDefCacheRepo()
    await repo.upsert(db, provider_id="catdv", field_def=_fd("x"))
    await repo.delete_by_key(db, provider_id="catdv", identifier="x")
    assert await repo.get_by_key(db, provider_id="catdv", identifier="x") is None


@pytest.mark.asyncio
async def test_latest_fetched_at(db):
    repo = FieldDefCacheRepo()
    assert await repo.latest_fetched_at(db, provider_id="catdv") is None
    await repo.upsert(db, provider_id="catdv", field_def=_fd("x"))
    ts = await repo.latest_fetched_at(db, provider_id="catdv")
    assert ts is not None
