from datetime import UTC, datetime

import pytest

from backend.app.services.cache_inspector import CacheInspector


async def _seed_clip_cache(db, key, *, name="n", canonical_json="{}"):
    await db.execute(
        """
        INSERT INTO clip_cache
          (provider_id, provider_clip_id, name, catalog_id,
           duration_secs, fps, canonical_json, provider_etag,
           fetched_at)
        VALUES (?, ?, ?, '1', 1.0, 25.0, ?, NULL, ?)
        """,
        (key[0], key[1], name, canonical_json, datetime.now(UTC).isoformat()),
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


async def _seed_ai_store_files(db, key, *, store_id, size_bytes, gcs_uri):
    await db.execute(
        """
        INSERT INTO ai_store_files
          (store_id, catdv_clip_id, provider_id, provider_clip_id,
           gcs_uri, mime_type, size_bytes, sha256,
           uploaded_at, last_used_at, expires_at)
        VALUES (?, ?, ?, ?, ?, 'video/mp4', ?, 'abc',
                ?, ?, NULL)
        """,
        (
            store_id, int(key[1]) if key[1].isdigit() else 0,
            key[0], key[1], gcs_uri, size_bytes,
            datetime.now(UTC).isoformat(),
            datetime.now(UTC).isoformat(),
        ),
    )
    await db.commit()


async def _seed_workspace(db, *, ws_id=1, name="w"):
    await db.execute(
        """
        INSERT INTO workspaces (id, name, provider_id, catalog_id, created_at)
        VALUES (?, ?, 'catdv', '1', ?)
        """,
        (ws_id, name, datetime.now(UTC).isoformat()),
    )
    await db.commit()


async def _seed_workspace_clip(db, *, ws_id, key, cache_state="ready"):
    await db.execute(
        """
        INSERT INTO workspace_clips
          (workspace_id, provider_id, provider_clip_id, added_at, cache_state)
        VALUES (?, ?, ?, ?, ?)
        """,
        (ws_id, key[0], key[1], datetime.now(UTC).isoformat(), cache_state),
    )
    await db.commit()


async def _seed_pending(db, *, key, status="pending"):
    await db.execute(
        """
        INSERT INTO pending_operations
          (provider_id, provider_clip_id, op_kind, op_json, status,
           attempts, enqueued_at)
        VALUES (?, ?, 'set_field', '{}', ?, 0, ?)
        """,
        (key[0], key[1], status, datetime.now(UTC).isoformat()),
    )
    await db.commit()


@pytest.mark.asyncio
async def test_status_for_clip_all_layers_present(db, tmp_path):
    key = ("catdv", "1")
    proxy = tmp_path / "1.mov"
    proxy.write_bytes(b"x" * 10)
    await _seed_clip_cache(db, key)
    await _seed_proxy_cache(db, key, file_path=str(proxy), size_bytes=10)
    await _seed_ai_store_files(db, key, store_id="gcs:b", size_bytes=99, gcs_uri="gs://b/1")

    insp = CacheInspector(db_provider=lambda: db, media_cache_cap_bytes=1024)
    status = await insp.status_for_clip(key)
    layers = {layer.layer: layer for layer in status.layers}
    assert layers["metadata"].present is True
    assert layers["media-local"].present is True
    assert layers["media-local"].size_bytes == 10
    assert layers["media-ai"].present is True
    assert layers["media-ai"].size_bytes == 99
    assert status.total_local_bytes == layers["metadata"].size_bytes + 10
    assert status.total_ai_bytes == 99


@pytest.mark.asyncio
async def test_status_metadata_only(db):
    key = ("catdv", "5")
    await _seed_clip_cache(db, key, name="just-meta")
    insp = CacheInspector(db_provider=lambda: db)
    s = await insp.status_for_clip(key)
    assert [layer.present for layer in s.layers] == [True, False, False]
    assert s.name == "just-meta"


@pytest.mark.asyncio
async def test_status_pinned_by_workspaces_reflected(db):
    key = ("catdv", "7")
    await _seed_clip_cache(db, key)
    await _seed_workspace(db, ws_id=1)
    await _seed_workspace(db, ws_id=2, name="other")
    await _seed_workspace_clip(db, ws_id=1, key=key)
    await _seed_workspace_clip(db, ws_id=2, key=key)
    insp = CacheInspector(db_provider=lambda: db)
    s = await insp.status_for_clip(key)
    md = next(layer for layer in s.layers if layer.layer == "metadata")
    ml = next(layer for layer in s.layers if layer.layer == "media-local")
    assert md.pinned_by_workspaces == (1, 2)
    assert ml.pinned_by_workspaces == (1, 2)


@pytest.mark.asyncio
async def test_status_evictable_blocked_by_pending_ops(db):
    key = ("catdv", "9")
    await _seed_clip_cache(db, key)
    await _seed_pending(db, key=key)
    insp = CacheInspector(db_provider=lambda: db)
    s = await insp.status_for_clip(key)
    md = next(layer for layer in s.layers if layer.layer == "metadata")
    assert md.evictable is False


@pytest.mark.asyncio
async def test_summary_totals(db, tmp_path):
    key = ("catdv", "1")
    proxy = tmp_path / "1.mov"
    proxy.write_bytes(b"x" * 10)
    await _seed_clip_cache(db, key)
    await _seed_proxy_cache(db, key, file_path=str(proxy), size_bytes=10)
    await _seed_ai_store_files(db, key, store_id="gcs:b", size_bytes=99, gcs_uri="gs://b/1")
    await _seed_workspace(db, ws_id=1)
    await _seed_workspace_clip(db, ws_id=1, key=key)
    await _seed_pending(db, key=key)
    insp = CacheInspector(db_provider=lambda: db, media_cache_cap_bytes=1000)
    summary = await insp.summary()
    assert summary.total_ai_bytes == 99
    assert summary.total_local_bytes >= 10  # >= because metadata size adds
    assert summary.counts_by_store == {"gcs:b": 1}
    assert summary.counts_by_workspace == {1: 1}
    assert summary.metadata_clip_count == 1
    assert summary.media_local_clip_count == 1
    assert summary.pending_ops_count == 1
    assert summary.media_cache_cap_bytes == 1000


@pytest.mark.asyncio
async def test_list_orphans_proxy_without_clip_cache(db, tmp_path):
    proxy = tmp_path / "33.mov"
    proxy.write_bytes(b"y")
    await _seed_proxy_cache(db, ("catdv", "33"),
                            file_path=str(proxy), size_bytes=1)
    insp = CacheInspector(db_provider=lambda: db)
    orphans = await insp.list_orphans()
    assert len(orphans) == 1
    assert orphans[0].clip_key == ("catdv", "33")


@pytest.mark.asyncio
async def test_list_orphans_ai_without_clip_cache(db):
    await _seed_ai_store_files(db, ("catdv", "55"),
                               store_id="gcs:b", size_bytes=1, gcs_uri="gs://b/55")
    insp = CacheInspector(db_provider=lambda: db)
    orphans = await insp.list_orphans()
    assert len(orphans) == 1
    assert orphans[0].clip_key == ("catdv", "55")
