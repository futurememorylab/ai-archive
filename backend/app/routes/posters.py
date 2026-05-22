import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

router = APIRouter(prefix="/api/poster", tags=["posters"])

_IMMUTABLE = "public, max-age=31536000, immutable"


@router.get("/{clip_id}")
async def get_poster(request: Request, clip_id: int):
    ctx = request.app.state.ctx
    if ctx.catdv is None or ctx.poster_cache is None:
        raise HTTPException(503, "poster service not initialized")

    try:
        path = await ctx.poster_cache.get_or_fetch(
            clip_id, ctx.catdv.download_poster
        )
    except httpx.HTTPStatusError as exc:
        if exc.response is not None and exc.response.status_code == 404:
            raise HTTPException(404, "poster not available") from exc
        raise HTTPException(502, f"upstream poster fetch failed: {exc}") from exc
    except Exception as exc:
        raise HTTPException(502, f"poster fetch failed: {exc}") from exc

    return FileResponse(
        path,
        media_type="image/jpeg",
        headers={"Cache-Control": _IMMUTABLE},
    )
