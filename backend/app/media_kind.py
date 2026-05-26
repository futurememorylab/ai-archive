"""Classify a media file as a still image vs. time-based media, by file
extension. The one source of truth shared by the proxy resolver, the
thumbnail service, and the clips view model.

Extension is authoritative here: CatDV reports stills with
``format = "Unknown"`` and ``duration = 0``, and the ``media.still`` flag
was observed ``false`` even on a real JPEG — so neither is reliable.
"""

from __future__ import annotations

from pathlib import PurePosixPath

IMAGE_EXTS: frozenset[str] = frozenset(
    {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".gif", ".bmp", ".webp", ".heic"}
)


def is_image_path(path: str | None) -> bool:
    """True if `path`'s extension names a still-image format."""
    if not path:
        return False
    return PurePosixPath(path).suffix.lower() in IMAGE_EXTS
