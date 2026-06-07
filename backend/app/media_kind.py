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

AUDIO_EXTS: frozenset[str] = frozenset(
    {".wav", ".mp3", ".aac", ".m4a", ".flac", ".aiff", ".aif", ".ogg"}
)


def is_image_path(path: str | None) -> bool:
    """True if `path`'s extension names a still-image format."""
    if not path:
        return False
    return PurePosixPath(path).suffix.lower() in IMAGE_EXTS


def classify_media_kind(path: str | None, *, has_audio: bool | None = None) -> str:
    """Classify media as ``image | audio | video | video+audio``.

    Extension-first, like ``is_image_path`` (CatDV's own flags are
    unreliable — see module docstring). ``has_audio`` refines the
    video case when the caller knows it; when unknown we default to
    ``video+audio`` — the conservative choice for token estimation
    (overestimates by the audio track, never underestimates).
    """
    if is_image_path(path):
        return "image"
    if path and PurePosixPath(path).suffix.lower() in AUDIO_EXTS:
        return "audio"
    if has_audio is False:
        return "video"
    return "video+audio"
