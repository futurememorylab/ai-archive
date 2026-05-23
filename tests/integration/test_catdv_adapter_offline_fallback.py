from datetime import UTC, datetime, timedelta

import pytest

from backend.app.archive.errors import RetryableError
from backend.app.archive.providers.catdv.adapter import CatdvArchiveAdapter
from backend.app.repositories.clip_cache import ClipCacheRepo
from backend.app.repositories.clip_list_cache import ClipListCacheRepo
from backend.app.repositories.field_def_cache import FieldDefCacheRepo
from backend.app.services.catdv_client import CatdvClient
from tests.fakes.fake_catdv import running_fake_catdv


def _adapter(client, db, *, is_online, ttl_hours=1, now=None):
    return CatdvArchiveAdapter(
        client=client,
        clip_cache_repo=ClipCacheRepo(),
        field_def_cache_repo=FieldDefCacheRepo(),
        clip_list_cache_repo=ClipListCacheRepo(),
        db_provider=lambda: db,
        clip_cache_ttl_hours=ttl_hours,
        clip_list_cache_ttl_minutes=1,
        clock=now or (lambda: datetime.now(UTC)),
        is_online_provider=is_online,
        default_catalog_id="881507",
    )


@pytest.mark.asyncio
async def test_get_clip_serves_stale_cache_when_offline(db):
    with running_fake_catdv() as (base_url, fake):
        fake.clips[7] = {"ID": 7, "name": "Cached", "fps": 25.0, "markers": []}
        now_holder = {"t": datetime(2026, 1, 1, tzinfo=UTC)}
        async with CatdvClient(base_url, "klientAI", "secret") as client:
            adapter = _adapter(
                client,
                db,
                is_online=lambda: True,
                ttl_hours=1,
                now=lambda: now_holder["t"],
            )
            await adapter.get_clip("7")  # warms cache

            # Advance past TTL — fresh cache miss. Flip offline.
            now_holder["t"] = now_holder["t"] + timedelta(hours=2)
            offline_adapter = _adapter(
                client,
                db,
                is_online=lambda: False,
                ttl_hours=1,
                now=lambda: now_holder["t"],
            )
            clip = await offline_adapter.get_clip("7")
            assert clip.name == "Cached"


@pytest.mark.asyncio
async def test_get_clip_offline_no_cache_raises_fatal(db):
    from backend.app.archive.errors import FatalProviderError

    async with CatdvClient("http://nowhere.invalid", "klientAI", "secret") as client:
        adapter = _adapter(client, db, is_online=lambda: False)
        with pytest.raises(FatalProviderError):
            await adapter.get_clip("999")


@pytest.mark.asyncio
async def test_get_clip_retryable_falls_back_to_stale_cache(db):
    """Online but CatDV is unreachable mid-session → stale cache wins."""
    with running_fake_catdv() as (base_url, fake):
        fake.clips[8] = {"ID": 8, "name": "Saved", "fps": 25.0, "markers": []}
        now_holder = {"t": datetime(2026, 1, 1, tzinfo=UTC)}
        async with CatdvClient(base_url, "klientAI", "secret") as client:
            warm = _adapter(client, db, is_online=lambda: True, now=lambda: now_holder["t"])
            await warm.get_clip("8")

        # Cache is stale; CatDV is now unreachable.
        now_holder["t"] = now_holder["t"] + timedelta(hours=2)
        async with CatdvClient(base_url, "klientAI", "secret") as dead_client:
            adapter = _adapter(dead_client, db, is_online=lambda: True, now=lambda: now_holder["t"])

            from backend.app.services.catdv_client import CatdvBusyError

            async def boom(*a, **kw):
                raise CatdvBusyError("simulated unreachable")

            dead_client.get_clip = boom  # type: ignore[assignment]

            clip = await adapter.get_clip("8")
            assert clip.name == "Saved"


