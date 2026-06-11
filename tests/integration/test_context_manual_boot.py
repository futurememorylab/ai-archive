# tests/integration/test_context_manual_boot.py
"""Manual mode builds the CatdvClient but must NOT log in at boot; auto mode
preserves the legacy startup login. We assert on login attempts via a stub."""

import importlib

import pytest


def _setenv(monkeypatch, tmp_path, connect_mode):
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("CATDV_BASE_URL", "http://localhost:0")
    monkeypatch.setenv("CATDV_USERNAME", "u")
    monkeypatch.setenv("CATDV_PASSWORD", "p")
    monkeypatch.setenv("CATDV_CATALOG_ID", "881507")
    monkeypatch.setenv("GCP_PROJECT_ID", "p")
    monkeypatch.setenv("GCS_BUCKET_NAME", "b")
    monkeypatch.setenv("PROXY_SOURCE", "rest")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CATDV_CONNECT_MODE", connect_mode)
    # The session conftest defaults CATDV_OFFLINE=true; override it so the
    # CatdvClient is actually built (manual mode still defers the login).
    monkeypatch.setenv("CATDV_OFFLINE", "false")


@pytest.mark.asyncio
async def test_manual_mode_does_not_login_at_boot(monkeypatch, tmp_path):
    _setenv(monkeypatch, tmp_path, "manual")
    login_calls = {"n": 0}

    from backend.app.services import catdv_client as cc

    async def fake_login(self):
        login_calls["n"] += 1
        self._logged_in = True

    monkeypatch.setattr(cc.CatdvClient, "login", fake_login)

    from backend.app import context as ctx_mod

    importlib.reload(ctx_mod)
    from backend.app.settings import Settings

    core, live = await ctx_mod.build_context(Settings(), init_external=True)
    try:
        assert live is not None
        assert live.catdv is not None       # client built
        assert live.catdv.logged_in is False
        assert login_calls["n"] == 0        # but NOT logged in
        from backend.app.services.connection_monitor import ConnectionState

        assert live.connection_monitor.current_state() == ConnectionState.disconnected
        assert live.idle_disconnector is not None
    finally:
        await (live or core).aclose()
