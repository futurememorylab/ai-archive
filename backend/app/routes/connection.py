"""Connection-state HTTP surface.

`GET /api/connection/state` returns the current `ConnectionState` value.
`GET /api/connection/events` streams state changes via SSE; the
ConnectionMonitor publishes them onto `EventBus` topic `"connection"`.

The (PR 5) UI connection pill subscribes to the SSE stream. For PR 4 we
ship the endpoint without a template so the wire shape is locked.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from fastapi.templating import Jinja2Templates

router = APIRouter(prefix="/api/connection", tags=["connection"])

_TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "templates"
_templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


def _mode(monitor) -> str:
    if monitor is None:
        return "online"
    if getattr(monitor, "is_forced", False) or getattr(monitor, "_forced_offline", False):
        return "forced_offline"
    from backend.app.services.connection_monitor import ConnectionState

    return "online" if monitor.current_state() == ConnectionState.online else "offline"


@router.get("/state")
async def get_state(request: Request) -> dict:
    ctx = request.app.state.ctx
    monitor = getattr(ctx, "connection_monitor", None)
    if monitor is None:
        return {"state": "online", "mode": "online"}
    return {
        "state": str(monitor.current_state().value),
        "mode": _mode(monitor),
    }


@router.post("/retry")
async def retry_now(request: Request):
    ctx = request.app.state.ctx
    monitor = getattr(ctx, "connection_monitor", None)
    is_htmx = request.headers.get("HX-Request") == "true"

    if monitor is None:
        body = {"state": "online", "mode": "online"}
    elif getattr(monitor, "is_forced", False) or getattr(monitor, "_forced_offline", False):
        if is_htmx:
            return _templates.TemplateResponse(
                request,
                "_connection_chip.html",
                {"mode": "forced_offline"},
                status_code=409,
            )
        raise HTTPException(status_code=409, detail="forced offline (CATDV_OFFLINE=true)")
    else:
        state = await monitor.retry_now()
        body = {"state": str(state.value), "mode": _mode(monitor)}

    if is_htmx:
        return _templates.TemplateResponse(
            request,
            "_connection_chip.html",
            {"mode": body["mode"]},
        )
    return body


@router.post("/offline")
async def set_offline(request: Request) -> dict:
    """Manual override: pin state to offline until cleared."""
    ctx = request.app.state.ctx
    if getattr(ctx, "connection_monitor", None) is None:
        return {"state": "offline"}
    ctx.connection_monitor.set_manual_offline(True)
    return {"state": str(ctx.connection_monitor.current_state().value)}


@router.post("/online")
async def set_online(request: Request) -> dict:
    """Clear the manual-offline override; state reverts to last probe."""
    ctx = request.app.state.ctx
    if getattr(ctx, "connection_monitor", None) is None:
        return {"state": "online"}
    ctx.connection_monitor.set_manual_offline(False)
    return {"state": str(ctx.connection_monitor.current_state().value)}


@router.get("/events")
async def stream_events(request: Request) -> StreamingResponse:
    ctx = request.app.state.ctx
    bus = ctx.event_bus
    queue = bus.subscribe("connection")

    async def gen():
        try:
            while True:
                if await request.is_disconnected():
                    return
                payload = await queue.get()
                yield f"data: {json.dumps(payload)}\n\n"
        finally:
            bus.unsubscribe("connection", queue)

    return StreamingResponse(gen(), media_type="text/event-stream")
