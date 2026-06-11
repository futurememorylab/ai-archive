"""MediaLocator ordering matrix. playback_source is a preference order,
not an exclusive mode: both layers are always consulted; a both-miss
raises MediaNotAvailable naming what each layer said."""

from pathlib import Path

import pytest

from backend.app.services.media_locator import (
    LocalFile,
    MediaLocator,
    MediaNotAvailable,
    RemoteUrl,
)


class FakeResolver:
    def __init__(self, path=None, exc=None):
        self.path, self.exc, self.calls = path, exc, 0

    async def path_for_clip_id(self, clip_id):
        self.calls += 1
        if self.exc:
            raise self.exc
        return self.path


class FakeRef:
    handle = "gs://catdv-proxies/clips/7.mov"


class FakeStore:
    def __init__(self, ref=None, exc=None):
        self.ref, self.exc, self.keys = ref, exc, []

    async def status(self, clip_key):
        self.keys.append(clip_key)
        if self.exc:
            raise self.exc
        return self.ref


class FakeGcs:
    def signed_url(self, handle, *, expires_s):
        return f"https://signed.example/{handle}"


def make(resolver, store, prefer):
    return MediaLocator(
        proxy_resolver=resolver, ai_store=store, gcs_service=FakeGcs(), prefer=prefer
    )


async def test_local_first_hits_local():
    resolver = FakeResolver(path=Path("/cache/7.mov"))
    store = FakeStore(ref=FakeRef())
    found = await make(resolver, store, "local").locate(7)
    assert found == LocalFile(Path("/cache/7.mov"))
    assert store.keys == []  # second layer never consulted on a hit


async def test_local_first_falls_back_to_gcs():
    resolver = FakeResolver(exc=FileNotFoundError("not cached"))
    store = FakeStore(ref=FakeRef())
    found = await make(resolver, store, "local").locate(7)
    assert isinstance(found, RemoteUrl)
    assert found.url.startswith("https://signed.example/gs://")
    assert store.keys == [("catdv", "7")]


async def test_gcs_first_skips_resolver_on_hit():
    resolver = FakeResolver(path=Path("/cache/7.mov"))
    store = FakeStore(ref=FakeRef())
    found = await make(resolver, store, "gcs").locate(7)
    assert isinstance(found, RemoteUrl)
    assert resolver.calls == 0


async def test_gcs_first_falls_back_to_local():
    resolver = FakeResolver(path=Path("/cache/7.mov"))
    store = FakeStore(ref=None)  # not uploaded
    found = await make(resolver, store, "gcs").locate(7)
    assert found == LocalFile(Path("/cache/7.mov"))


async def test_both_miss_raises_naming_layers():
    resolver = FakeResolver(exc=FileNotFoundError("not cached"))
    store = FakeStore(ref=None)
    with pytest.raises(MediaNotAvailable) as e:
        await make(resolver, store, "local").locate(7)
    assert "local cache" in str(e.value)
    assert "ai store" in str(e.value)


async def test_none_resolver_is_a_miss_not_a_crash():
    store = FakeStore(ref=None)
    with pytest.raises(MediaNotAvailable):
        await make(None, store, "local").locate(7)
