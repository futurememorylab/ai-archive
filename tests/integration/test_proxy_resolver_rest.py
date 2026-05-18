from pathlib import Path

import pytest

from backend.app.services.catdv_client import CatdvClient
from backend.app.services.proxy_resolver import RestProxyResolver
from tests.fakes.fake_catdv import running_fake_catdv


@pytest.mark.asyncio
async def test_rest_resolver_downloads_on_miss(tmp_path: Path):
    blob = b"V" * 10000
    with running_fake_catdv() as (base_url, fake):
        fake.proxies[7] = blob

        client = CatdvClient(base_url=base_url, username="klientAI", password="secret")
        async with client:
            await client.login()
            cache_dir = tmp_path / "cache"
            resolver = RestProxyResolver(catdv=client, cache_dir=cache_dir)

            path = await resolver.path_for_clip_id(7)
            assert path.exists()
            assert path.read_bytes() == blob
            assert resolver.is_managed(path)


@pytest.mark.asyncio
async def test_rest_resolver_hits_cache_on_second_call(tmp_path: Path):
    blob = b"V" * 10000
    with running_fake_catdv() as (base_url, fake):
        fake.proxies[7] = blob

        client = CatdvClient(base_url=base_url, username="klientAI", password="secret")
        async with client:
            await client.login()
            cache_dir = tmp_path / "cache"
            resolver = RestProxyResolver(catdv=client, cache_dir=cache_dir)

            await resolver.path_for_clip_id(7)
            fake.proxies.pop(7)
            path = await resolver.path_for_clip_id(7)
            assert path.read_bytes() == blob
