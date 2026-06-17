"""Map an Annotation + its ReviewItems into the right-aside view-model.

The result is a dict with the same `markers / fields / notes` shapes the
existing Published view renders, so the Markers / Fields / Notes panels
can render Draft or Published through the same Jinja partial.
"""

from __future__ import annotations

from typing import Any

from backend.app.models.annotation import Annotation, ReviewItem
from backend.app.ui.view_models import _fix


def _effective_value(item: ReviewItem) -> Any:
    """The value to display/apply: the human edit if present, else the AI proposal.

    Mirrors the apply path (``edited_value if not None else proposed_value``) so a
    persisted edit (e.g. a dragged marker time) shows on reload instead of reverting.
    """
    return item.edited_value if item.edited_value is not None else item.proposed_value


def _unwrap(value: Any) -> Any:
    """Unwrap {"value": X} dict wrappers that some prompt schemas produce."""
    if isinstance(value, dict) and "value" in value:
        return value["value"]
    return value


def _marker_from_review(item: ReviewItem) -> dict[str, Any]:
    src = _unwrap(_effective_value(item))
    pv: dict[str, Any] = src if isinstance(src, dict) else {}
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
        "item_id": item.id,
        "kind": "marker",
        "decision": item.decision,
        "applied_at": item.applied_at,
        "synced_at": item.synced_at,
    }


def _field_from_review(item: ReviewItem) -> dict[str, Any]:
    identifier = item.target_identifier or ""
    value = _unwrap(_effective_value(item))
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
        "multi": isinstance(value, list),
        "item_id": item.id,
        "kind": "field",
        "decision": item.decision,
        "applied_at": item.applied_at,
        "synced_at": item.synced_at,
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
    `studio_run_id`; the dict shape is identical. `fps` and `big_notes`
    are included so studio (which has no enclosing clip.* Alpine scope)
    can render through the same template without a fallback path."""
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
            "note_items": [],
            "big_notes": None,
            "fps": fps,
        }
    markers = [_marker_from_review(it) for it in review_items if it.kind == "marker"]
    markers.sort(key=lambda m: m["in_secs"])
    fields = [_field_from_review(it) for it in review_items if it.kind == "field"]
    fields.sort(key=lambda f: f["identifier"])
    note_texts = [
        _fix(str(_unwrap(_effective_value(it)))) or ""
        for it in review_items
        if it.kind == "note" and _effective_value(it) is not None
    ]
    notes = "\n\n".join(t for t in note_texts if t) or None
    note_items = [
        {
            "item_id": it.id,
            "kind": "note",
            "decision": it.decision,
            "applied_at": it.applied_at,
            "synced_at": it.synced_at,
            "identifier": it.target_identifier,
            "text": _fix(str(_unwrap(_effective_value(it)))) or "",
        }
        for it in review_items
        if it.kind == "note" and _effective_value(it) is not None
    ]
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
        "note_items": note_items,
        "big_notes": None,
        "fps": fps,
    }
