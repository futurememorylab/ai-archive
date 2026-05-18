from fastapi import APIRouter, HTTPException, Request

router = APIRouter(prefix="/api/catdv", tags=["catdv"])


@router.get("/clips")
async def list_clips(request: Request, q: str | None = None,
                      offset: int = 0, limit: int = 50):
    ctx = request.app.state.ctx
    if ctx.catdv is None:
        raise HTTPException(503, "CatDV client not initialized")
    return await ctx.catdv.list_clips(
        catalog_id=ctx.settings.catdv_catalog_id, offset=offset, limit=limit, q=q,
    )


@router.get("/clips/{clip_id}")
async def get_clip(request: Request, clip_id: int):
    ctx = request.app.state.ctx
    if ctx.catdv is None:
        raise HTTPException(503, "CatDV client not initialized")
    try:
        return await ctx.catdv.get_clip(clip_id)
    except Exception as exc:
        raise HTTPException(502, f"upstream CatDV error: {exc}")
