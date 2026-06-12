"""cache_page must compute `_all_cached_keys` and `list_orphans` at most
once per render. The current code calls each twice (once for the
inventory pass and once for the metric strip), doubling the query load
on every page view."""

from __future__ import annotations

import asyncio
import importlib
from datetime import UTC, datetime

from fastapi.testclient import TestClient


def _setenv(monkeypatch, tmp_path):
    """Mirror of test_routes_cache.py::_setenv."""
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


async def _seed_clip(ctx):
    now = datetime.now(UTC).isoformat()
    await ctx.db.execute(
        "INSERT INTO clip_cache "
        "(provider_id, provider_clip_id, name, catalog_id, "
        "duration_secs, fps, canonical_json, provider_etag, fetched_at) "
        "VALUES (?, ?, 'n', '1', 1.0, 25.0, '{}', NULL, ?)",
        ("catdv", "42", now),
    )
    await ctx.db.commit()


def test_cache_page_does_not_double_compute_all_keys(tmp_path, monkeypatch):
    """T2-1 part 3: duplicate `_all_cached_keys` + `list_orphans` calls
    are gone. Asserted via call counters on each."""
    from backend.app.routes import cache as cache_route

    app = _make_app(monkeypatch, tmp_path)
    call_counts: dict[str, int] = {"_all_cached_keys": 0, "list_orphans": 0}

    orig_all_keys = cache_route._all_cached_keys

    async def _counting_all_keys(db):
        call_counts["_all_cached_keys"] += 1
        return await orig_all_keys(db)

    monkeypatch.setattr(cache_route, "_all_cached_keys", _counting_all_keys)

    with TestClient(app) as client:
        ctx = client.app.state.core_ctx
        asyncio.run(_seed_clip(ctx))

        # Monkeypatch the bound method on the live instance (TestClient
        # has already run the lifespan so ctx.cache_inspector is wired).
        orig_list_orphans = ctx.cache_inspector.list_orphans

        async def _counting_list_orphans(*args, **kwargs):
            call_counts["list_orphans"] += 1
            return await orig_list_orphans(*args, **kwargs)

        ctx.cache_inspector.list_orphans = _counting_list_orphans  # type: ignore[method-assign]

        r = client.get("/cache")
        assert r.status_code == 200

    assert call_counts["_all_cached_keys"] == 1, (
        f"expected 1 call; got {call_counts['_all_cached_keys']}"
    )
    assert call_counts["list_orphans"] == 1, (
        f"expected 1 call; got {call_counts['list_orphans']}"
    )
