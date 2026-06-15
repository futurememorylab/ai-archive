"""VPN (onetun tunnel) control surface. Cloud-only: every endpoint returns
409 when the deployment is not VPN-managed (no WireGuard configured).

`disable` is a master switch: release the CatDV seat over the still-live
tunnel and pin the monitor offline BEFORE the supervisor drops the tunnel,
so logout traverses a working connection."""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Request

from backend.app.routes.pages.templates import templates as _templates

router = APIRouter(prefix="/api/vpn", tags=["vpn"])


def _supervisor(request: Request):
    live = request.app.state.live_ctx
    return live.vpn_supervisor if live is not None else None


def _status_dict(sup) -> dict:
    if sup is None:
        return {"managed": False, "desired": "off",
                "process_running": False, "healthy": False, "connecting": False}
    s = sup.status()
    return {"managed": s.managed, "desired": s.desired,
            "process_running": s.process_running, "healthy": s.healthy,
            "connecting": s.connecting}


def _toast(message: str, level: str = "success") -> dict[str, str]:
    return {"HX-Trigger": json.dumps({"toast": {"message": message, "level": level}})}


async def _reply(request: Request, sup, *, headers: dict | None = None):
    if request.headers.get("HX-Request") == "true":
        return _templates.TemplateResponse(
            request, "_connection_chip_inner.html", {}, headers=headers,
        )
    from fastapi.responses import JSONResponse
    return JSONResponse(_status_dict(sup), headers=headers)


@router.get("/status")
async def status(request: Request):
    return _status_dict(_supervisor(request))


@router.post("/enable")
async def enable(request: Request):
    sup = _supervisor(request)
    if sup is None:
        raise HTTPException(409, "VPN not managed on this deployment")
    live = request.app.state.live_ctx
    live.connection_monitor.set_manual_offline(False)
    await sup.enable()
    return await _reply(request, sup, headers=_toast("VPN tunnel enabled."))


@router.post("/disable")
async def disable(request: Request):
    sup = _supervisor(request)
    if sup is None:
        raise HTTPException(409, "VPN not managed on this deployment")
    live = request.app.state.live_ctx
    if live.catdv is not None:
        try:
            await live.catdv.logout()
        except Exception:  # noqa: BLE001 — seat will time out server-side
            pass
    live.connection_monitor.set_manual_offline(True)
    await sup.disable()
    return await _reply(request, sup, headers=_toast("VPN tunnel disabled."))


@router.post("/retry")
async def retry(request: Request):
    sup = _supervisor(request)
    if sup is None:
        raise HTTPException(409, "VPN not managed on this deployment")
    st = await sup.probe_now()
    msg = "VPN reachable." if st.healthy else "VPN still unreachable."
    level = "success" if st.healthy else "error"
    return await _reply(request, sup, headers=_toast(msg, level))
