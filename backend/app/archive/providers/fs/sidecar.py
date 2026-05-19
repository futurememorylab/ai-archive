"""Sidecar JSON (de)serialisation for the FS archive adapter.

A sidecar is a `<clipname>.annot.json` file living next to its media file.
On-disk shape:

    {
      "markers": [
        {
          "name": str,
          "in":  {"secs": float, "frm": int, "fps": float},
          "out": {"secs": float, "frm": int, "fps": float} | null,
          "description": str | null,
          "category":    str | null,
          "color":       str | null
        },
        ...
      ],
      "fields": {
        "<identifier>": {"value": Any, "is_multi": bool},
        ...
      },
      "notes": {"<name>": "<text>", ...},
      "provider_data": {<opaque>: <any>}      # round-tripped untouched
    }

Unknown top-level keys are preserved by being moved into `provider_data`
on the way in and written back on the way out. The canonical SMPTE `txt`
string is intentionally not persisted — it is a display concern derivable
from `secs+fps` and would otherwise drift if fps detection later changes.
"""

from __future__ import annotations

import json
from typing import Any

from backend.app.archive.model import (
    FieldValue,
    Marker,
    Timecode,
)


SIDECAR_TOP_KEYS = {"markers", "fields", "notes", "provider_data"}


def _tc_to_dict(tc: Timecode) -> dict[str, Any]:
    frm = tc.frm if tc.frm is not None else round(tc.secs * tc.fps)
    return {"secs": float(tc.secs), "frm": int(frm), "fps": float(tc.fps)}


def _tc_from_dict(d: dict[str, Any], default_fps: float) -> Timecode:
    fps_v = d.get("fps")
    fps = float(fps_v) if isinstance(fps_v, (int, float)) and fps_v > 0 else default_fps
    secs_v = d.get("secs")
    secs = float(secs_v) if isinstance(secs_v, (int, float)) else 0.0
    frm_v = d.get("frm")
    frm = int(frm_v) if isinstance(frm_v, int) else None
    return Timecode(secs=secs, fps=fps, frm=frm, txt=None)


def _marker_to_dict(m: Marker) -> dict[str, Any]:
    out: dict[str, Any] = {
        "name": m.name,
        "in": _tc_to_dict(m.in_),
        "out": _tc_to_dict(m.out) if m.out is not None else None,
        "description": m.description,
        "category": m.category,
        "color": m.color,
    }
    return out


def _marker_from_dict(d: dict[str, Any], default_fps: float) -> Marker:
    out_raw = d.get("out")
    return Marker(
        name=str(d.get("name", "")),
        in_=_tc_from_dict(d.get("in") or {}, default_fps),
        out=_tc_from_dict(out_raw, default_fps) if isinstance(out_raw, dict) else None,
        description=d.get("description"),
        category=d.get("category"),
        color=d.get("color"),
    )


def parse_sidecar(
    raw: dict[str, Any] | None, *, default_fps: float
) -> tuple[
    tuple[Marker, ...],
    dict[str, FieldValue],
    dict[str, str],
    dict[str, Any],
]:
    """Decode a sidecar dict into (markers, fields, notes, provider_data).

    `raw=None` (i.e. sidecar missing) returns empty defaults.
    Unknown top-level keys are folded into `provider_data` to round-trip
    untouched on the next write.
    """
    if raw is None:
        return (), {}, {}, {}

    raw_markers = raw.get("markers") or []
    markers = tuple(
        _marker_from_dict(m, default_fps)
        for m in raw_markers
        if isinstance(m, dict)
    )

    raw_fields = raw.get("fields") or {}
    fields: dict[str, FieldValue] = {}
    if isinstance(raw_fields, dict):
        for identifier, entry in raw_fields.items():
            if isinstance(entry, dict):
                value = entry.get("value")
                is_multi = bool(entry.get("is_multi", isinstance(value, list)))
            else:
                value = entry
                is_multi = isinstance(value, list)
            fields[str(identifier)] = FieldValue(
                identifier=str(identifier),
                value=value,
                is_multi=is_multi,
            )

    raw_notes = raw.get("notes") or {}
    notes: dict[str, str] = {}
    if isinstance(raw_notes, dict):
        for k, v in raw_notes.items():
            if isinstance(v, str):
                notes[str(k)] = v

    provider_data: dict[str, Any] = {}
    pd = raw.get("provider_data")
    if isinstance(pd, dict):
        provider_data.update(pd)
    # Preserve any unknown top-level keys.
    for k, v in raw.items():
        if k not in SIDECAR_TOP_KEYS:
            provider_data.setdefault(k, v)

    return markers, fields, notes, provider_data


def render_sidecar(
    *,
    markers: tuple[Marker, ...] | list[Marker],
    fields: dict[str, FieldValue],
    notes: dict[str, str],
    provider_data: dict[str, Any],
) -> dict[str, Any]:
    """Render canonical fragments back into the on-disk dict shape."""
    return {
        "markers": [_marker_to_dict(m) for m in markers],
        "fields": {
            identifier: {"value": fv.value, "is_multi": bool(fv.is_multi)}
            for identifier, fv in fields.items()
        },
        "notes": dict(notes),
        "provider_data": dict(provider_data),
    }


def dumps_sidecar(doc: dict[str, Any]) -> str:
    """Stable, pretty(ish) JSON for diffing."""
    return json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True)


def loads_sidecar(raw: str) -> dict[str, Any]:
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError("sidecar root must be an object")
    return parsed
