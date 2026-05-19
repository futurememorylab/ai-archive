from datetime import datetime, timedelta, timezone

import pytest

from backend.app.archive.providers.catdv.adapter import CatdvArchiveAdapter
from backend.app.repositories.clip_cache import ClipCacheRepo
from backend.app.repositories.field_def_cache import FieldDefCacheRepo
from backend.app.services.catdv_client import CatdvClient
from tests.fakes.fake_catdv import running_fake_catdv


def _adapter(client, db, *, ttl_hours: int = 168, now=None):
    return CatdvArchiveAdapter(
        client=client,
        clip_cache_repo=ClipCacheRepo(),
        field_def_cache_repo=FieldDefCacheRepo(),
        db_provider=lambda: db,
        clip_cache_ttl_hours=ttl_hours,
        clock=now or (lambda: datetime.now(timezone.utc)),
    )


@pytest.mark.asyncio
async def test_get_clip_writes_through_to_cache(db):
    with running_fake_catdv() as (base_url, fake):
        fake.clips[1] = {"ID": 1, "name": "Clip_A", "fps": 25.0, "markers": []}
        async with CatdvClient(base_url, "klientAI", "secret") as client:
            adapter = _adapter(client, db)
            clip = await adapter.get_clip("1")
            assert clip.name == "Clip_A"

        cur = await db.execute(
            "SELECT name FROM clip_cache WHERE provider_id='catdv' "
            "AND provider_clip_id='1'"
        )
        row = await cur.fetchone()
        assert row is not None and row[0] == "Clip_A"


@pytest.mark.asyncio
async def test_get_clip_serves_from_cache_within_ttl(db):
    with running_fake_catdv() as (base_url, fake):
        fake.clips[2] = {"ID": 2, "name": "Original", "fps": 25.0, "markers": []}
        async with CatdvClient(base_url, "klientAI", "secret") as client:
            adapter = _adapter(client, db, ttl_hours=24)
            first = await adapter.get_clip("2")
            assert first.name == "Original"

            # Mutate upstream; cache should still serve original.
            fake.clips[2]["name"] = "Mutated"
            second = await adapter.get_clip("2")
            assert second.name == "Original"


@pytest.mark.asyncio
async def test_get_clip_bypasses_cache_when_expired(db):
    with running_fake_catdv() as (base_url, fake):
        fake.clips[3] = {"ID": 3, "name": "Old", "fps": 25.0, "markers": []}
        # Mutable clock holder.
        now_holder = {"t": datetime(2026, 1, 1, tzinfo=timezone.utc)}

        async with CatdvClient(base_url, "klientAI", "secret") as client:
            adapter = _adapter(
                client, db, ttl_hours=1, now=lambda: now_holder["t"]
            )
            await adapter.get_clip("3")

            # Advance clock past TTL and mutate upstream.
            now_holder["t"] = now_holder["t"] + timedelta(hours=2)
            fake.clips[3]["name"] = "Fresh"
            second = await adapter.get_clip("3")
            assert second.name == "Fresh"


@pytest.mark.asyncio
async def test_list_field_definitions_writes_through(db):
    with running_fake_catdv() as (base_url, fake):
        fake.field_defs = [
            {"identifier": "pragafilm.barva", "name": "Barva", "type": "BOOLEAN"},
            {
                "identifier": "pragafilm.theme",
                "name": "Theme",
                "type": "PICKLIST",
                "multi": True,
                "picklistValues": ["a", "b"],
            },
        ]
        async with CatdvClient(base_url, "klientAI", "secret") as client:
            adapter = _adapter(client, db)
            fds = await adapter.list_field_definitions()
        ids = {fd.identifier for fd in fds}
        assert ids == {"pragafilm.barva", "pragafilm.theme"}

        cur = await db.execute(
            "SELECT COUNT(*) FROM field_def_cache WHERE provider_id='catdv'"
        )
        assert (await cur.fetchone())[0] == 2


@pytest.mark.asyncio
async def test_list_field_definitions_serves_from_cache_within_ttl(db):
    with running_fake_catdv() as (base_url, fake):
        fake.field_defs = [
            {"identifier": "f", "name": "F", "type": "TEXT"},
        ]
        async with CatdvClient(base_url, "klientAI", "secret") as client:
            adapter = _adapter(client, db, ttl_hours=24)
            first = await adapter.list_field_definitions()
            assert len(first) == 1

            fake.field_defs = [
                {"identifier": "f", "name": "F", "type": "TEXT"},
                {"identifier": "g", "name": "G", "type": "TEXT"},
            ]
            second = await adapter.list_field_definitions()
            assert {fd.identifier for fd in second} == {"f"}


@pytest.mark.asyncio
async def test_adapter_without_cache_repos_still_works(db):
    """Backwards-compatible behaviour: cache deps are optional."""
    with running_fake_catdv() as (base_url, fake):
        fake.clips[9] = {"ID": 9, "name": "Plain", "fps": 25.0, "markers": []}
        async with CatdvClient(base_url, "klientAI", "secret") as client:
            adapter = CatdvArchiveAdapter(client=client)
            clip = await adapter.get_clip("9")
            assert clip.name == "Plain"
