from datetime import UTC, datetime
from pathlib import Path

import pytest

from backend.app.repositories.cache_actions_log import CacheActionsLogRepo
from backend.app.services.cache_actions import CacheActions
from backend.app.services.cache_inspector import CacheInspector


async def _seed_clip_cache(db, key, *, canonical_json="{}"):
    await db.execute(
        """
        INSERT INTO clip_cache
          (provider_id, provider_clip_id, name, catalog_id,
           duration_secs, fps, canonical_json, provider_etag, fetched_at)
        VALUES (?, ?, 'n', '1', 1.0, 25.0, ?, NULL, ?)
        """,
        (key[0], key[1], canonical_json, datetime.now(UTC).isoformat()),
    )
    await db.commit()


async def _seed_proxy_cache(db, key, *, file_path, size_bytes):
    await db.execute(
        """
        INSERT INTO proxy_cache
          (catdv_clip_id, provider_id, provider_clip_id, file_path,
           size_bytes, etag, downloaded_at, last_used_at)
        VALUES (?, ?, ?, ?, ?, NULL, ?, ?)
        """,
        (
            int(key[1]) if key[1].isdigit() else 0,
            key[0], key[1], file_path, size_bytes,
            datetime.now(UTC).isoformat(),
            datetime.now(UTC).isoformat(),
        ),
    )
    await db.commit()


async def _seed_ai(db, key, *, size_bytes=10):
    await db.execute(
        """
        INSERT INTO ai_store_files
          (store_id, catdv_clip_id, provider_id, provider_clip_id,
           gcs_uri, mime_type, size_bytes, sha256,
           uploaded_at, last_used_at, expires_at)
        VALUES ('gcs:b', ?, ?, ?, 'gs://b/x', 'video/mp4',
                ?, 'abc', ?, ?, NULL)
        """,
        (
            int(key[1]) if key[1].isdigit() else 0,
            key[0], key[1], size_bytes,
            datetime.now(UTC).isoformat(),
            datetime.now(UTC).isoformat(),
        ),
    )
    await db.commit()


async def _seed_workspace_pin(db, *, ws_id, key):
    await db.execute(
        """
        INSERT INTO workspaces (id, name, provider_id, catalog_id, created_at)
        VALUES (?, ?, 'catdv', '1', ?)
        ON CONFLICT(id) DO NOTHING
        """,
        (ws_id, f"w{ws_id}", datetime.now(UTC).isoformat()),
    )
    await db.execute(
        """
        INSERT INTO workspace_clips
          (workspace_id, provider_id, provider_clip_id, added_at, cache_state)
        VALUES (?, ?, ?, ?, 'ready')
        """,
        (ws_id, key[0], key[1], datetime.now(UTC).isoformat()),
    )
    await db.commit()


async def _seed_pending(db, key):
    await db.execute(
        """
        INSERT INTO pending_operations
          (provider_id, provider_clip_id, op_kind, op_json, status,
           attempts, enqueued_at)
        VALUES (?, ?, 'set_field', '{}', 'pending', 0, ?)
        """,
        (key[0], key[1], datetime.now(UTC).isoformat()),
    )
    await db.commit()


class FakeAIStore:
    def __init__(self):
        self.evict_calls = []

    async def evict(self, key):
        self.evict_calls.append(key)


def _actions(db, *, ai_store=None):
    insp = CacheInspector(db_provider=lambda: db)
    return CacheActions(
        db_provider=lambda: db,
        inspector=insp,
        log_repo=CacheActionsLogRepo(),
        ai_store=ai_store,
    )


@pytest.mark.asyncio
async def test_evict_local_media_happy_path(db, tmp_path: Path):
    key = ("catdv", "1")
    proxy = tmp_path / "1.mov"
    proxy.write_bytes(b"x" * 100)
    await _seed_clip_cache(db, key)
    await _seed_proxy_cache(db, key, file_path=str(proxy), size_bytes=100)
    actions = _actions(db)
    out = await actions.evict_local_media(key)
    assert out.result == "ok"
    assert out.bytes_freed == 100
    assert not proxy.exists()
    cur = await db.execute(
        "SELECT COUNT(*) FROM proxy_cache "
        "WHERE provider_id='catdv' AND provider_clip_id='1'"
    )
    assert (await cur.fetchone())[0] == 0
    cur = await db.execute(
        "SELECT COUNT(*) FROM cache_actions_log WHERE result='ok'"
    )
    assert (await cur.fetchone())[0] == 1


