"""CatDV routes — HTTP endpoints under /api/catdv for listing and getting
clips. Thin wrapper around the active ArchiveProvider."""

from fastapi import APIRouter, HTTPException, Request

from backend.app.archive.errors import ProviderError
from backend.app.archive.model import ClipQuery
from backend.app.deps import get_live_ctx

router = APIRouter(prefix="/api/catdv", tags=["catdv"])


@router.get("/clips")
async def list_clips(request: Request, q: str | None = None, offset: int = 0, limit: int = 50):
    ctx = get_live_ctx(request)
    try:
        page = await ctx.archive.list_clips(
            str(ctx.settings.catdv_catalog_id),
            ClipQuery(text=q, offset=offset, limit=limit),
        )
    except ProviderError as exc:
        raise HTTPException(502, f"archive error: {exc}") from exc
    return {
        "total": page.total,
        "clips": [c.provider_data for c in page.items],
    }


@router.get("/clips/{clip_id}")
async def get_clip(request: Request, clip_id: int):
    ctx = get_live_ctx(request)
    try:
        clip = await ctx.archive.get_clip(str(clip_id))
    except ProviderError as exc:
        raise HTTPException(502, f"archive error: {exc}") from exc
    return clip.provider_data
