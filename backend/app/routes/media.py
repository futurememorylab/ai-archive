"""Media routes — HTTP endpoints under /api/media for HTTP Range
streaming of proxy files resolved by ProxyResolver."""

import mimetypes
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse, Response, StreamingResponse

from backend.app.deps import get_core_ctx, get_live_ctx
from backend.app.services.media_locator import LocalFile, MediaNotAvailable, RemoteUrl

from backend.app.uploaded_ids import is_uploaded

router = APIRouter(prefix="/api/media", tags=["media"])

_DEFAULT_CHUNK = 1 << 16

# Short-lived 404 cache for the thumb endpoint. Browsers don't cache 404s
# reliably without an explicit Cache-Control, so list pages re-fire the
# same misses on every visit. A small TTL collapses the storm; it must
# stay short so a freshly-prefetched file or CatDV reconnect becomes
# visible without a forced reload.
_THUMB_MISS_CACHE = "public, max-age=300"


def _thumb_404(detail: str) -> Response:
    return Response(
        status_code=404,
        content=detail.encode(),
        media_type="text/plain",
        headers={"Cache-Control": _THUMB_MISS_CACHE},
    )


@router.get("/{clip_id}/thumb")
async def stream_thumbnail(request: Request, clip_id: int):
    if is_uploaded(clip_id):
        # Uploaded posters are pre-stored at ingest in the DB-first thumb
        # cache; serve them via the core ctx so uploads thumbnail fully
        # offline (no live CatDV/Gemini wiring required).
        core = get_core_ctx(request)
        path = core.settings.data_dir / "cache" / "thumbs" / f"{clip_id}.jpg"
        if not path.exists() or path.stat().st_size == 0:  # sync-io-ok: uploaded poster lookup, tracked for the tier-4 async-io pass
            return _thumb_404("no thumbnail")
        return FileResponse(
            path,
            media_type="image/jpeg",
            headers={"Cache-Control": "public, max-age=86400"},
        )
    ctx = get_live_ctx(request)
    svc = ctx.thumbnail_service
    if svc is None:
        return _thumb_404("thumbnails unavailable")
    path = await svc.get_or_fetch(clip_id)
    if path is None:
        return _thumb_404("no thumbnail")
    return FileResponse(
        path,
        media_type="image/jpeg",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@router.get("/{clip_id}")
async def stream_media(request: Request, clip_id: int):
    if is_uploaded(clip_id):
        # Uploaded clips are pre-seeded into the proxy cache at ingest and
        # served DB-first via the core ctx — playable fully offline.
        core = get_core_ctx(request)
        row = await core.proxy_cache_repo.get(core.db, clip_id)
        if row is None:
            raise HTTPException(404, f"uploaded clip {clip_id} not in local cache")
        path = Path(row["file_path"])
        if not path.exists() or path.stat().st_size == 0:  # sync-io-ok: uploaded proxy lookup, tracked for the tier-4 async-io pass
            raise HTTPException(404, f"uploaded clip {clip_id} file missing: {path}")
    else:
        ctx = get_live_ctx(request)
        try:
            located = await ctx.media_locator.locate(clip_id)
        except MediaNotAvailable as exc:
            raise HTTPException(404, str(exc)) from exc
        if isinstance(located, RemoteUrl):
            # Browser follows to GCS; range requests for seeking go
            # straight to the signed URL, bytes never transit this app.
            return RedirectResponse(located.url, status_code=307)
        path = located.path

    mime = mimetypes.guess_type(str(path))[0] or "video/quicktime"
    size = path.stat().st_size  # sync-io-ok: pre-existing, single metadata call on the stream-response path
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
            with open(path, "rb") as f:  # sync-io-ok: pre-existing, runs inside a StreamingResponse body generator
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
