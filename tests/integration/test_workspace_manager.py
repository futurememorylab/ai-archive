from pathlib import Path

import pytest

from backend.app.archive.provider import ProviderCapabilities
from backend.app.repositories.workspaces import WorkspacesRepo
from backend.app.services.workspace_manager import (
    PrepEvent,
    WorkspaceManager,
)


class FakeProvider:
    id = "catdv"

    def __init__(self, *, media_is_local: bool = False, fail_on: set[str] | None = None) -> None:
        self.capabilities = ProviderCapabilities(
            supports_markers=True,
            supports_notes=frozenset({"notes"}),
            supports_field_create=False,
            supports_etag=False,
            media_is_local=media_is_local,
            write_atomicity="per-clip",
        )
        self.get_calls: list[str] = []
        self._fail_on = fail_on or set()

    async def get_clip(self, clip_id: str):
        self.get_calls.append(clip_id)
        if clip_id in self._fail_on:
            raise RuntimeError(f"boom {clip_id}")
        return None  # workspace_manager doesn't use the return value


class FakeResolver:
    def __init__(self, cache_dir: Path, *, fail_on: set[int] | None = None) -> None:
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.calls: list[int] = []
        self._fail_on = fail_on or set()

    async def path_for_clip_id(self, clip_id: int) -> Path:
        self.calls.append(clip_id)
        if clip_id in self._fail_on:
            raise FileNotFoundError(f"no proxy for {clip_id}")
        p = self.cache_dir / f"{clip_id}.mov"
        p.write_bytes(b"stub")
        return p


@pytest.fixture
def mgr(db, tmp_path):
    repo = WorkspacesRepo()
    provider = FakeProvider()
    resolver = FakeResolver(tmp_path / "proxies")
    return (
        WorkspaceManager(
            workspaces_repo=repo,
            provider=provider,
            proxy_resolver=resolver,
            db_provider=lambda: db,
        ),
        provider,
        resolver,
        repo,
    )


@pytest.mark.asyncio
async def test_create_with_clips(db, mgr):
    m, provider, resolver, repo = mgr
    ws_id = await m.create_workspace(
        name="w",
        provider_id="catdv",
        catalog_id="1",
        clip_keys=[("catdv", "1"), ("catdv", "2")],
    )
    ws = await m.get(ws_id)
    assert ws["name"] == "w"
    assert len(ws["clips"]) == 2
    assert {c["cache_state"] for c in ws["clips"]} == {"pending"}


@pytest.mark.asyncio
async def test_prepare_walks_to_ready(db, mgr):
    m, provider, resolver, repo = mgr
    ws_id = await m.create_workspace(
        name="w",
        provider_id="catdv",
        catalog_id="1",
        clip_keys=[("catdv", "1"), ("catdv", "2")],
    )
    evs: list[PrepEvent] = []
    async for ev in m.prepare(ws_id):
        evs.append(ev)

    # 3 events per clip: metadata, media, ready
    assert len(evs) == 6
    by_clip = {}
    for ev in evs:
        by_clip.setdefault(ev.clip_key, []).append(ev.state)
    assert by_clip[("catdv", "1")] == ["metadata", "media", "ready"]
    assert by_clip[("catdv", "2")] == ["metadata", "media", "ready"]

    # resolver was called for both clips
    assert sorted(resolver.calls) == [1, 2]

    # clip_cache primary pin set (writes-through inside set_primary_pin —
    # but it only touches existing rows; insert one for visibility):
    # nothing to assert here without seeding clip_cache; the call path is
    # exercised, the row update is a no-op when no row exists.

    # workspace_clips all ready
    ws = await m.get(ws_id)
    assert {c["cache_state"] for c in ws["clips"]} == {"ready"}


@pytest.mark.asyncio
async def test_prepare_partial_error_per_clip(db, tmp_path):
    repo = WorkspacesRepo()
    provider = FakeProvider(fail_on={"99"})
    resolver = FakeResolver(tmp_path / "proxies")
    m = WorkspaceManager(
        workspaces_repo=repo,
        provider=provider,
        proxy_resolver=resolver,
        db_provider=lambda: db,
    )
    ws_id = await m.create_workspace(
        name="w",
        provider_id="catdv",
        catalog_id="1",
        clip_keys=[("catdv", "1"), ("catdv", "99"), ("catdv", "2")],
    )
    evs = []
    async for ev in m.prepare(ws_id):
        evs.append(ev)
    by_clip = {}
    for ev in evs:
        by_clip.setdefault(ev.clip_key, []).append(ev.state)
    assert by_clip[("catdv", "1")][-1] == "ready"
    assert by_clip[("catdv", "99")][-1] == "error"
    assert by_clip[("catdv", "2")][-1] == "ready"

    ws = await m.get(ws_id)
    states = {(c["provider_clip_id"], c["cache_state"]) for c in ws["clips"]}
    assert ("1", "ready") in states
    assert ("99", "error") in states
    assert ("2", "ready") in states


