from datetime import datetime, timezone

import pytest

from backend.app.archive.errors import FatalProviderError
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


@pytest.mark.asyncio
async def test_apply_changes_adds_marker_via_put():
    with running_fake_catdv() as (base_url, fake):
        fake.clips[7] = {"ID": 7, "name": "c", "fps": 25.0, "markers": [], "fields": {}}
        async with CatdvClient(base_url, "klientAI", "secret") as client:
            adapter = CatdvArchiveAdapter(client=client)
            cs = ChangeSet(
                clip_key=("catdv", "7"),
                ops=[
                    AddMarkers(
                        markers=[
                            Marker(
                                name="m",
                                in_=Timecode(secs=4.0, fps=25.0),
                                out=Timecode(secs=6.0, fps=25.0),
                            )
                        ]
                    )
                ],
            )
            result = await adapter.apply_changes(cs)
        assert result.status == "ok"
        assert len(fake.put_log) == 1
        clip_id, body = fake.put_log[0]
        assert clip_id == 7
        assert len(body["markers"]) == 1
        assert body["markers"][0]["in"]["frm"] == 100


@pytest.mark.asyncio
async def test_apply_changes_setfield_writes_minimal_payload():
    with running_fake_catdv() as (base_url, fake):
        fake.clips[8] = {"ID": 8, "name": "c", "fps": 25.0, "markers": [], "fields": {}}
        async with CatdvClient(base_url, "klientAI", "secret") as client:
            adapter = CatdvArchiveAdapter(client=client)
            cs = ChangeSet(
                clip_key=("catdv", "8"),
                ops=[SetField(identifier="pragafilm.barva", value="true")],
            )
            result = await adapter.apply_changes(cs)
        assert result.status == "ok"
        _, body = fake.put_log[0]
        assert body == {"fields": {"pragafilm.barva": "true"}}


@pytest.mark.asyncio
async def test_apply_changes_returns_fatal_on_catdv_error():
    with running_fake_catdv() as (base_url, fake):
        async with CatdvClient(base_url, "klientAI", "secret") as client:
            adapter = CatdvArchiveAdapter(client=client)
            cs = ChangeSet(
                clip_key=("catdv", "99"),
                ops=[SetField(identifier="x", value=1)],
            )
            with pytest.raises(FatalProviderError):
                await adapter.apply_changes(cs)


@pytest.mark.asyncio
async def test_adapter_health_returns_ok_on_live_fake():
    with running_fake_catdv() as (base_url, _):
        async with CatdvClient(base_url, "klientAI", "secret") as client:
            adapter = CatdvArchiveAdapter(client=client)
            health = await adapter.health()
    assert health.ok is True
    assert health.latency_ms is not None
    assert health.latency_ms >= 0.0


@pytest.mark.asyncio
async def test_apply_changes_returns_ok_with_new_etag_on_success():
    with running_fake_catdv() as (base_url, fake):
        fake.clips[11] = {
            "ID": 11, "name": "c", "fps": 25.0, "markers": [], "fields": {},
            "modifyDate": "2026-05-19",
        }
        async with CatdvClient(base_url, "klientAI", "secret") as client:
            adapter = CatdvArchiveAdapter(client=client)
            cs = ChangeSet(
                clip_key=("catdv", "11"),
                ops=[SetField(identifier="x", value=1)],
            )
            result = await adapter.apply_changes(cs)
    assert result.status == "ok"
    # the fake responds with modifyDate="2026-05-18" on PUT.
    assert result.new_etag is not None


@pytest.mark.asyncio
async def test_apply_changes_returns_conflict_when_expected_etag_mismatches():
    with running_fake_catdv() as (base_url, fake):
        fake.clips[12] = {
            "ID": 12, "name": "c", "fps": 25.0, "markers": [], "fields": {},
            "modifyDate": "2026-05-19T12:00:00",
        }
        async with CatdvClient(base_url, "klientAI", "secret") as client:
            adapter = CatdvArchiveAdapter(client=client)
            cs = ChangeSet(
                clip_key=("catdv", "12"),
                ops=[SetField(identifier="x", value=1)],
                expected_etag="2026-01-01T00:00:00",
            )
            result = await adapter.apply_changes(cs)
    assert result.status == "conflict"
    assert result.conflict_detail is not None
    assert result.conflict_detail.expected_etag == "2026-01-01T00:00:00"
    assert result.conflict_detail.actual_etag == "2026-05-19T12:00:00"
    assert fake.put_log == []   # no PUT issued on conflict


@pytest.mark.asyncio
async def test_apply_changes_proceeds_when_expected_etag_is_none():
    with running_fake_catdv() as (base_url, fake):
        fake.clips[13] = {
            "ID": 13, "name": "c", "fps": 25.0, "markers": [], "fields": {},
            "modifyDate": "v-anything",
        }
        async with CatdvClient(base_url, "klientAI", "secret") as client:
            adapter = CatdvArchiveAdapter(client=client)
            cs = ChangeSet(
                clip_key=("catdv", "13"),
                ops=[SetField(identifier="x", value=1)],
                expected_etag=None,
            )
            result = await adapter.apply_changes(cs)
    assert result.status == "ok"
    assert len(fake.put_log) == 1
