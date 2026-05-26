"""Streaming MP4 upload helper for Studio testbench items."""
from pathlib import Path
from uuid import uuid4


class UploadError(ValueError):
    pass


_CHUNK = 1024 * 1024  # 1 MiB


async def save_upload(upload, *, uploads_dir: Path, max_mb: int) -> str:
    """Stream-write `upload` to `uploads_dir/<uuid>.<ext>`. Returns the
    relative filename (caller stores it in `testbench_items.upload_path`).
    Raises UploadError on MIME / size violations."""
    content_type = (upload.content_type or "").lower()
    if not content_type.startswith("video/"):
        raise UploadError(f"unsupported content type {content_type}; expected video/*")
    suffix = Path(upload.filename or "").suffix.lower().lstrip(".") or "mp4"
    rel = f"{uuid4().hex}.{suffix}"
    uploads_dir.mkdir(parents=True, exist_ok=True)
    dest = uploads_dir / rel
    limit = max_mb * 1024 * 1024
    written = 0
    try:
        with dest.open("wb") as fh:
            while True:
                chunk = await upload.read(_CHUNK)
                if not chunk:
                    break
                written += len(chunk)
                if written > limit:
                    raise UploadError(f"upload exceeds {max_mb} MB limit")
                fh.write(chunk)
    except Exception:
        dest.unlink(missing_ok=True)
        raise
    return rel
