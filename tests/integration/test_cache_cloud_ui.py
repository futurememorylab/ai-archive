"""Cloud (ai_store) cache UI: the badge hides the unused local-media layer,
and the per-clip cache controls show Cache/Purge against the ai_store layer
(not the local proxy layer). Mirrors the monkeypatched-settings + seed
pattern in test_routes_cache.py.
"""

from __future__ import annotations

import importlib
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient


def _setenv(monkeypatch, tmp_path, *, media_cache: str):
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("CATDV_BASE_URL", "http://localhost:0")
    monkeypatch.setenv("CATDV_USERNAME", "")
    monkeypatch.setenv("CATDV_PASSWORD", "p")
    monkeypatch.setenv("CATDV_CATALOG_ID", "881507")
    monkeypatch.setenv("GCP_PROJECT_ID", "p")
    monkeypatch.setenv("GCS_BUCKET_NAME", "b")
    monkeypatch.setenv("PROXY_SOURCE", "rest")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MEDIA_CACHE", media_cache)


def _make_app(monkeypatch, tmp_path, *, media_cache: str):
    _setenv(monkeypatch, tmp_path, media_cache=media_cache)
    from backend.app import main as main_mod

    importlib.reload(main_mod)
    return main_mod.app


def _seed_clip(client, *, key, proxy_path=None, proxy_size=0, ai_size=0):
    ctx = client.app.state.core_ctx
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
                (int(key[1]), key[0], key[1], proxy_path, proxy_size, now, now),
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
                (int(key[1]), key[0], key[1], ai_size, now, now),
            )
        await db.commit()

    asyncio.run(_run())


def test_badge_hides_media_local_glyph_in_ai_store_mode(monkeypatch, tmp_path: Path):
    app = _make_app(monkeypatch, tmp_path, media_cache="ai_store")
    with TestClient(app) as client:
        _seed_clip(client, key=("catdv", "10"), ai_size=2048)
        r = client.get("/ui/cache-badge/catdv/10")
    assert r.status_code == 200
    # metadata + ai glyphs stay; the unused local-media layer is hidden.
    assert "glyph metadata" in r.text
    assert "glyph media-ai" in r.text
    assert "glyph media-local" not in r.text


def test_badge_shows_media_local_glyph_in_local_mode(monkeypatch, tmp_path: Path):
    app = _make_app(monkeypatch, tmp_path, media_cache="local")
    with TestClient(app) as client:
        _seed_clip(client, key=("catdv", "11"), proxy_path="/x", proxy_size=5)
        r = client.get("/ui/cache-badge/catdv/11")
    assert r.status_code == 200
    assert "glyph media-local" in r.text


def test_popover_hides_media_local_row_in_ai_store_mode(monkeypatch, tmp_path: Path):
    app = _make_app(monkeypatch, tmp_path, media_cache="ai_store")
    with TestClient(app) as client:
        _seed_clip(client, key=("catdv", "12"), ai_size=2048)
        r = client.get("/ui/cache-popover/catdv/12")
    assert r.status_code == 200
    assert "layer-media-ai" in r.text
    assert "layer-media-local" not in r.text


def test_cache_actions_ai_cached_shows_purge_not_cache(monkeypatch, tmp_path: Path):
    app = _make_app(monkeypatch, tmp_path, media_cache="ai_store")
    with TestClient(app) as client:
        _seed_clip(client, key=("catdv", "13"), ai_size=2048)
        r = client.get("/ui/cache-actions/13?kind=video")
    assert r.status_code == 200
    assert "Purge cache" in r.text
    assert "Cache video" not in r.text


def test_cache_actions_ai_absent_shows_cache_not_purge(monkeypatch, tmp_path: Path):
    app = _make_app(monkeypatch, tmp_path, media_cache="ai_store")
    with TestClient(app) as client:
        _seed_clip(client, key=("catdv", "14"))  # metadata only, no ai bytes
        r = client.get("/ui/cache-actions/14?kind=video")
    assert r.status_code == 200
    assert "Cache video" in r.text
    assert "Purge cache" not in r.text
