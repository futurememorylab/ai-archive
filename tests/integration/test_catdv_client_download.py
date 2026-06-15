import hashlib
from pathlib import Path

import httpx
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


@pytest.mark.asyncio
async def test_download_proxy_completes_across_capped_chunks(tmp_path: Path):
    # The cloud tunnel only delivers a bounded slice per stream before it
    # stalls/cuts. download_proxy must resume via Range until the file matches
    # the server's declared total — not stop at the first short chunk.
    blob = bytes(range(256)) * 40  # 10_240 bytes, content-checkable
    with running_fake_catdv() as (base_url, fake):
        fake.proxies[7] = blob
        fake.media_chunk_cap = 1500  # ~7 resumes to assemble the whole file
        out = tmp_path / "proxy.mov"
        client = CatdvClient(base_url=base_url, username="klientAI", password="secret")
        async with client:
            await client.login()
            await client.download_proxy(clip_id=7, dest=out)
        assert out.read_bytes() == blob, "must reassemble the full proxy, not a truncation"


@pytest.mark.asyncio
async def test_download_proxy_raises_when_link_makes_no_progress(tmp_path: Path):
    # A genuinely dead link delivers zero bytes per attempt; the resume loop
    # must bail (not spin forever) so the prefetch job can mark an error.
    blob = b"Z" * 4096
    with running_fake_catdv() as (base_url, fake):
        fake.proxies[7] = blob
        fake.media_chunk_cap = 0  # every request yields 0 bytes
        out = tmp_path / "proxy.mov"
        client = CatdvClient(base_url=base_url, username="klientAI", password="secret")
        async with client:
            await client.login()
            with pytest.raises(httpx.HTTPError):
                await client.download_proxy(clip_id=7, dest=out)
        assert out.stat().st_size < len(blob)  # never claimed completion
