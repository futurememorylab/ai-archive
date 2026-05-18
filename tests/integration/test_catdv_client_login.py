import pytest

from backend.app.services.catdv_client import CatdvClient, CatdvAuthError
from tests.fakes.fake_catdv import running_fake_catdv


@pytest.mark.asyncio
async def test_login_succeeds_with_valid_creds():
    with running_fake_catdv() as (base_url, fake):
        client = CatdvClient(base_url=base_url, username="klientAI", password="secret")
        async with client:
            await client.login()
            fake.clips[1] = {"ID": 1, "name": "c"}
            clip = await client.get_clip(1)
            assert clip["ID"] == 1


@pytest.mark.asyncio
async def test_login_fails_with_bad_creds():
    with running_fake_catdv() as (base_url, _):
        client = CatdvClient(base_url=base_url, username="klientAI", password="wrong")
        async with client:
            with pytest.raises(CatdvAuthError):
                await client.login()
