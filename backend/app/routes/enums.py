"""Read-only JSON API for centralised enumerations (frontend consumers)."""

from fastapi import APIRouter, HTTPException, Request

from backend.app.deps import get_core_ctx
from backend.app.services.enum_service import EnumError

router = APIRouter(tags=["enums"])


@router.get("/api/enums/{key}")
async def get_enum(request: Request, key: str) -> list[dict]:
    ctx = get_core_ctx(request)
    try:
        vals = await ctx.enum_service.values(key)
    except EnumError as exc:
        raise HTTPException(404, str(exc)) from exc
    return [
        {"value": v.value, "label": v.label, "enabled": v.enabled, "default": v.is_default}
        for v in vals
    ]
