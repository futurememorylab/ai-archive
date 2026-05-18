import pytest

from backend.app.services.catdv_client import CatdvClient
from tests.fakes.fake_catdv import running_fake_catdv


@pytest.mark.asyncio
async def test_list_clips_with_paging_and_search():
    with running_fake_catdv() as (base_url, fake):
        for i in range(5):
            fake.clips[i] = {"ID": i, "name": f"clip_{i}"}

        client = CatdvClient(base_url=base_url, username="klientAI", password="secret")
        async with client:
            await client.login()
            page = await client.list_clips(catalog_id=881507, offset=0, limit=2)
            assert page["total"] == 5
            assert len(page["clips"]) == 2

            matches = await client.list_clips(catalog_id=881507, q="clip_3")
            assert matches["total"] == 1
            assert matches["clips"][0]["ID"] == 3
