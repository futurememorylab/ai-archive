"""Cache routes — JSON shapes, HTML page, HTMX badge + popover partials.

Same monkeypatched-settings pattern as test_routes_ui.py.
"""

from __future__ import annotations

import importlib
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


def _setenv(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("CATDV_BASE_URL", "http://localhost:0")
    monkeypatch.setenv("CATDV_USERNAME", "")
    monkeypatch.setenv("CATDV_PASSWORD", "p")
    monkeypatch.setenv("CATDV_CATALOG_ID", "881507")
    monkeypatch.setenv("GCP_PROJECT_ID", "p")
    monkeypatch.setenv("GCS_BUCKET_NAME", "b")
    monkeypatch.setenv("PROXY_SOURCE", "rest")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))


def _make_app(monkeypatch, tmp_path):
    _setenv(monkeypatch, tmp_path)
    from backend.app import main as main_mod
    importlib.reload(main_mod)
    return main_mod.app


def _seed_clip(client, *, key, proxy_path=None, proxy_size=0,
               ai_size=0):
    """Seed test data via the running app's DB connection."""
    ctx = client.app.state.ctx
    db = ctx.db
    import asyncio
    now = datetime.now(UTC).isoformat()

    async def _run():
        await db.execute(
            """
            INSERT INTO clip_cache
              (provider_id, provider_clip_id, name, catalog_id,
               duration_secs, fps, canonical_json, provider_etag, fetched_at)
            VALUES (?, ?, 'n', '1', 1.0, 25.0, '{}', NULL, ?)
            """,
            (key[0], key[1], now),
        )
        if proxy_path:
            await db.execute(
                """
                INSERT INTO proxy_cache
                  (catdv_clip_id, provider_id, provider_clip_id, file_path,
                   size_bytes, etag, downloaded_at, last_used_at)
                VALUES (?, ?, ?, ?, ?, NULL, ?, ?)
                """,
                (int(key[1]) if key[1].isdigit() else 0, key[0], key[1],
                 proxy_path, proxy_size, now, now),
            )
        if ai_size:
            await db.execute(
                """
                INSERT INTO ai_store_files
                  (store_id, catdv_clip_id, provider_id, provider_clip_id,
                   gcs_uri, mime_type, size_bytes, sha256,
                   uploaded_at, last_used_at, expires_at)
                VALUES ('gcs:b', ?, ?, ?, 'gs://b/x', 'video/mp4', ?, 'abc',
                        ?, ?, NULL)
                """,
                (int(key[1]) if key[1].isdigit() else 0, key[0], key[1],
                 ai_size, now, now),
            )
        await db.commit()

    asyncio.get_event_loop().run_until_complete(_run())


@pytest.mark.asyncio
async def test_cache_summary_empty(monkeypatch, tmp_path: Path):
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        r = client.get("/api/cache/summary")
    assert r.status_code == 200
    data = r.json()
    assert data["total_local_bytes"] == 0
    assert data["total_ai_bytes"] == 0
    assert data["pending_ops_count"] == 0
    # media_cache_cap_bytes follows the env default (50 GB)
    assert data["media_cache_cap_bytes"] == 50 * 1024 ** 3


def test_cache_clip_status_and_evict(monkeypatch, tmp_path: Path):
    app = _make_app(monkeypatch, tmp_path)
    proxy = tmp_path / "1.mov"

    proxy.write_bytes(b"x" * 100)
    with TestClient(app) as client:
        _seed_clip(client, key=("catdv", "1"),
                   proxy_path=str(proxy), proxy_size=100)
        r = client.get("/api/cache/clip/catdv/1")
        assert r.status_code == 200
        data = r.json()
        assert data["clip_key"] == ["catdv", "1"]
        layers = {layer["layer"]: layer for layer in data["layers"]}
        assert layers["media-local"]["present"] is True
        assert layers["media-local"]["size_bytes"] == 100

        r = client.post(
            "/api/cache/clip/catdv/1/evict",
            json={"layers": ["media-local"], "force": False},
        )
        assert r.status_code == 200
        body = r.json()
        # post-evict status: media-local now absent
        post = {layer["layer"]: layer for layer in body["status"]["layers"]}
        assert post["media-local"]["present"] is False
        assert body["result"]["ok"] == 1


def test_cache_page_renders(monkeypatch, tmp_path: Path):
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        r = client.get("/cache")
    assert r.status_code == 200
    assert "Cache management" in r.text
    assert "Local cache" in r.text


def test_cache_badge_partial(monkeypatch, tmp_path: Path):
    app = _make_app(monkeypatch, tmp_path)
    proxy = tmp_path / "2.mov"

    proxy.write_bytes(b"y" * 5)
    with TestClient(app) as client:
        _seed_clip(client, key=("catdv", "2"),
                   proxy_path=str(proxy), proxy_size=5)
        r = client.get("/ui/cache-badge/catdv/2")
    assert r.status_code == 200
    assert "cache-badge" in r.text
    # all three glyphs rendered
    assert "metadata" in r.text and "media-local" in r.text and "media-ai" in r.text


def test_cache_popover_partial(monkeypatch, tmp_path: Path):
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        _seed_clip(client, key=("catdv", "3"))
        r = client.get("/ui/cache-popover/catdv/3")
    assert r.status_code == 200
    assert "cache-popover" in r.text
    assert "Evict" in r.text


def test_cache_orphans_endpoint(monkeypatch, tmp_path: Path):
    """proxy_cache with no clip_cache → orphan list."""
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        ctx = client.app.state.ctx
        import asyncio
        proxy = tmp_path / "33.mov"

        proxy.write_bytes(b"o")
        now = datetime.now(UTC).isoformat()

        async def _seed():
            await ctx.db.execute(
                """
                INSERT INTO proxy_cache
                  (catdv_clip_id, provider_id, provider_clip_id, file_path,
                   size_bytes, etag, downloaded_at, last_used_at)
                VALUES (33, 'catdv', '33', ?, 1, NULL, ?, ?)
                """,
                (str(proxy), now, now),
            )
            await ctx.db.commit()
        asyncio.get_event_loop().run_until_complete(_seed())

        r = client.get("/api/cache/orphans")
    assert r.status_code == 200
    arr = r.json()
    assert len(arr) == 1
    assert arr[0]["clip_key"] == ["catdv", "33"]


def test_bulk_evict_route(monkeypatch, tmp_path: Path):
    app = _make_app(monkeypatch, tmp_path)
    proxy = tmp_path / "4.mov"

    proxy.write_bytes(b"z" * 20)
    with TestClient(app) as client:
        _seed_clip(client, key=("catdv", "4"),
                   proxy_path=str(proxy), proxy_size=20)
        r = client.post(
            "/api/cache/bulk-evict",
            json={
                "clip_keys": [["catdv", "4"]],
                "layers": ["media-local"],
                "force": False,
            },
        )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] == 1
    assert body["bytes_freed"] == 20