@pytest.mark.asyncio
async def test_invariant_local_media_pinned_blocks_without_force(db, tmp_path):
    key = ("catdv", "1")
    proxy = tmp_path / "1.mov"
    proxy.write_bytes(b"x" * 100)
    await _seed_clip_cache(db, key)
    await _seed_proxy_cache(db, key, file_path=str(proxy), size_bytes=100)
    await _seed_workspace_pin(db, ws_id=7, key=key)
    actions = _actions(db)

    out = await actions.evict_local_media(key, force=False)
    assert out.result == "skipped"
    assert "pinned_by_workspaces=[7]" in (out.detail or "")
    assert proxy.exists()

    # log row written for the skip
    cur = await db.execute(
        "SELECT COUNT(*) FROM cache_actions_log WHERE result='skipped'"
    )
    assert (await cur.fetchone())[0] == 1

    # force=True bypasses the pin
    out2 = await actions.evict_local_media(key, force=True)
    assert out2.result == "ok"
    assert not proxy.exists()


@pytest.mark.asyncio
async def test_invariant_ai_blocked_by_pending_ops(db):
    key = ("catdv", "5")
    await _seed_clip_cache(db, key)
    await _seed_ai(db, key, size_bytes=42)
    await _seed_pending(db, key)
    store = FakeAIStore()
    actions = _actions(db, ai_store=store)

    out = await actions.evict_ai_media(key, force=False)
    assert out.result == "skipped"
    assert "pending_ops=1" in (out.detail or "")
    assert store.evict_calls == []

    out2 = await actions.evict_ai_media(key, force=True)
    assert out2.result == "ok"
    assert store.evict_calls == [key]


@pytest.mark.asyncio
async def test_invariant_metadata_blocked_by_pending_ops(db):
    key = ("catdv", "6")
    await _seed_clip_cache(db, key)
    await _seed_pending(db, key)
    actions = _actions(db)

    out = await actions.evict_metadata(key, force=False)
    assert out.result == "skipped"
    assert "pending_ops=" in (out.detail or "")


@pytest.mark.asyncio
async def test_invariant_metadata_blocked_by_pin(db):
    key = ("catdv", "8")
    await _seed_clip_cache(db, key)
    await _seed_workspace_pin(db, ws_id=2, key=key)
    actions = _actions(db)
    out = await actions.evict_metadata(key, force=False)
    assert out.result == "skipped"
    assert "pinned_by_workspaces" in (out.detail or "")


@pytest.mark.asyncio
async def test_evict_everywhere_force_evicts_all(db, tmp_path):
    key = ("catdv", "9")
    proxy = tmp_path / "9.mov"
    proxy.write_bytes(b"y" * 50)
    await _seed_clip_cache(db, key)
    await _seed_proxy_cache(db, key, file_path=str(proxy), size_bytes=50)
    await _seed_ai(db, key, size_bytes=30)
    await _seed_workspace_pin(db, ws_id=3, key=key)
    await _seed_pending(db, key)
    store = FakeAIStore()
    actions = _actions(db, ai_store=store)

    result = await actions.evict_clip_everywhere(key, force=True)
    assert result.errors == 0
    assert result.ok >= 3
    assert not proxy.exists()
    cur = await db.execute(
        "SELECT COUNT(*) FROM clip_cache WHERE provider_clip_id='9'"
    )
    assert (await cur.fetchone())[0] == 0
    cur = await db.execute(
        "SELECT COUNT(*) FROM proxy_cache WHERE provider_clip_id='9'"
    )
    assert (await cur.fetchone())[0] == 0
    cur = await db.execute(
        "SELECT COUNT(*) FROM ai_store_files WHERE provider_clip_id='9'"
    )
    assert (await cur.fetchone())[0] == 0
    # prominent log row written
    cur = await db.execute(
        "SELECT COUNT(*) FROM cache_actions_log "
        "WHERE action='evict_clip_everywhere_force'"
    )
    assert (await cur.fetchone())[0] == 1