@pytest.mark.asyncio
async def test_prepare_skips_media_when_provider_local(db, tmp_path):
    repo = WorkspacesRepo()
    provider = FakeProvider(media_is_local=True)
    resolver = FakeResolver(tmp_path / "proxies")
    m = WorkspaceManager(
        workspaces_repo=repo,
        provider=provider,
        proxy_resolver=resolver,
        db_provider=lambda: db,
    )
    ws_id = await m.create_workspace(
        name="w",
        provider_id="catdv",
        catalog_id="1",
        clip_keys=[("catdv", "1")],
    )
    evs = []
    async for ev in m.prepare(ws_id):
        evs.append(ev)
    states = [e.state for e in evs]
    assert states == ["metadata", "ready"]
    assert resolver.calls == []


@pytest.mark.asyncio
async def test_prepare_is_resumable_skips_ready(db, mgr):
    m, *_ = mgr
    ws_id = await m.create_workspace(
        name="w",
        provider_id="catdv",
        catalog_id="1",
        clip_keys=[("catdv", "1")],
    )
    async for _ in m.prepare(ws_id):
        pass
    # second prepare should skip "ready"
    evs2 = []
    async for ev in m.prepare(ws_id):
        evs2.append(ev)
    assert evs2 == []


@pytest.mark.asyncio
async def test_release_clears_pin_does_not_evict(db, mgr, tmp_path):
    from datetime import UTC, datetime

    m, provider, resolver, repo = mgr
    # seed a clip_cache row so primary-pin update is observable
    await db.execute(
        """
        INSERT INTO clip_cache (provider_id, provider_clip_id, name, catalog_id,
                                duration_secs, fps, canonical_json, fetched_at)
        VALUES ('catdv', '1', 'n', '1', 1.0, 25.0, '{}', ?)
        """,
        (datetime.now(UTC).isoformat(),),
    )
    await db.commit()

    ws_id = await m.create_workspace(
        name="w",
        provider_id="catdv",
        catalog_id="1",
        clip_keys=[("catdv", "1")],
    )
    async for _ in m.prepare(ws_id):
        pass

    # primary pin set
    cur = await db.execute(
        "SELECT pinned_to_workspace_id FROM clip_cache "
        "WHERE provider_id='catdv' AND provider_clip_id='1'"
    )
    assert (await cur.fetchone())[0] == ws_id

    await m.release(ws_id)
    # workspace_clips row removed
    ws = await m.get(ws_id)
    assert ws["clips"] == []
    # primary pin cleared but clip_cache row + media file remain
    cur = await db.execute(
        "SELECT pinned_to_workspace_id FROM clip_cache "
        "WHERE provider_id='catdv' AND provider_clip_id='1'"
    )
    assert (await cur.fetchone())[0] is None
    # media file is still on disk
    assert (resolver.cache_dir / "1.mov").exists()


@pytest.mark.asyncio
async def test_release_with_delete_workspace_removes_row(db, mgr):
    m, *_ = mgr
    ws_id = await m.create_workspace(
        name="w",
        provider_id="catdv",
        catalog_id="1",
        clip_keys=[("catdv", "1")],
    )
    await m.release(ws_id, delete_workspace=True)
    assert await m.get(ws_id) is None


@pytest.mark.asyncio
async def test_remove_clips_repoints_to_other_workspace(db, mgr):
    from datetime import UTC, datetime

    m, *_ = mgr
    # seed a clip_cache row
    await db.execute(
        """
        INSERT INTO clip_cache (provider_id, provider_clip_id, name, catalog_id,
                                duration_secs, fps, canonical_json, fetched_at)
        VALUES ('catdv', '7', 'n', '1', 1.0, 25.0, '{}', ?)
        """,
        (datetime.now(UTC).isoformat(),),
    )
    await db.commit()

    a = await m.create_workspace(
        name="a",
        provider_id="catdv",
        catalog_id="1",
        clip_keys=[("catdv", "7")],
    )
    b = await m.create_workspace(
        name="b",
        provider_id="catdv",
        catalog_id="1",
        clip_keys=[("catdv", "7")],
    )
    async for _ in m.prepare(a):
        pass
    async for _ in m.prepare(b):
        pass
    # b's prepare overwrote the pin (last-set-wins)
    cur = await db.execute(
        "SELECT pinned_to_workspace_id FROM clip_cache "
        "WHERE provider_id='catdv' AND provider_clip_id='7'"
    )
    assert (await cur.fetchone())[0] == b

    # remove from b → re-point to a
    await m.remove_clips(b, [("catdv", "7")])
    cur = await db.execute(
        "SELECT pinned_to_workspace_id FROM clip_cache "
        "WHERE provider_id='catdv' AND provider_clip_id='7'"
    )
    assert (await cur.fetchone())[0] == a
