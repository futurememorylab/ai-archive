"""Map CanonicalClip + provider_data into flat dicts for Jinja templates.

Keeps templates logic-free: no `.provider_data["foo"]["bar"]` chains in HTML.
"""

from __future__ import annotations

from typing import Any

import ftfy

from backend.app.archive.model import CanonicalClip, FieldValue, Marker

_YEAR_FIELD = "pragafilm.rok.natočení"
_DECADE_FIELD = "pragafilm.dekáda.natočení"
_PRAGAFILM_PREFIX = "pragafilm."


def _fix(s: str | None) -> str | None:
    """Repair Czech mojibake that lives in CatDV's legacy marker payloads.

    Some clips were imported via one or two rounds of UTF-8-as-Latin-1
    re-encoding. ftfy's `fix_text` handles the single-round case cleanly
    and never touches strings that already look fine. For the deeper
    cases (e.g. `koÃÂÃÂ¡rkem` → `kočárkem`) ftfy stops at half-fixed
    output, so we then peel additional `latin-1 → utf-8` rounds for as
    long as the round-trip is well-formed and keeps shrinking the
    string (a real fix always removes bytes — mojibake re-encodes one
    byte as two).
    """
    if not s:
        return s
    # Peel raw double/triple mojibake first while the string is still
    # entirely in the Latin-1 range; ftfy can only undo one round on its
    # own. A real fix always shortens the string (one mangled byte was
    # re-encoded as two), so progress is the stop condition.
    for _ in range(3):
        try:
            peeled = s.encode("latin-1").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            break
        if peeled == s or len(peeled) >= len(s):
            break
        s = peeled
    return ftfy.fix_text(s)


def _first_value(fv: FieldValue | None) -> str | None:
    if fv is None:
        return None
    v = fv.value
    if isinstance(v, list):
        return str(v[0]) if v else None
    return str(v) if v is not None else None


def clip_summary(
    clip: CanonicalClip,
    cache_status: Any | None = None,
) -> dict[str, Any]:
    """One row in the clips-list table."""
    pd = clip.provider_data
    raw_notes = pd.get("notes")
    if not raw_notes:
        raw_notes = pd.get("bigNotes") or ""
    notes_excerpt = _fix(raw_notes) or None
    notes_has_more = bool(
        notes_excerpt
        and (len(notes_excerpt) > 140 or notes_excerpt.count("\n") >= 2)
    )
    return {
        "id": int(clip.key[1]),
        "name": _fix(clip.name),
        "duration_secs": clip.duration_secs,
        "year": _first_value(clip.fields.get(_YEAR_FIELD)),
        "decade": _first_value(clip.fields.get(_DECADE_FIELD)),
        "marker_count": len(clip.markers),
        "cache": cache_status_view(cache_status) if cache_status else None,
        "poster_id": pd.get("posterID"),
        "notes_excerpt": notes_excerpt,
        "notes_has_more": notes_has_more,
    }


def _marker_view(m: Marker) -> dict[str, Any]:
    return {
        "name": _fix(m.name),
        "in_secs": m.in_.secs,
        "out_secs": m.out.secs if m.out is not None else None,
        "description": _fix(m.description),
        "category": m.category,
        "color": m.color,
    }


def _field_view(fv: FieldValue) -> dict[str, Any]:
    value = fv.value
    if isinstance(value, list):
        value_str = ", ".join(_fix(str(v)) or "" for v in value)
    elif value is None:
        value_str = ""
    else:
        value_str = _fix(str(value)) or ""
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


def clip_detail(
    clip: CanonicalClip,
    cache_status: Any | None = None,
) -> dict[str, Any]:
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
            "markers": [
                _marker_view(m)
                for m in sorted(clip.markers, key=lambda m: m.in_.secs)
            ],
            "fields": fields_view,
            "notes": _fix(clip.provider_data.get("notes")) or None,
            "big_notes": _fix(clip.provider_data.get("bigNotes")) or None,
            "cache": cache_status_view(cache_status) if cache_status else None,
        },
    }


def cache_status_view(status) -> dict[str, Any]:
    """Render-ready cache hints for the badge + buttons."""
    md, ml, ai = status.layers

    def _shape(layer) -> dict[str, Any]:
        size = int(layer.size_bytes or 0)
        pinned = bool(layer.pinned_by_workspaces)
        return {
            "present": bool(layer.present),
            "pinned": pinned,
            "evictable": bool(layer.evictable),
            "size_bytes": size,
            "size_mb": size // (1024 * 1024),
        }

    return {
        "clip_key": list(status.clip_key),
        "metadata": _shape(md),
        "media_local": _shape(ml),
        "media_ai": _shape(ai),
    }
