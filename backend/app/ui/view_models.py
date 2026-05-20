"""Map CanonicalClip + provider_data into flat dicts for Jinja templates.

Keeps templates logic-free: no `.provider_data["foo"]["bar"]` chains in HTML.
"""

from __future__ import annotations

from typing import Any

from backend.app.archive.model import CanonicalClip, FieldValue, Marker

_YEAR_FIELD = "pragafilm.rok.natočení"
_DECADE_FIELD = "pragafilm.dekáda.natočení"
_PRAGAFILM_PREFIX = "pragafilm."


def _first_value(fv: FieldValue | None) -> str | None:
    if fv is None:
        return None
    v = fv.value
    if isinstance(v, list):
        return str(v[0]) if v else None
    return str(v) if v is not None else None


def clip_summary(clip: CanonicalClip) -> dict[str, Any]:
    """One row in the clips-list table."""
    return {
        "id": int(clip.key[1]),
        "name": clip.name,
        "duration_secs": clip.duration_secs,
        "year": _first_value(clip.fields.get(_YEAR_FIELD)),
        "decade": _first_value(clip.fields.get(_DECADE_FIELD)),
        "marker_count": len(clip.markers),
    }


def _marker_view(m: Marker) -> dict[str, Any]:
    return {
        "name": m.name,
        "in_secs": m.in_.secs,
        "out_secs": m.out.secs if m.out is not None else None,
        "description": m.description,
        "category": m.category,
        "color": m.color,
    }


def _field_view(fv: FieldValue) -> dict[str, Any]:
    value = fv.value
    if isinstance(value, list):
        value_str = ", ".join(str(v) for v in value)
    else:
        value_str = "" if value is None else str(value)
    return {
        "identifier": fv.identifier,
        "name": fv.identifier.split(".")[-1],
        "value": value_str,
    }


def _format_summary(provider_data: dict[str, Any]) -> str:
    media = provider_data.get("media") or {}
    bits: list[str] = []
    fmt = provider_data.get("format")
    if fmt:
        bits.append(str(fmt))
    codec = media.get("codec")
    if codec:
        bits.append(str(codec))
    w, h = media.get("width"), media.get("height")
    if w and h:
        bits.append(f"{w}×{h}")
    return " · ".join(bits)


def clip_detail(clip: CanonicalClip) -> dict[str, Any]:
    """Single-clip page view model."""
    clip_id = int(clip.key[1])
    fields_view = [
        _field_view(fv)
        for ident, fv in clip.fields.items()
        if ident.startswith(_PRAGAFILM_PREFIX)
    ]
    fields_view.sort(key=lambda f: f["identifier"])

    return {
        "clip": {
            "id": clip_id,
            "name": clip.name,
            "duration_secs": clip.duration_secs,
            "fps": clip.fps or 25.0,
            "format": _format_summary(clip.provider_data),
            "media_url": f"/api/media/{clip_id}",
            "markers": [_marker_view(m) for m in clip.markers],
            "fields": fields_view,
            "notes": clip.provider_data.get("notes") or None,
            "big_notes": clip.provider_data.get("bigNotes") or None,
        },
    }
