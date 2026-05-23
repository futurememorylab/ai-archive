"""End-to-end smoke for offline mode.

Two scenarios:
- Forced offline via ``CATDV_OFFLINE=true`` — never touches the network.
- Auto-fallback when configured CatDV is unreachable at startup.

The codebase's ``main.app`` is module-level, not a factory. We reload
``backend.app.main`` with the right env then drive it via TestClient, the
same pattern used by ``test_routes_connection.py``.
"""

import importlib

import pytest
from fastapi.testclient import TestClient

import backend.app.services.gcs as gcs_mod
import backend.app.services.gemini as gemini_mod


class _StubGcs:
    def __init__(self, *args, **kwargs):
        self.bucket_name = "b"
        self._bucket = type("FakeBucket", (), {"exists": staticmethod(lambda: True)})()


class _StubGemini:
    def __init__(self, *args, **kwargs):
        pass


def _env(monkeypatch, tmp_path, **overrides):
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("CATDV_BASE_URL", "http://127.0.0.1:1")
    monkeypatch.setenv("CATDV_USERNAME", "u")
    monkeypatch.setenv("CATDV_PASSWORD", "p")
    monkeypatch.setenv("CATDV_CATALOG_ID", "881507")
    monkeypatch.setenv("GCP_PROJECT_ID", "p")
    monkeypatch.setenv("GCS_BUCKET_NAME", "b")
    monkeypatch.setenv("PROXY_SOURCE", "rest")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setattr(gcs_mod, "GcsService", _StubGcs)
    monkeypatch.setattr(gemini_mod, "GeminiService", _StubGemini)
    for k, v in overrides.items():
        monkeypatch.setenv(k, v)


def _reload_app():
    import backend.app.main as main_mod

    importlib.reload(main_mod)
    return main_mod.app


@pytest.mark.asyncio
async def test_forced_offline_boots_and_serves_health(tmp_path, monkeypatch):
    _env(monkeypatch, tmp_path, CATDV_OFFLINE="true")

    app = _reload_app()
    with TestClient(app) as c:
        body = c.get("/api/health").json()
        assert body["mode"] == "forced_offline"

        r = c.post("/api/connection/retry")
        assert r.status_code == 409

        ctx = c.app.state.ctx
        # Forced offline never creates a CatDV client → no seat held.
        assert ctx.catdv is None
        # Proxy resolver is the cache-only flavour, so no proxy downloads.
        from backend.app.services.proxy_resolver import LocalCacheOnlyResolver

        assert isinstance(ctx.proxy_resolver, LocalCacheOnlyResolver)


@pytest.mark.asyncio
async def test_catdv_unreachable_at_startup_boots_offline(tmp_path, monkeypatch):
    """No CATDV_OFFLINE flag, but the configured CatDV is unreachable.

    The app must still boot, degrade to offline, and serve /api/health.
    """
    # base URL points at 127.0.0.1:1 → connection refused; force CATDV_OFFLINE=false
    # so the offline mode comes from the unreachable upstream, not the forced flag.
    _env(monkeypatch, tmp_path, CATDV_OFFLINE="false")

    app = _reload_app()
    with TestClient(app) as c:
        ctx = c.app.state.ctx
        assert ctx.catdv is None  # login attempt failed → context degraded
        body = c.get("/api/health").json()
        assert body["mode"] == "offline"

        from backend.app.services.connection_monitor import ConnectionState

        assert ctx.connection_monitor.current_state() == ConnectionState.offline


@pytest.mark.asyncio
async def test_offline_clip_list_serves_empty_when_no_cache(tmp_path, monkeypatch):
    """Forced offline with an empty cache renders without raising."""
    _env(monkeypatch, tmp_path, CATDV_OFFLINE="true")

    app = _reload_app()
    with TestClient(app) as c:
        ctx = c.app.state.ctx
        from backend.app.archive.model import ClipQuery

        page = await ctx.archive.list_clips("881507", ClipQuery(text=None, offset=0, limit=10))
        assert page.total == 0
        assert page.items == ()