@pytest.mark.asyncio
async def test_apply_changes_offline_raises_retryable_without_calling_client(db):
    from backend.app.archive.model import ChangeSet

    calls: list = []

    class SpyClient:
        async def get_clip(self, *a, **kw):
            calls.append(("get_clip", a, kw))
            raise AssertionError("must not be called")

        async def put_clip(self, *a, **kw):
            calls.append(("put_clip", a, kw))
            raise AssertionError("must not be called")

    adapter = _adapter(SpyClient(), db, is_online=lambda: False)  # type: ignore[arg-type]
    cs = ChangeSet(clip_key=("catdv", "1"), ops=(), expected_etag=None)
    with pytest.raises(RetryableError):
        await adapter.apply_changes(cs)
    assert calls == []


@pytest.mark.asyncio
async def test_is_online_provider_none_preserves_today_behavior(db):
    """Existing tests construct without is_online_provider; must keep working."""
    with running_fake_catdv() as (base_url, fake):
        fake.clips[1] = {"ID": 1, "name": "X", "fps": 25.0, "markers": []}
        async with CatdvClient(base_url, "klientAI", "secret") as client:
            adapter = CatdvArchiveAdapter(
                client=client,
                clip_cache_repo=ClipCacheRepo(),
                clip_list_cache_repo=ClipListCacheRepo(),
                field_def_cache_repo=FieldDefCacheRepo(),
                db_provider=lambda: db,
            )
            clip = await adapter.get_clip("1")
            assert clip.name == "X"


@pytest.mark.asyncio
async def test_list_clips_offline_paginates_from_cache(db):
    """Offline path returns ClipPage built from clip_cache."""
    from backend.app.archive.model import CanonicalClip, ClipQuery, MediaRef

    repo = ClipCacheRepo()
    for i in range(3):
        clip = CanonicalClip(
            key=("catdv", str(i)),
            name=f"Clip{i}",
            duration_secs=10.0,
            fps=25.0,
            markers=(),
            fields={},
            notes={"notes": ""},
            media=MediaRef(
                mime_type="video/quicktime",
                size_bytes=0,
                cached_path=None,
                upstream_handle=str(i),
            ),
            provider_data={},
            fetched_at=datetime.now(UTC),
        )
        await repo.upsert(db, clip=clip, catalog_id="881507")

    adapter = CatdvArchiveAdapter(
        client=None,
        clip_cache_repo=repo,
        field_def_cache_repo=FieldDefCacheRepo(),
        clip_list_cache_repo=ClipListCacheRepo(),
        db_provider=lambda: db,
        is_online_provider=lambda: False,
        default_catalog_id="881507",
    )
    page = await adapter.list_clips("881507", ClipQuery(text=None, offset=0, limit=10))
    assert page.total == 3
    assert {c.name for c in page.items} == {"Clip0", "Clip1", "Clip2"}


@pytest.mark.asyncio
async def test_list_clips_offline_search_q(db):
    from backend.app.archive.model import CanonicalClip, ClipQuery, MediaRef

    repo = ClipCacheRepo()
    for name, cid in [("Alpha", "1"), ("Beta", "2"), ("Bravo", "3")]:
        clip = CanonicalClip(
            key=("catdv", cid),
            name=name,
            duration_secs=10.0,
            fps=25.0,
            markers=(),
            fields={},
            notes={"notes": ""},
            media=MediaRef(
                mime_type="video/quicktime",
                size_bytes=0,
                cached_path=None,
                upstream_handle=cid,
            ),
            provider_data={},
            fetched_at=datetime.now(UTC),
        )
        await repo.upsert(db, clip=clip, catalog_id="881507")

    adapter = CatdvArchiveAdapter(
        client=None,
        clip_cache_repo=repo,
        field_def_cache_repo=FieldDefCacheRepo(),
        clip_list_cache_repo=ClipListCacheRepo(),
        db_provider=lambda: db,
        is_online_provider=lambda: False,
        default_catalog_id="881507",
    )
    page = await adapter.list_clips("881507", ClipQuery(text="b", offset=0, limit=10))
    assert page.total == 2
    assert {c.name for c in page.items} == {"Beta", "Bravo"}
