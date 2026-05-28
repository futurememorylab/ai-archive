"""Map an Annotation + its ReviewItems into the right-aside view-model.

The result is a dict with the same `markers / fields / notes` shapes the
existing Published view renders, so the Markers / Fields / Notes panels
can render Draft or Published through the same Jinja partial.
"""

from __future__ import annotations

from typing import Any

from backend.app.models.annotation import Annotation, ReviewItem
from backend.app.ui.view_models import _fix


def _marker_from_review(item: ReviewItem) -> dict[str, Any]:
    pv: dict[str, Any] = item.proposed_value if isinstance(item.proposed_value, dict) else {}
    in_part = pv.get("in") or {}
    out_part = pv.get("out")
    return {
        "name": _fix(pv.get("name")) or "",
        "category": pv.get("category"),
        "description": _fix(pv.get("description")),
        "in_secs": float(in_part.get("secs", 0.0)),
        "out_secs": float(out_part["secs"])
        if isinstance(out_part, dict) and "secs" in out_part
        else None,
        "color": pv.get("color"),
    }


def _field_from_review(item: ReviewItem) -> dict[str, Any]:
    identifier = item.target_identifier or ""
    value = item.proposed_value
    # Studio schemas wrap field values as {"value": ..., "evidence_secs": [...]}.
    # Clip-detail annotations historically pass raw values. Unwrap when present.
    if isinstance(value, dict) and "value" in value:
        value = value["value"]
    if isinstance(value, list):
        value_str = ", ".join(_fix(str(v)) or "" for v in value)
    elif value is None:
        value_str = ""
    else:
        value_str = _fix(str(value)) or ""
    return {
        "identifier": identifier,
        "name": identifier.split(".")[-1],
        "value": value_str,
    }


def build_draft_view(
    annotation: Annotation | None,
    review_items: list[ReviewItem],
    *,
    prompt_name: str | None = None,
    version_num: int | None = None,
    created_at: str | None = None,
    fps: float = 25.0,
) -> dict[str, Any]:
    """Returns the `panels` dict consumed by templates/pages/_anno_panels.html.

    Used by both the clip-detail draft view and the studio output card.
    Studio callers pass `annotation=None` and `review_items` loaded by
    `studio_run_id`; the dict shape is identical."""
    if annotation is None and not review_items:
        return {
            "has_draft": False,
            "annotation_id": None,
            "created_at": created_at,
            "prompt_name": prompt_name,
            "version_num": version_num,
            "model": None,
            "markers": [],
            "fields": [],
            "notes": None,
            "big_notes": None,
            "fps": fps,
        }
    markers = [_marker_from_review(it) for it in review_items if it.kind == "marker"]
    markers.sort(key=lambda m: m["in_secs"])
    fields = [_field_from_review(it) for it in review_items if it.kind == "field"]
    fields.sort(key=lambda f: f["identifier"])
    note_texts: list[str] = []
    for it in review_items:
        if it.kind != "note" or it.proposed_value is None:
            continue
        raw = it.proposed_value
        if isinstance(raw, dict) and "value" in raw:
            raw = raw["value"]
        text = _fix(str(raw)) or ""
        note_texts.append(text)
    notes = "\n\n".join(t for t in note_texts if t) or None
    return {
        "has_draft": True,
        "annotation_id": annotation.id if annotation else None,
        "created_at": created_at,
        "prompt_name": prompt_name,
        "version_num": version_num,
        "model": annotation.model if annotation else None,
        "markers": markers,
        "fields": fields,
        "notes": notes,
        "big_notes": None,
        "fps": fps,
    }
