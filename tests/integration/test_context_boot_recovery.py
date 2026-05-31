"""Boot-time CatDV login can fail in three distinct ways. Only one of
them (bad credentials) should tear down the client; the others
(seat-limit, transport error) must keep the client alive so the
ConnectionMonitor can recover via retry_now() without a process
restart.
"""

import asyncio
from collections.abc import Callable

import pytest

import backend.app.services.gcs as gcs_mod
import backend.app.services.gemini as gemini_mod
from backend.app.context import build_context
from backend.app.services.catdv_client import (
    CatdvAuthError,
    CatdvBusyError,
    CatdvClient,
)
from backend.app.services.connection_monitor import ConnectionState
from backend.app.settings import Settings
from tests.fakes.fake_catdv import running_fake_catdv


class _StubGcs:
    def __init__(self, *args, **kwargs):
        self.bucket_name = "b"
        self._bucket = type("FakeBucket", (), {"exists": staticmethod(lambda: True)})()


class _StubGemini:
    def __init__(self, *args, **kwargs):
        pass


def _set_env(monkeypatch, tmp_path, base_url):
    # conftest.py defaults CATDV_OFFLINE=true for the whole suite; tests
    # that exercise the real boot login path must opt out.
    monkeypatch.delenv("CATDV_OFFLINE", raising=False)
    monkeypatch.setenv("CATDV_BASE_URL", base_url)
    monkeypatch.setenv("CATDV_USERNAME", "klientAI")
    monkeypatch.setenv("CATDV_PASSWORD", "secret")
    monkeypatch.setenv("CATDV_CATALOG_ID", "881507")
    monkeypatch.setenv("GCP_PROJECT_ID", "p")
    monkeypatch.setenv("GCS_BUCKET_NAME", "b")
    monkeypatch.setenv("GCP_LOCATION", "europe-west3")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))


def _patch_first_login(monkeypatch, raise_first: Callable[[], Exception]) -> dict[str, int]:
    """Replace CatdvClient.login so the first call raises `raise_first()`
    and subsequent calls fall through to the real implementation.
    Returns a counter dict so the test can assert how many attempts ran.
    """
    calls = {"n": 0}
    original = CatdvClient.login

    async def patched(self) -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            raise raise_first()
        await original(self)

    monkeypatch.setattr(CatdvClient, "login", patched)
    return calls


@pytest.mark.asyncio
async def test_busy_at_boot_keeps_client_and_recovers(tmp_path, monkeypatch):
    monkeypatch.setattr(gcs_mod, "GcsService", _StubGcs)
    monkeypatch.setattr(gemini_mod, "GeminiService", _StubGemini)
    _patch_first_login(
        monkeypatch,
        lambda: CatdvBusyError("Web Client session limit reached (Maximum:2)"),
    )

    with running_fake_catdv() as (base_url, _fake):
        _set_env(monkeypatch, tmp_path, base_url)
        _, ctx = await build_context(Settings(), init_external=True)
        try:
            # The client must NOT be torn down on a transient seat-busy
            # error; otherwise the monitor has nothing to probe.
            assert ctx.catdv is not None
            assert ctx.connection_monitor is not None
            assert ctx.connection_monitor.current_state() == ConnectionState.offline

            # Manual reconnect should reach the (anonymous) /api/info
            # endpoint on the fake and flip the monitor online.
            new_state = await ctx.connection_monitor.retry_now()
            assert new_state == ConnectionState.online
        finally:
            await ctx.aclose()


@pytest.mark.asyncio
async def test_transport_error_at_boot_keeps_client_and_recovers(tmp_path, monkeypatch):
    monkeypatch.setattr(gcs_mod, "GcsService", _StubGcs)
    monkeypatch.setattr(gemini_mod, "GeminiService", _StubGemini)
    _patch_first_login(
        monkeypatch,
        lambda: RuntimeError("simulated transport failure"),
    )

    with running_fake_catdv() as (base_url, _fake):
        _set_env(monkeypatch, tmp_path, base_url)
        _, ctx = await build_context(Settings(), init_external=True)
        try:
            assert ctx.catdv is not None
            assert ctx.connection_monitor is not None
            assert ctx.connection_monitor.current_state() == ConnectionState.offline

            new_state = await ctx.connection_monitor.retry_now()
            assert new_state == ConnectionState.online
        finally:
            await ctx.aclose()


def _patch_hanging_login(monkeypatch, hang_for: float) -> dict[str, int]:
    """Replace CatdvClient.login so the first call hangs for `hang_for`
    seconds (simulating an unreachable host that silently drops the TCP
    connection), then subsequent calls fall through to the real impl.
    """
    calls = {"n": 0}
    original = CatdvClient.login

    async def patched(self) -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            await asyncio.sleep(hang_for)
            return
        await original(self)

    monkeypatch.setattr(CatdvClient, "login", patched)
    return calls


@pytest.mark.asyncio
async def test_hanging_login_at_boot_is_bounded_and_recovers(tmp_path, monkeypatch):
    """An unreachable CatDV that silently hangs must NOT block startup for
    the full client timeout. The boot login is bounded by
    CATDV_STARTUP_LOGIN_TIMEOUT_S; on timeout we boot offline but keep the
    client so the monitor can recover."""
    monkeypatch.setattr(gcs_mod, "GcsService", _StubGcs)
    monkeypatch.setattr(gemini_mod, "GeminiService", _StubGemini)
    _patch_hanging_login(monkeypatch, hang_for=30.0)
    monkeypatch.setenv("CATDV_STARTUP_LOGIN_TIMEOUT_S", "0.1")

    with running_fake_catdv() as (base_url, _fake):
        _set_env(monkeypatch, tmp_path, base_url)
        # If the boot login were unbounded it would hang ~30s; guard the
        # whole build with a 5s wall-clock budget so the failure mode is a
        # clear timeout rather than a 30s stall.
        _, ctx = await asyncio.wait_for(
            build_context(Settings(), init_external=True), timeout=5.0
        )
        try:
            # Timeout is transport-like: keep the client alive for retry.
            assert ctx.catdv is not None
            assert ctx.connection_monitor is not None
            assert ctx.connection_monitor.current_state() == ConnectionState.offline

            new_state = await ctx.connection_monitor.retry_now()
            assert new_state == ConnectionState.online
        finally:
            await ctx.aclose()


@pytest.mark.asyncio
async def test_auth_error_at_boot_drops_client(tmp_path, monkeypatch):
    """Bad credentials are not transient — keep current behavior of
    tearing down the client. The monitor stays offline; retry_now()
    cannot recover without new credentials and a restart."""
    monkeypatch.setattr(gcs_mod, "GcsService", _StubGcs)
    monkeypatch.setattr(gemini_mod, "GeminiService", _StubGemini)
    _patch_first_login(
        monkeypatch,
        lambda: CatdvAuthError("Invalid user name or password"),
    )

    with running_fake_catdv() as (base_url, _fake):
        _set_env(monkeypatch, tmp_path, base_url)
        _, ctx = await build_context(Settings(), init_external=True)
        try:
            assert ctx.catdv is None, "auth error must tear down the client"
            assert ctx.connection_monitor is not None
            assert ctx.connection_monitor.current_state() == ConnectionState.offline
        finally:
            await ctx.aclose()
