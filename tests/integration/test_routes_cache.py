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


def test_cache_route_helpers_removed(monkeypatch, tmp_path: Path):
    """T3-A2: the per-request _inspector/_actions helpers are gone — cache
    handlers reach ctx.cache_inspector / ctx.cache_actions directly off the
    CoreCtx."""
    import backend.app.routes.cache as c

    assert not hasattr(c, "_inspector")
    assert not hasattr(c, "_actions")


def _seed_clip(client, *, key, proxy_path=None, proxy_size=0, ai_size=0):
    """Seed test data via the running app's DB connection."""
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
                (
                    int(key[1]) if key[1].isdigit() else 0,
                    key[0],
                    key[1],
                    proxy_path,
                    proxy_size,
                    now,
                    now,
                ),
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
                (int(key[1]) if key[1].isdigit() else 0, key[0], key[1], ai_size, now, now),
            )
        await db.commit()

    asyncio.run(_run())


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
    assert data["media_cache_cap_bytes"] == 50 * 1024**3


def test_cache_clip_status_and_evict(monkeypatch, tmp_path: Path):
    app = _make_app(monkeypatch, tmp_path)
    proxy = tmp_path / "1.mov"

    proxy.write_bytes(b"x" * 100)
    with TestClient(app) as client:
        _seed_clip(client, key=("catdv", "1"), proxy_path=str(proxy), proxy_size=100)
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
    # crumb leaf is now "Cache" (rendered via the breadcrumb macro)
    assert 'class="crumb"' in r.text
    assert "Local cache" in r.text


def test_cache_badge_partial(monkeypatch, tmp_path: Path):
    app = _make_app(monkeypatch, tmp_path)
    proxy = tmp_path / "2.mov"

    proxy.write_bytes(b"y" * 5)
    with TestClient(app) as client:
        _seed_clip(client, key=("catdv", "2"), proxy_path=str(proxy), proxy_size=5)
        r = client.get("/ui/cache-badge/catdv/2")
    assert r.status_code == 200
    assert "cache-badge" in r.text
    # all three glyphs rendered
    assert "metadata" in r.text and "media-local" in r.text and "media-ai" in r.text
    # T3-A4 pin: clip_key subscript access renders the provider + clip id
    assert 'data-provider-id="catdv"' in r.text
    assert 'data-clip-id="2"' in r.text
    # T3-A4 pin: size_bytes and pinned_by_workspaces|length appear via attribute access
    assert "5 bytes" in r.text  # proxy size in media-local title


def test_cache_popover_partial(monkeypatch, tmp_path: Path):
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        _seed_clip(client, key=("catdv", "3"), proxy_path=str(tmp_path / "3.mov"), proxy_size=999)
        r = client.get("/ui/cache-popover/catdv/3")
    assert r.status_code == 200
    assert "cache-popover" in r.text
    assert "Evict" in r.text
    # T3-A4 pin: layer names render (direct iteration over tuple[LayerStatus,...])
    assert "metadata" in r.text
    assert "media-local" in r.text
    assert "media-ai" in r.text
    # T3-A4 pin: size_bytes for media-local row renders correctly
    assert "999" in r.text
    # T3-A4 pin: totals line uses total_local_bytes and total_ai_bytes attributes
    assert "Local:" in r.text
    assert "AI:" in r.text
    # T3-A4 pin: clip_key subscript access works (header renders provider/id)
    assert "catdv/3" in r.text


def test_cache_popover_datetime_isoformat(monkeypatch, tmp_path: Path):
    """T3-A4 pin: fetched_at / last_used_at render in isoformat (with 'T'),
    not as the space-separated datetime str Python gives by default."""
    app = _make_app(monkeypatch, tmp_path)
    proxy = tmp_path / "dt.mov"
    proxy.write_bytes(b"d")
    with TestClient(app) as client:
        _seed_clip(client, key=("catdv", "99"), proxy_path=str(proxy), proxy_size=1)
        r = client.get("/ui/cache-popover/catdv/99")
    assert r.status_code == 200
    body = r.text
    # The DB stores timestamps as ISO strings with 'T' separator; once rendered
    # via the template the output must contain at least one 'T' separator inside
    # a date-looking substring (i.e. not "—" placeholder, and not a space-separated
    # datetime like "2026-05-30 12:00:00").  We look for the isoformat pattern.
    import re
    # Any ISO-8601 datetime with a T in the rendered HTML
    assert re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", body), (
        "Expected isoformat datetime with 'T' separator in popover — "
        "got: " + body[:400]
    )


def test_cache_orphans_endpoint(monkeypatch, tmp_path: Path):
    """proxy_cache with no clip_cache → orphan list."""
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        ctx = client.app.state.core_ctx
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

        asyncio.run(_seed())

        r = client.get("/api/cache/orphans")
    assert r.status_code == 200
    arr = r.json()
    assert len(arr) == 1
    assert arr[0]["clip_key"] == ["catdv", "33"]


