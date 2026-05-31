"""Test helper for the CoreCtx / LiveCtx split (T3-A1).

Integration tests boot the app offline (``init_external=False`` because
``CATDV_USERNAME=""``), so ``app.state.live_ctx`` is None and live-only
routes return 503. Tests that want to exercise a live route with fakes
call :func:`install_live_ctx` to wrap the already-built CoreCtx in a
LiveCtx, supplying just the live services they care about; the rest
default to ``MagicMock`` so the dataclass is fully constructed.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from backend.app.context import LiveCtx
from backend.app.services.connection_monitor import ConnectionState


class _OnlineMonitor:
    """Default connection monitor for injected LiveCtxs: reports online and
    not forced, so the topbar/layout renders ``mode == "online"`` (matching
    the pre-split offline-boot default where the monitor was absent)."""

    is_forced = False
    _forced_offline = False

    def current_state(self) -> ConnectionState:
        return ConnectionState.online


def install_live_ctx(app, **overrides):
    """Wrap ``app.state.core_ctx`` in a LiveCtx and stash it on the app.

    Any live service not given in ``overrides`` defaults to a MagicMock
    (or, for the connection monitor, an online stub). Returns the LiveCtx
    so callers can set attributes after the fact (mirrors the old
    ``ctx.archive = ...`` injection style).
    """
    core = app.state.core_ctx
    defaults: dict = {
        "archive": MagicMock(),
        "ai_store": MagicMock(),
        "gemini": MagicMock(),
        "sync_engine": MagicMock(),
        "connection_monitor": _OnlineMonitor(),
        "workspace_manager": MagicMock(),
        "lru_eviction": MagicMock(),
        "_gcs_service": MagicMock(),
        "catdv": None,
        "proxy_resolver": None,
        "thumbnail_service": None,
        "media_prefetcher": None,
    }
    defaults.update(overrides)
    live = LiveCtx(core=core, **defaults)
    app.state.live_ctx = live
    return live
