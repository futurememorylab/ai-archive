import time
from pathlib import Path

import pytest

from backend.app.services.catdv_client import CatdvClient
from tests.fakes.fake_catdv import running_fake_catdv


@pytest.mark.asyncio
async def test_download_thumbnail_writes_jpeg(tmp_path: Path):
    blob = b"\xff\xd8\xff" + b"JPEGDATA" * 16  # fake JPEG bytes
    with running_fake_catdv() as (base_url, fake):
        fake.thumbnails[9000] = blob
        out = tmp_path / "42.jpg"
        client = CatdvClient(base_url=base_url, username="klientAI", password="secret")
        async with client:
            await client.login()
            await client.download_thumbnail(9000, out)
        assert out.read_bytes() == blob


@pytest.mark.asyncio
async def test_download_thumbnail_missing_raises(tmp_path: Path):
    with running_fake_catdv() as (base_url, fake):
        out = tmp_path / "42.jpg"
        client = CatdvClient(base_url=base_url, username="klientAI", password="secret")
        async with client:
            await client.login()
            with pytest.raises(Exception):
                await client.download_thumbnail(1234, out)
        assert not out.exists() or out.stat().st_size == 0


@pytest.mark.asyncio
async def test_download_thumbnail_reauths_then_streams(tmp_path: Path):
    blob = b"\xff\xd8\xff" + b"JPEGDATA" * 16  # fake JPEG bytes
    with running_fake_catdv() as (base_url, fake):
        fake.thumbnails[9000] = blob
        out = tmp_path / "42.jpg"
        client = CatdvClient(base_url=base_url, username="klientAI", password="secret")
        async with client:
            await client.login()
            # Force the first thumbnail GET to see an AUTH envelope; the
            # retry should re-login and stream bytes once the window elapses.
            fake.force_auth_until = time.time() + 0.001
            await client.download_thumbnail(9000, out)
        assert out.read_bytes() == blob
