"""Effective media-resolution resolution: per-prompt override beats the model
default, which beats the global 'medium' fallback. Out-of-set values (stale DB
rows, direct edits) are ignored so they can never reach the Gemini SDK map and
KeyError at run time. See cost-prediction spec §2."""

from typing import get_args

from backend.app.models.media import DEFAULT_MEDIA_RESOLUTION, MediaResolution

_VALID = frozenset(get_args(MediaResolution))


def resolve_media_resolution(
    version_override: str | None, model_default: str | None
) -> str:
    for candidate in (version_override, model_default):
        if candidate in _VALID:
            return candidate
    return DEFAULT_MEDIA_RESOLUTION


def resolution_valid_for_kind(resolution: str, media_kind: str) -> bool:
    """Gemini accepts HIGH media resolution only for single still images;
    LOW/MEDIUM are valid for every media kind. (See the 400 INVALID_ARGUMENT
    'HIGH media resolution only for single images' from the Vertex API.)"""
    if resolution == "high":
        return media_kind == "image"
    return True
