"""Connection-state HTTP surface.

`GET /api/connection/state` returns the current `ConnectionState` value.
`GET /api/connection/events` streams state changes via SSE; the
ConnectionMonitor publishes them onto `EventBus` topic `"connection"`.

The (PR 5) UI connection pill subscribes to the SSE stream. For PR 4 we
ship the endpoint without a template so the wire shape is locked.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from backend.app.deps import get_core_ctx, get_live_ctx
from backend.app.routes.pages.templates import templates as _templates
from backend.app.services.catdv_client import CatdvAuthError, CatdvBusyError
from backend.app.services.errors import humanise
from backend.app.shutdown import schedule_graceful_shutdown

router = APIRouter(prefix="/api/connection", tags=["connection"])


def _mode(monitor) -> str:
    if monitor is None:
        return "online"
    if getattr(monitor, "is_forced", False) or getattr(monitor, "_forced_offline", False):
        return "forced_offline"
    from backend.app.services.connection_monitor import ConnectionState

    state = monitor.current_state()
    if state == ConnectionState.online:
        return "online"
    if state == ConnectionState.disconnected:
        return "disconnected"
    return "offline"


def _monitor(request: Request):
    """The connection monitor lives on the LiveCtx; None when offline."""
    live = request.app.state.live_ctx
    return live.connection_monitor if live is not None else None


async def _pill_or_json(request: Request, monitor, *, status_code: int = 200,
                        headers: dict[str, str] | None = None):
    if request.headers.get("HX-Request") == "true":
        # The topbar chip is the live control surface; return it when the
        # chip targets us. (The connection pill is an alternate surface and
        # is returned otherwise.) The chip computes its own mode from the
        # request, so no context is needed.
        if request.headers.get("HX-Target") == "connection-chip":
            # Return the inner partial — it swaps into the stable
            # #connection-chip container's innerHTML.
            return _templates.TemplateResponse(
                request, "_connection_chip_inner.html", {},
                status_code=status_code, headers=headers,
            )
        from backend.app.routes.ui import _pill_context

        # Include pending_count so the swapped-in pill's "Sync now (N)"
        # button renders correctly instead of flashing "Sync now ()" until
        # the next /ui/connection-pill poll (mirrors that handler).
        ctx = get_core_ctx(request)
        context = _pill_context(request)
        rows = await ctx.pending_ops_repo.list_pending(ctx.db)
        context["pending_count"] = len(rows)
        return _templates.TemplateResponse(
            request, "connection_pill.html", context,
            status_code=status_code, headers=headers,
        )
    body = {"state": str(monitor.current_state().value) if monitor else "online",
            "mode": _mode(monitor)}
    from fastapi.responses import JSONResponse

    return JSONResponse(body, status_code=status_code, headers=headers)


@router.get("/state")
async def get_state(request: Request) -> dict:
    monitor = _monitor(request)
    if monitor is None:
        return {"state": "online", "mode": "online"}
    return {
        "state": str(monitor.current_state().value),
        "mode": _mode(monitor),
    }


@router.post("/retry")
async def retry_now(request: Request):
    monitor = _monitor(request)
    is_htmx = request.headers.get("HX-Request") == "true"
    # The pill (manual mode) and the chip (auto mode) both re-probe via this
    # endpoint; each wants its own partial back. Route by the HTMX target.
    wants_pill = request.headers.get("HX-Target") == "connection-pill"

    if monitor is None:
        body = {"state": "online", "mode": "online"}
    elif getattr(monitor, "is_forced", False) or getattr(monitor, "_forced_offline", False):
        if wants_pill:
            return await _pill_or_json(request, monitor, status_code=409)
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

    if wants_pill:
        return await _pill_or_json(request, monitor)
    if is_htmx:
        # Inner partial → swaps into the stable #connection-chip container.
        return _templates.TemplateResponse(
            request,
            "_connection_chip_inner.html",
            {"mode": body["mode"]},
        )
    return body


@router.post("/shutdown")
async def shutdown(request: Request):
    """Release the CatDV seat and stop the server.

    Schedules a self-SIGTERM a beat after the response flushes; uvicorn's
    graceful shutdown then runs the lifespan teardown (LiveCtx.aclose),
    which stops the connection monitor before logging out so the seat can't
    be re-grabbed. Refused under --reload (the reloader may respawn us).
    """
    ctx = get_core_ctx(request)
    if getattr(ctx.settings, "app_env", "dev") == "prod":
        raise HTTPException(
            status_code=403,
            detail="shutdown is managed by Cloud Run; the instance scales to zero on idle",
        )
    if getattr(ctx.settings, "dev_reload", False):
        raise HTTPException(
            status_code=409,
            detail="shutdown disabled in reload mode; stop with Ctrl-C",
        )
    schedule_graceful_shutdown()
    return _templates.TemplateResponse(request, "_shutdown_screen.html", {})


@router.post("/offline")
async def set_offline(request: Request) -> dict:
    """Manual override: pin state to offline until cleared."""
    monitor = _monitor(request)
    if monitor is None:
        return {"state": "offline"}
    monitor.set_manual_offline(True)
    return {"state": str(monitor.current_state().value)}


@router.post("/online")
async def set_online(request: Request) -> dict:
    """Clear the manual-offline override; state reverts to last probe."""
    monitor = _monitor(request)
    if monitor is None:
        return {"state": "online"}
    monitor.set_manual_offline(False)
    return {"state": str(monitor.current_state().value)}


def _toast_header(message: str, level: str = "error") -> dict[str, str]:
    return {"HX-Trigger": json.dumps({"toast": {"message": message, "level": level}})}


@router.post("/connect")
async def connect(request: Request):
    live = get_live_ctx(request)  # 503 if fully offline
    monitor = _monitor(request)
    if live.catdv is None:
        raise HTTPException(status_code=409, detail="CatDV not configured")
    try:
        await live.catdv.login()
    except CatdvBusyError as exc:
        return await _pill_or_json(request, monitor, status_code=409,
                                   headers=_toast_header(f"CatDV seat busy: {humanise(exc)}"))
    except CatdvAuthError as exc:
        return await _pill_or_json(request, monitor, status_code=401,
                                   headers=_toast_header(f"CatDV login rejected: {humanise(exc)}"))
    except Exception as exc:  # noqa: BLE001 — transport / unreachable
        return await _pill_or_json(request, monitor, status_code=502,
                                   headers=_toast_header(f"CatDV unreachable: {humanise(exc)}"))
    if monitor is not None:
        await monitor.probe_once()
    return await _pill_or_json(request, monitor)


@router.post("/disconnect")
async def disconnect(request: Request):
    live = get_live_ctx(request)
    monitor = _monitor(request)
    if live.catdv is not None:
        await live.catdv.logout()
    if monitor is not None:
        await monitor.probe_once()
    return await _pill_or_json(request, monitor)


@router.get("/events")
async def stream_events(request: Request) -> StreamingResponse:
    ctx = get_core_ctx(request)
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
