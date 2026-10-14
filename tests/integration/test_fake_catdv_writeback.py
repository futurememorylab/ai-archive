"""The in-process FakeCatdv must mirror the real server's PUT semantics the
app relies on (#123):

  * a supplied ``markers`` array replaces the clip's whole marker list, and
  * a supplied ``fields`` map merges PER KEY — keys not in the body are left
    untouched.

Task 1's live probe would confirm these against the real server; until it is
run, the fake is where the per-key-merge assumption is pinned, so a regression
in the ownership code (which PUTs only the one owned field) is caught here.
"""

import pytest

from backend.app.services.catdv_client import CatdvClient
from tests.fakes.fake_catdv import running_fake_catdv


@pytest.mark.asyncio
async def test_put_markers_replaces_the_whole_list():
    with running_fake_catdv() as (base_url, fake):
        fake.clips[1] = {
            "ID": 1,
            "name": "c",
            "markers": [{"name": "old", "category": "AI"}],
            "fields": {},
        }
        async with CatdvClient(base_url, "klientAI", "secret") as client:
            await client.login()
            await client.put_clip(1, {"markers": [{"name": "new", "category": "AI"}]})
        assert fake.clips[1]["markers"] == [{"name": "new", "category": "AI"}]


@pytest.mark.asyncio
async def test_put_fields_merges_per_key():
    with running_fake_catdv() as (base_url, fake):
        fake.clips[2] = {
            "ID": 2,
            "name": "c",
            "markers": [],
            "fields": {
                "pragafilm.popis.materialu": "human",
                "pragafilm.dekáda.natočení": "30.léta",
            },
        }
        async with CatdvClient(base_url, "klientAI", "secret") as client:
            await client.login()
            await client.put_clip(2, {"fields": {"pragafilm.popis.ai": "AI summary"}})
        fields = fake.clips[2]["fields"]
        # the one written key landed...
        assert fields["pragafilm.popis.ai"] == "AI summary"
        # ...and the other keys are untouched (per-key merge, not wholesale).
        assert fields["pragafilm.popis.materialu"] == "human"
        assert fields["pragafilm.dekáda.natočení"] == "30.léta"


@pytest.mark.asyncio
async def test_put_non_field_keys_still_replace_wholesale():
    with running_fake_catdv() as (base_url, fake):
        fake.clips[3] = {"ID": 3, "name": "c", "markers": [], "fields": {}, "notes": "old"}
        async with CatdvClient(base_url, "klientAI", "secret") as client:
            await client.login()
            await client.put_clip(3, {"notes": "new"})
        assert fake.clips[3]["notes"] == "new"
