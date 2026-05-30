"""Media routes — HTTP endpoints under /api/media for HTTP Range
streaming of proxy files resolved by ProxyResolver."""

import mimetypes
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse

from backend.app.deps import get_live_ctx

router = APIRouter(prefix="/api/media", tags=["media"])

_DEFAULT_CHUNK = 1 << 16


@router.get("/{clip_id}/thumb")
async def stream_thumbnail(request: Request, clip_id: int):
    ctx = get_live_ctx(request)
    svc = ctx.thumbnail_service
    if svc is None:
        raise HTTPException(404, "thumbnails unavailable")
    path = await svc.get_or_fetch(clip_id)
    if path is None:
        raise HTTPException(404, "no thumbnail")
    return FileResponse(
        path,
        media_type="image/jpeg",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@router.get("/{clip_id}")
async def stream_media(request: Request, clip_id: int):
    ctx = get_live_ctx(request)
    if ctx.proxy_resolver is None:
        raise HTTPException(503, "proxy resolver not initialized")

    try:
        path: Path = await ctx.proxy_resolver.path_for_clip_id(clip_id)
    except Exception as exc:
        raise HTTPException(404, f"proxy unavailable: {exc}") from exc

    mime = mimetypes.guess_type(str(path))[0] or "video/quicktime"
    size = path.stat().st_size
    range_header = request.headers.get("range")

    if range_header and range_header.startswith("bytes="):
        start_s, _, end_s = range_header[6:].partition("-")
        try:
            start = int(start_s)
            end = int(end_s) if end_s else size - 1
        except ValueError:
            raise HTTPException(400, "bad Range header") from None
        if start >= size or end >= size or start > end:
            raise HTTPException(416, "Range not satisfiable")
        length = end - start + 1

        def _stream():
            with open(path, "rb") as f:
                f.seek(start)
                remaining = length
                while remaining > 0:
                    chunk = f.read(min(_DEFAULT_CHUNK, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                    yield chunk

        return StreamingResponse(
            _stream(),
            status_code=206,
            media_type=mime,
            headers={
                "Content-Range": f"bytes {start}-{end}/{size}",
                "Content-Length": str(length),
                "Accept-Ranges": "bytes",
            },
        )

    return FileResponse(path, media_type=mime, headers={"Accept-Ranges": "bytes"})
