"""FastAPI dependencies — typed accessors for shared app state.

Two accessors mirror the ``CoreCtx`` / ``LiveCtx`` split:

* ``get_core_ctx`` — always available; for handlers that touch only
  always-present state (DB, repos, write queue, cache services).
* ``get_live_ctx`` — raises a typed 503 when CatDV/Gemini wiring is
  offline (``live_ctx is None``); for handlers that touch any genuinely
  external service. This is the offline/online contract surfacing at the
  edge, replacing the scattered ``assert ctx.foo is not None``.

Both are plain functions: handlers take ``request: Request`` and call
``ctx = get_live_ctx(request)`` (or ``get_core_ctx``) inline.
"""

from __future__ import annotations

from fastapi import HTTPException, Request

from backend.app.context import CoreCtx, LiveCtx


def get_core_ctx(request: Request) -> CoreCtx:
    """Return the always-present CoreCtx built by the lifespan."""
    return request.app.state.core_ctx  # type: ignore[no-any-return]


def get_live_ctx(request: Request) -> LiveCtx:
    """Return the LiveCtx, or raise 503 when running offline.

    ``app.state.live_ctx`` is None whenever the app booted without
    external wiring (``init_external=False``). Handlers that need any
    live service (archive, catdv, gemini, ai_store, proxy_resolver,
    thumbnail_service, sync_engine, connection_monitor, workspace_manager,
    lru_eviction, media_prefetcher) depend on this and get a clear 503.
    """
    live = request.app.state.live_ctx
    if live is None:
        raise HTTPException(503, "CatDV/Gemini offline — live services unavailable")
    return live  # type: ignore[no-any-return]
