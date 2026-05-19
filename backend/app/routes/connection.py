"""Connection-state HTTP surface.

`GET /api/connection/state` returns the current `ConnectionState` value.
`GET /api/connection/events` streams state changes via SSE; the
ConnectionMonitor publishes them onto `EventBus` topic `"connection"`.

The (PR 5) UI connection pill subscribes to the SSE stream. For PR 4 we
ship the endpoint without a template so the wire shape is locked.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

router = APIRouter(prefix="/api/connection", tags=["connection"])


@router.get("/state")
async def get_state(request: Request) -> dict:
    ctx = request.app.state.ctx
    if getattr(ctx, "connection_monitor", None) is None:
        return {"state": "online"}
    return {"state": str(ctx.connection_monitor.current_state().value)}


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
