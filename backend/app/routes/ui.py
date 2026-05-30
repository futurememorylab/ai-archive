"""Server-rendered HTMX partials for the four offline-cycle UI surfaces.

No client-side state, no JS framework. Each partial renders into a stable
id on the page and HTMX swaps it via outerHTML.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from backend.app.deps import get_core_ctx

TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

router = APIRouter(prefix="/ui", tags=["ui"])


@router.get("/connection-pill", response_class=HTMLResponse)
async def connection_pill(request: Request):
    ctx = get_core_ctx(request)
    live = request.app.state.live_ctx
    state = "online"
    if live is not None:
        state = str(live.connection_monitor.current_state().value)
    rows = await ctx.pending_ops_repo.list_pending(ctx.db)
    pending_count = len(rows)
    return templates.TemplateResponse(
        request,
        "connection_pill.html",
        {"state": state, "pending_count": pending_count},
    )


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
        {"rows": rows},
    )


@router.get("/clip-badge/{provider_id}/{provider_clip_id}", response_class=HTMLResponse)
async def clip_badge(request: Request, provider_id: str, provider_clip_id: str):
    ctx = get_core_ctx(request)
    counts = await ctx.pending_ops_repo.count_pending_by_clip(ctx.db, provider_id=provider_id)
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
