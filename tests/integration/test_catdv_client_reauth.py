import time

import pytest

from backend.app.services.catdv_client import CatdvClient
from tests.fakes.fake_catdv import running_fake_catdv


@pytest.mark.asyncio
async def test_reauth_on_auth_envelope_succeeds():
    with running_fake_catdv() as (base_url, fake):
        fake.clips[42] = {"ID": 42, "name": "x"}
        client = CatdvClient(base_url=base_url, username="klientAI", password="secret")
        async with client:
            await client.login()
            # Force the next call to see AUTH; retry should re-login automatically.
            fake.force_auth_until = time.time() + 0.001
            clip = await client.get_clip(42)
            assert clip["ID"] == 42
