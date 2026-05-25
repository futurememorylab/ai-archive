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
            assert page["totalItems"] == 5
            assert len(page["items"]) == 2

            matches = await client.list_clips(catalog_id=881507, q="clip_3")
            assert matches["totalItems"] == 1
            assert matches["items"][0]["ID"] == 3


@pytest.mark.asyncio
async def test_list_clips_requests_fields_and_markers():
    # The bulk endpoint omits user fields/markers unless asked; the clips
    # list needs year/decade + marker count, so we must request them.
    with running_fake_catdv() as (base_url, fake):
        fake.clips[1] = {"ID": 1, "name": "clip_1"}
        client = CatdvClient(base_url=base_url, username="klientAI", password="secret")
        async with client:
            await client.login()
            await client.list_clips(catalog_id=881507)
        include = fake.last_list_params.get("include", "")
        assert "clip.fields" in include
        assert "markers" in include
