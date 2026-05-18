import hashlib
from pathlib import Path

import pytest

from backend.app.services.catdv_client import CatdvClient
from tests.fakes.fake_catdv import running_fake_catdv


@pytest.mark.asyncio
async def test_download_proxy_full(tmp_path: Path):
    blob = b"A" * (256 * 1024)
    with running_fake_catdv() as (base_url, fake):
        fake.proxies[7] = blob
        out = tmp_path / "proxy.mov"
        client = CatdvClient(base_url=base_url, username="klientAI", password="secret")
        async with client:
            await client.login()
            await client.download_proxy(clip_id=7, dest=out)
        assert out.read_bytes() == blob


@pytest.mark.asyncio
async def test_download_proxy_resumes_partial(tmp_path: Path):
    blob = b"X" * 1024 + b"Y" * 1024
    with running_fake_catdv() as (base_url, fake):
        fake.proxies[7] = blob
        out = tmp_path / "proxy.mov"
        out.write_bytes(b"X" * 1024)
        client = CatdvClient(base_url=base_url, username="klientAI", password="secret")
        async with client:
            await client.login()
            await client.download_proxy(clip_id=7, dest=out)
        assert hashlib.sha256(out.read_bytes()).hexdigest() == hashlib.sha256(blob).hexdigest()
