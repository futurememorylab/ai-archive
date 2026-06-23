"""CatDV <-> canonical model translation helpers. Imported by the CatDV
adapter and by `payload.py` when building PUT requests."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from backend.app.archive.model import (
    CanonicalClip,
    FieldDef,
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


# CatDV's marker `name` column is length-limited; a runaway AI-generated name
# (a whole sentence) makes `replaceMarkers` fail with a DB "Data too long for
# column 'name'" 500 that takes the entire write down. Clamp the name and keep
# the full text in the description so nothing is lost. Conservative default for
# a VARCHAR(255)-ish column; lower it if a given CatDV schema is tighter.
MARKER_NAME_MAX = 200


def _clamp_marker_name(name: str | None, description: str | None) -> tuple[str, str | None]:
    name = name or ""
    if len(name) <= MARKER_NAME_MAX:
        return name, description
    clamped = name[: MARKER_NAME_MAX - 1].rstrip() + "…"
    # Preserve the full original name at the head of the description.
    description = name if not description else f"{name}\n\n{description}"
    return clamped, description


def marker_to_catdv(marker: Marker, fps: float) -> dict[str, Any]:
    name, description = _clamp_marker_name(marker.name, marker.description)
    out: dict[str, Any] = {
        "name": name,
        "in": _timecode_to_catdv(marker.in_, fps),
    }
    if marker.out is not None:
        out["out"] = _timecode_to_catdv(marker.out, fps)
    if description is not None:
        out["description"] = description
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


_CATDV_TYPE_MAP: dict[str, str] = {
    "TEXT": "text",
    "STRING": "text",
    "INTEGER": "integer",
    "INT": "integer",
    "DECIMAL": "decimal",
    "FLOAT": "decimal",
    "DATE": "date",
    "PICKLIST": "picklist",
    "MULTI_PICKLIST": "multi-picklist",
    "BOOLEAN": "bool",
    "BOOL": "bool",
}


def field_def_from_catdv(raw: dict[str, Any]) -> FieldDef:
    identifier = str(raw.get("identifier") or raw.get("id") or raw.get("name") or "")
    name = str(raw.get("name") or identifier)
    raw_type = str(raw.get("type") or "TEXT").upper()
    is_multi_raw = raw.get("multi") or raw.get("isMulti") or False
    mapped_type = _CATDV_TYPE_MAP.get(raw_type, "text")
    if mapped_type == "picklist" and bool(is_multi_raw):
        mapped_type = "multi-picklist"
    pv = raw.get("picklistValues") or raw.get("values") or None
    pv_tuple: tuple[str, ...] | None
    if isinstance(pv, list):
        pv_tuple = tuple(str(v) for v in pv)
    else:
        pv_tuple = None
    return FieldDef(
        identifier=identifier,
        name=name,
        type=mapped_type,  # type: ignore[arg-type]
        is_multi=bool(is_multi_raw) or mapped_type == "multi-picklist",
        is_editable=bool(raw.get("editable", True)),
        picklist_values=pv_tuple,
        provider_data=raw,
    )