def test_cache_page_full_render(monkeypatch, tmp_path: Path):
    """New cache page: metric strip + four tiles + tabs render."""
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        r = client.get("/cache")
    assert r.status_code == 200
    body = r.text
    assert "metric-strip" in body
    assert "cache-tabs" in body
    # All four metric tile labels
    assert "Local cache" in body
    assert "AI store" in body
    assert "Prefetch queue" in body
    assert "Orphans" in body
    # Shell shows pillset (PR1/2) and rail-cache-active
    assert "rail-btn active" in body
    # (The standalone CATALOG env-pill and the connection-dropdown footer that
    # used to surface the catalog id in the shell were both removed by design.)


def test_cache_page_orphans_tile(monkeypatch, tmp_path: Path):
    """Orphan count + bytes surface in the metric strip."""
    app = _make_app(monkeypatch, tmp_path)
    proxy = tmp_path / "orph.mov"
    proxy.write_bytes(b"o" * 42)
    with TestClient(app) as client:
        ctx = client.app.state.core_ctx
        import asyncio

        now = datetime.now(UTC).isoformat()

        async def _seed():
            await ctx.db.execute(
                """
                INSERT INTO proxy_cache
                  (catdv_clip_id, provider_id, provider_clip_id, file_path,
                   size_bytes, etag, downloaded_at, last_used_at)
                VALUES (77, 'catdv', '77', ?, 42, NULL, ?, ?)
                """,
                (str(proxy), now, now),
            )
            await ctx.db.commit()

        asyncio.run(_seed())

        r = client.get("/cache")
    assert r.status_code == 200
    body = r.text
    # one orphan, 42 bytes (rendered via bytes_human → "42 B")
    assert "Orphans" in body
    # the tile renders the count in m-value; just spot-check the bytes
    assert "42 B" in body


def test_cache_tab_local_filters_rows(monkeypatch, tmp_path: Path):
    """tab=local hides rows without media-local."""
    app = _make_app(monkeypatch, tmp_path)
    proxy = tmp_path / "a.mov"
    proxy.write_bytes(b"x" * 10)
    with TestClient(app) as client:
        # 1) Has media_local
        _seed_clip(client, key=("catdv", "1001"), proxy_path=str(proxy), proxy_size=10)
        # 2) Metadata only
        _seed_clip(client, key=("catdv", "1002"))
        r = client.get("/cache?tab=local")
    assert r.status_code == 200
    assert "catdv/1001" in r.text
    assert "catdv/1002" not in r.text
    assert 'class="vlist"' in r.text  # cache list uses the shared scaffold
    assert "/api/media/1001/thumb" in r.text  # thumbnail wired on cache rows


def test_cache_tab_ai_filters_rows(monkeypatch, tmp_path: Path):
    """tab=ai hides rows without media-ai."""
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        _seed_clip(client, key=("catdv", "2001"), ai_size=1234)
        _seed_clip(client, key=("catdv", "2002"))
        r = client.get("/cache?tab=ai")
    assert r.status_code == 200
    assert "catdv/2001" in r.text
    assert "catdv/2002" not in r.text


def test_cache_tab_swap_returns_partial(monkeypatch, tmp_path: Path):
    """HX-Request returns only the table partial, no <html>."""
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        r = client.get("/cache?tab=local", headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert "<!doctype" not in r.text.lower()
    assert "<html" not in r.text.lower()


def test_cache_tab_queue_partial(monkeypatch, tmp_path: Path):
    """tab=queue serves the queue partial (auto-refresh wrapper)."""
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        r = client.get("/cache?tab=queue", headers={"HX-Request": "true"})
    assert r.status_code == 200
    body = r.text
    assert "<!doctype" not in body.lower()
    # queue wrapper carries the auto-refresh hx-trigger
    assert 'id="prefetch-panel"' in body
    assert 'hx-trigger="every 2s"' in body


def test_bulk_evict_route(monkeypatch, tmp_path: Path):
    app = _make_app(monkeypatch, tmp_path)
    proxy = tmp_path / "4.mov"

    proxy.write_bytes(b"z" * 20)
    with TestClient(app) as client:
        _seed_clip(client, key=("catdv", "4"), proxy_path=str(proxy), proxy_size=20)
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


def test_cache_pagination_first_page(monkeypatch, tmp_path: Path):
    """limit=2 over 3 clips shows 2 rows + a next link to offset=2."""
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        _seed_clip(client, key=("catdv", "5001"))
        _seed_clip(client, key=("catdv", "5002"))
        _seed_clip(client, key=("catdv", "5003"))
        r = client.get("/cache?tab=all&limit=2", headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert r.text.count('class="row-check"') == 2
    assert "offset=2" in r.text  # next link
    assert "of 3" in r.text  # "1–2 of 3" range label


def test_cache_pagination_second_page(monkeypatch, tmp_path: Path):
    """offset=2&limit=2 over 3 clips shows the remaining 1 row + a prev link."""
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        _seed_clip(client, key=("catdv", "5001"))
        _seed_clip(client, key=("catdv", "5002"))
        _seed_clip(client, key=("catdv", "5003"))
        r = client.get("/cache?tab=all&limit=2&offset=2", headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert r.text.count('class="row-check"') == 1
    assert "offset=0" in r.text  # prev link


def test_cache_queue_tab_has_no_pager(monkeypatch, tmp_path: Path):
    """Queue tab is a live list, not paginated."""
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        r = client.get("/cache?tab=queue", headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert 'class="pager"' not in r.text
