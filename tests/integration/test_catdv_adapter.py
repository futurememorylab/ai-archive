from datetime import datetime, timezone

import pytest

from backend.app.archive.model import (
    AddMarkers,
    ChangeSet,
    ClipQuery,
    Marker,
    SetField,
    Timecode,
)
from backend.app.archive.providers.catdv.adapter import CatdvArchiveAdapter
from backend.app.services.catdv_client import CatdvClient
from tests.fakes.fake_catdv import running_fake_catdv


@pytest.mark.asyncio
async def test_adapter_list_clips_returns_clip_page():
    with running_fake_catdv() as (base_url, fake):
        fake.clips[1] = {"ID": 1, "name": "Clip_1", "markers": [], "fps": 25.0}
        fake.clips[2] = {"ID": 2, "name": "Clip_2", "markers": [], "fps": 25.0}
        async with CatdvClient(base_url, "klientAI", "secret") as client:
            adapter = CatdvArchiveAdapter(client=client)
            page = await adapter.list_clips("881507", ClipQuery(limit=10))
        assert page.total == 2
        assert {c.name for c in page.items} == {"Clip_1", "Clip_2"}


@pytest.mark.asyncio
async def test_adapter_get_clip_returns_canonical_clip_with_provider_data():
    with running_fake_catdv() as (base_url, fake):
        fake.clips[42] = {
            "ID": 42,
            "name": "Clip_42",
            "fps": 25.0,
            "markers": [],
            "fields": {"pragafilm.barva": "true"},
        }
        async with CatdvClient(base_url, "klientAI", "secret") as client:
            adapter = CatdvArchiveAdapter(client=client)
            clip = await adapter.get_clip("42")
        assert clip.key == ("catdv", "42")
        assert clip.name == "Clip_42"
        assert clip.provider_data["ID"] == 42
        assert "pragafilm.barva" in clip.fields


@pytest.mark.asyncio
async def test_adapter_capabilities_reflect_catdv():
    with running_fake_catdv() as (base_url, _):
        async with CatdvClient(base_url, "klientAI", "secret") as client:
            adapter = CatdvArchiveAdapter(client=client)
        caps = adapter.capabilities
        assert caps.supports_markers is True
        assert caps.supports_etag is False
        assert caps.write_atomicity == "per-clip"
        assert "notes" in caps.supports_notes
        assert "bigNotes" in caps.supports_notes
