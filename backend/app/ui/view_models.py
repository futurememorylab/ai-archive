"""Map CanonicalClip + provider_data into flat dicts for Jinja templates.

Keeps templates logic-free: no `.provider_data["foo"]["bar"]` chains in HTML.
"""

from __future__ import annotations

from typing import Any

import ftfy

from backend.app.archive.model import CanonicalClip, FieldValue, Marker
from backend.app.media_kind import is_image_path

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
    clip_id = int(clip.key[1])
    return {
        "id": clip_id,
        "name": _fix(clip.name),
        "duration_secs": clip.duration_secs,
        "year": _first_value(clip.fields.get(_YEAR_FIELD)),
        "decade": _first_value(clip.fields.get(_DECADE_FIELD)),
        "marker_count": len(clip.markers),
        "cache": cache_status_view(cache_status) if cache_status else None,
        "thumb_url": f"/api/media/{clip_id}/thumb",
        "kind": _media_kind(clip.provider_data),
        "select_value": f"{clip.key[0]}/{clip_id}",
        "row_href": f"/clips/{clip_id}",
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


def _media_kind(provider_data: dict[str, Any]) -> str:
    media = provider_data.get("media") or {}
    return "image" if is_image_path(media.get("filePath")) else "video"


def clip_detail(
    clip: CanonicalClip,
    cache_status: Any | None = None,
) -> dict[str, Any]:
    """Single-clip page view model."""
    clip_id = int(clip.key[1])
    fields_view = [
        _field_view(fv) for ident, fv in clip.fields.items() if ident.startswith(_PRAGAFILM_PREFIX)
    ]
    fields_view.sort(key=lambda f: f["identifier"])

    return {
        "clip": {
            "id": clip_id,
            "name": clip.name,
            "duration_secs": clip.duration_secs,
            "fps": clip.fps or 25.0,
            "format": _format_summary(clip.provider_data),
            "kind": _media_kind(clip.provider_data),
            "media_url": f"/api/media/{clip_id}",
            "markers": [_marker_view(m) for m in sorted(clip.markers, key=lambda m: m.in_.secs)],
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


def batch_view(row: dict) -> dict:
    """Shape a `JobsRepo.list_batches` row into the dict the Batches table
    renders. Pure function (no I/O) so it is unit-tested in isolation.

    Status mirrors the design: running → 'Running X/Y'; not running with
    drafts still awaiting → 'Awaiting review' / 'N to review'; otherwise
    'Applied'.
    """
    ran = int(row["ran"])
    completed = int(row["completed"])
    failed = int(row["failed"])
    awaiting = int(row["awaiting_clips"])
    running = int(row["running_jobs"]) > 0 or int(row["in_flight"]) > 0
    reviewed = max(0, completed - awaiting)

    if running:
        status_state, status_label = "accent", f"Running {completed + failed}/{ran}"
    elif awaiting > 0:
        status_state = ""
        status_label = "Awaiting review" if reviewed == 0 else f"{awaiting} to review"
    else:
        status_state, status_label = "ok", "Applied"

    name = row.get("prompt_name") or "(prompt unavailable)"
    if row.get("prompt_name") and int(row.get("prompt_count", 1)) > 1:
        name = f"{name} + {int(row['prompt_count']) - 1} more"

    job_ids = list(row["job_ids"])
    started = row.get("started_at") or ""
    try:
        from datetime import datetime as _dt

        started = _dt.fromisoformat(started).strftime("%d %b %H:%M")
    except (ValueError, TypeError):
        pass

    # "Review →" jumps straight into the review of the first un-reviewed clip
    # of this batch; fall back to the batch-filtered clips list if none.
    first_pending = row.get("first_pending_clip_id")
    review_href = (
        f"/clips/{int(first_pending)}?review=1"
        if first_pending is not None
        else f"/?batch={','.join(str(i) for i in job_ids)}&anno=for_review"
    )
    # Clicking the batch row opens the clips list filtered to this batch — ALL
    # its files (every job_item, any status), each showing its queued /
    # processing / done / failed badge. No anno filter, so nothing is hidden.
    files_href = f"/?batch={','.join(str(i) for i in job_ids)}"

    return {
        "batch_key": row["batch_key"],
        "id": int(row["primary_job_id"]),
        "job_ids": job_ids,
        "prompt": name,
        "version": row.get("version_num"),
        "model": row.get("model") or "",
        "started": started,
        "ran": ran,
        "completed": completed,
        "failed": failed,
        "reviewed": reviewed,
        "awaiting": awaiting,
        "running": running,
        "pct_done": round((completed + failed) / ran * 100) if ran else 0,
        "pct_reviewed": round(reviewed / completed * 100) if completed else 0,
        "status_state": status_state,
        "status_label": status_label,
        "review_href": review_href,
        "files_href": files_href,
    }
