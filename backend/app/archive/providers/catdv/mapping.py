from __future__ import annotations

from datetime import datetime
from typing import Any

from backend.app.archive.model import (
    CanonicalClip,
    FieldValue,
    Marker,
    MediaRef,
    Timecode,
)
from backend.app.timecode import secs_to_smpte

DEFAULT_FPS = 25.0


def from_catdv_clip(raw: dict[str, Any], *, fetched_at: datetime) -> CanonicalClip:
    clip_id = str(raw["ID"])
    fps = float(raw.get("fps") or DEFAULT_FPS)

    duration_secs = 0.0
    dur = raw.get("duration")
    if isinstance(dur, dict) and isinstance(dur.get("secs"), (int, float)):
        duration_secs = float(dur["secs"])

    markers = tuple(_marker_from_catdv(m, fps) for m in raw.get("markers", []) or [])
    fields = {
        identifier: FieldValue(
            identifier=identifier,
            value=value,
            is_multi=isinstance(value, list),
        )
        for identifier, value in (raw.get("fields") or {}).items()
    }
    notes: dict[str, str] = {}
    for key in ("notes", "bigNotes"):
        v = raw.get(key)
        if isinstance(v, str):
            notes[key] = v

    media = MediaRef(
        mime_type=raw.get("format") or "video/quicktime",
        size_bytes=None,
        cached_path=None,
        upstream_handle=clip_id,
    )

    return CanonicalClip(
        key=("catdv", clip_id),
        name=str(raw.get("name", "")),
        duration_secs=duration_secs,
        fps=fps,
        markers=markers,
        fields=fields,
        notes=notes,
        media=media,
        provider_data=raw,
        fetched_at=fetched_at,
    )


def _marker_from_catdv(raw: dict[str, Any], fps: float) -> Marker:
    return Marker(
        name=str(raw.get("name", "")),
        in_=_timecode_from_catdv(raw.get("in") or {}, fps),
        out=_timecode_from_catdv(raw["out"], fps) if isinstance(raw.get("out"), dict) else None,
        description=raw.get("description"),
        category=raw.get("category"),
        color=raw.get("color"),
    )


def _timecode_from_catdv(raw: dict[str, Any], default_fps: float) -> Timecode:
    fps_v = raw.get("fmt")
    fps = float(fps_v) if isinstance(fps_v, (int, float)) and fps_v > 0 else default_fps
    secs_v = raw.get("secs")
    secs = float(secs_v) if isinstance(secs_v, (int, float)) else 0.0
    frm_v = raw.get("frm")
    frm = int(frm_v) if isinstance(frm_v, int) else None
    txt = raw.get("txt") if isinstance(raw.get("txt"), str) else None
    return Timecode(secs=secs, fps=fps, frm=frm, txt=txt)


def marker_to_catdv(marker: Marker, fps: float) -> dict[str, Any]:
    out: dict[str, Any] = {
        "name": marker.name,
        "in": _timecode_to_catdv(marker.in_, fps),
    }
    if marker.out is not None:
        out["out"] = _timecode_to_catdv(marker.out, fps)
    if marker.description is not None:
        out["description"] = marker.description
    if marker.category is not None:
        out["category"] = marker.category
    if marker.color is not None:
        out["color"] = marker.color
    return out


def _timecode_to_catdv(tc: Timecode, default_fps: float) -> dict[str, Any]:
    fps = tc.fps if tc.fps > 0 else default_fps
    secs = float(tc.secs)
    frm = tc.frm if tc.frm is not None else round(secs * fps)
    txt = tc.txt if tc.txt is not None else secs_to_smpte(secs, fps)
    return {"frm": frm, "fmt": float(fps), "secs": secs, "txt": txt}