@pytest.mark.asyncio
async def test_evict_everywhere_without_force_short_circuits(db, tmp_path):
    """media-ai blocked by pending → media-local + metadata not attempted."""
    key = ("catdv", "10")
    proxy = tmp_path / "10.mov"
    proxy.write_bytes(b"z" * 20)
    await _seed_clip_cache(db, key)
    await _seed_proxy_cache(db, key, file_path=str(proxy), size_bytes=20)
    await _seed_ai(db, key, size_bytes=5)
    await _seed_pending(db, key)
    store = FakeAIStore()
    actions = _actions(db, ai_store=store)

    result = await actions.evict_clip_everywhere(key, force=False)
    # only the ai-media layer's skip is recorded
    assert result.skipped == 1
    assert result.ok == 0
    # proxy file still on disk; clip_cache row still there
    assert proxy.exists()
    cur = await db.execute(
        "SELECT COUNT(*) FROM clip_cache WHERE provider_clip_id='10'"
    )
    assert (await cur.fetchone())[0] == 1


@pytest.mark.asyncio
async def test_bulk_evict_mixed(db, tmp_path):
    a, b = ("catdv", "1"), ("catdv", "2")
    pa = tmp_path / "1.mov"; pa.write_bytes(b"a" * 10)
    pb = tmp_path / "2.mov"; pb.write_bytes(b"b" * 20)
    await _seed_clip_cache(db, a)
    await _seed_clip_cache(db, b)
    await _seed_proxy_cache(db, a, file_path=str(pa), size_bytes=10)
    await _seed_proxy_cache(db, b, file_path=str(pb), size_bytes=20)
    await _seed_workspace_pin(db, ws_id=1, key=b)
    actions = _actions(db)
    result = await actions.bulk_evict([a, b], ["media-local"])
    assert result.ok == 1
    assert result.skipped == 1
    assert result.bytes_freed == 10
    assert not pa.exists()
    assert pb.exists()


@pytest.mark.asyncio
async def test_evict_orphans_drops_only_orphans(db, tmp_path):
    """proxy_cache row with no clip_cache → evicted; one with metadata stays."""
    proxy_orphan = tmp_path / "33.mov"; proxy_orphan.write_bytes(b"o")
    proxy_real = tmp_path / "1.mov"; proxy_real.write_bytes(b"r")
    await _seed_proxy_cache(db, ("catdv", "33"),
                            file_path=str(proxy_orphan), size_bytes=1)
    await _seed_clip_cache(db, ("catdv", "1"))
    await _seed_proxy_cache(db, ("catdv", "1"),
                            file_path=str(proxy_real), size_bytes=1)
    actions = _actions(db)
    result = await actions.evict_orphans()
    assert result.ok == 1
    assert not proxy_orphan.exists()
    assert proxy_real.exists()


@pytest.mark.asyncio
async def test_log_row_written_for_skips(db, tmp_path):
    key = ("catdv", "1")
    proxy = tmp_path / "1.mov"; proxy.write_bytes(b"x")
    await _seed_clip_cache(db, key)
    await _seed_proxy_cache(db, key, file_path=str(proxy), size_bytes=1)
    await _seed_workspace_pin(db, ws_id=1, key=key)
    actions = _actions(db)
    # five attempted skips
    for _ in range(5):
        await actions.evict_local_media(key, force=False)
    cur = await db.execute(
        "SELECT COUNT(*) FROM cache_actions_log WHERE result='skipped'"
    )
    assert (await cur.fetchone())[0] == 5


@pytest.mark.asyncio
async def test_who_provider_used(db, tmp_path):
    key = ("catdv", "1")
    proxy = tmp_path / "1.mov"; proxy.write_bytes(b"x")
    await _seed_clip_cache(db, key)
    await _seed_proxy_cache(db, key, file_path=str(proxy), size_bytes=1)
    insp = CacheInspector(db_provider=lambda: db)
    actions = CacheActions(
        db_provider=lambda: db,
        inspector=insp,
        log_repo=CacheActionsLogRepo(),
        who_provider=lambda: "system",
    )
    await actions.evict_local_media(key)
    cur = await db.execute(
        "SELECT who FROM cache_actions_log ORDER BY id DESC LIMIT 1"
    )
    assert (await cur.fetchone())[0] == "system"
