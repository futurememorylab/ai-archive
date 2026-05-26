import time
from pathlib import Path

import pytest

from backend.app.services.catdv_client import CatdvClient
from tests.fakes.fake_catdv import running_fake_catdv


@pytest.mark.asyncio
async def test_download_original_writes_bytes(tmp_path: Path):
    blob = b"\xff\xd8\xff" + b"IMGDATA" * 32
    with running_fake_catdv() as (base_url, fake):
        fake.originals[881519] = blob
        out = tmp_path / "888745.jpg"
        client = CatdvClient(base_url=base_url, username="klientAI", password="secret")
        async with client:
            await client.login()
            await client.download_original(881519, out)
        assert out.read_bytes() == blob


@pytest.mark.asyncio
async def test_download_original_missing_raises(tmp_path: Path):
    with running_fake_catdv() as (base_url, fake):
        out = tmp_path / "888745.jpg"
        client = CatdvClient(base_url=base_url, username="klientAI", password="secret")
        async with client:
            await client.login()
            with pytest.raises(Exception):
                await client.download_original(999999, out)


@pytest.mark.asyncio
async def test_download_original_reauths_then_streams(tmp_path: Path):
    blob = b"\xff\xd8\xff" + b"IMGDATA" * 32
    with running_fake_catdv() as (base_url, fake):
        fake.originals[881519] = blob
        out = tmp_path / "888745.jpg"
        client = CatdvClient(base_url=base_url, username="klientAI", password="secret")
        async with client:
            await client.login()
            fake.force_auth_until = time.time() + 0.001
            await client.download_original(881519, out)
        assert out.read_bytes() == blob
