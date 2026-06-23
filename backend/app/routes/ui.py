"""Server-rendered HTMX partials for the four offline-cycle UI surfaces.

No client-side state, no JS framework. Each partial renders into a stable
id on the page and HTMX swaps it via outerHTML.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from backend.app.deps import get_core_ctx
from backend.app.routes.pages.templates import templates

router = APIRouter(prefix="/ui", tags=["ui"])


def _pill_context(request: Request) -> dict:
    live = request.app.state.live_ctx
    settings = request.app.state.core_ctx.settings
    state = "online"
    if live is not None:
        state = str(live.connection_monitor.current_state().value)
    return {
        "state": state,
        "connect_mode": getattr(settings, "catdv_connect_mode", "manual"),
    }


@router.get("/connection-pill", response_class=HTMLResponse)
async def connection_pill(request: Request):
    ctx = get_core_ctx(request)
    rows = await ctx.pending_ops_repo.list_pending(ctx.db)
    context = _pill_context(request)
    context["pending_count"] = len(rows)
    return templates.TemplateResponse(request, "connection_pill.html", context)


@router.get("/connection-chip", response_class=HTMLResponse)
async def connection_chip(request: Request):
    # The stable #connection-chip container polls this every 5s and swaps the
    # result into its innerHTML, so we return the INNER partial (label +
    # action), which computes its own mode/connect_mode from the request.
    # no-store: a status partial must never be served stale from cache.
    resp = templates.TemplateResponse(request, "_connection_chip_inner.html", {})
    resp.headers["Cache-Control"] = "no-store"
    return resp


@router.get("/workspace-switcher", response_class=HTMLResponse)
async def workspace_switcher(request: Request, ws_id: int | None = None):
    ctx = get_core_ctx(request)
    live = request.app.state.live_ctx
    if live is None:
        return templates.TemplateResponse(
            request,
            "workspace_switcher.html",
            {
                "workspaces": [],
                "active_ws_id": None,
                "active_ws": None,
                "catalog_id": str(ctx.settings.catdv_catalog_id),
            },
        )
    workspaces = await live.workspace_manager.list_workspaces()
    active = await live.workspace_manager.get(ws_id) if ws_id is not None else None
    return templates.TemplateResponse(
        request,
        "workspace_switcher.html",
        {
            "workspaces": workspaces,
            "active_ws_id": ws_id,
            "active_ws": active,
            "catalog_id": str(ctx.settings.catdv_catalog_id),
        },
    )


@router.get("/sync-drawer", response_class=HTMLResponse)
async def sync_drawer(request: Request):
    ctx = get_core_ctx(request)
    rows = await ctx.pending_ops_repo.list_with_clip_names(ctx.db)
    return templates.TemplateResponse(
        request,
        "sync_drawer.html",
        {"sync_rows": rows},
    )


@router.get("/review-pill", response_class=HTMLResponse)
async def review_pill(request: Request):
    """Topbar "N to review" pill inner partial. Polled (load + every 15s) and
    refreshed after draft-changing actions by #review-pill, so the count never
    goes stale without a full reload. DB-only; mirrors the sync-chip pattern."""
    ctx = get_core_ctx(request)
    review_count = await ctx.review_items_repo.count_clips_for_review(ctx.db)
    resp = templates.TemplateResponse(
        request, "pages/_review_pill_inner.html", {"review_count": review_count}
    )
    resp.headers["Cache-Control"] = "no-store"
    return resp


@router.get("/sync-chip", response_class=HTMLResponse)
async def sync_chip(request: Request):
    """Topbar sync indicator inner partial: queued / problem counts + the
    pending-writes drawer. Polled every 10s (and on load) by #sync-chip, and
    returned by retry/discard so the panel refreshes in place. DB-only."""
    ctx = get_core_ctx(request)
    counts = await ctx.pending_ops_repo.count_actionable(ctx.db)
    rows = await ctx.pending_ops_repo.list_with_clip_names(ctx.db)
    # Offline → the SyncEngine can't drain the queue (sync_engine._tick returns
    # early when not online), so the drawer explains the wait instead of showing
    # a bare, action-less "Queued". Cached monitor state — no CatDV round-trip.
    live = getattr(request.app.state, "live_ctx", None)
    monitor = getattr(live, "connection_monitor", None)
    offline = monitor is not None and monitor.current_state().value != "online"
    resp = templates.TemplateResponse(
        request,
        "_sync_chip_inner.html",
        {"sync_counts": counts, "sync_rows": rows, "offline": offline},
    )
    resp.headers["Cache-Control"] = "no-store"
    return resp


@router.get("/clip-badge/{provider_id}/{provider_clip_id}", response_class=HTMLResponse)
async def clip_badge(request: Request, provider_id: str, provider_clip_id: str):
    ctx = get_core_ctx(request)
    counts = await ctx.pending_ops_repo.count_pending_by_clip(
        ctx.db, provider_id=provider_id, clip_ids=[provider_clip_id]
    )
    bucket = counts.get(provider_clip_id, {"pending": 0, "conflict": 0})
    return templates.TemplateResponse(
        request,
        "clip_badge.html",
        {
            "provider_id": provider_id,
            "provider_clip_id": provider_clip_id,
            "pending": bucket["pending"],
            "conflict": bucket["conflict"],
        },
    )
