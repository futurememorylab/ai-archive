"""TargetMap expansion — turns a Gemini structured output + a prompt's
TargetMap into ReviewItems (markers / fields / notes), with clamping of
out-of-range timestamps."""

from typing import Any

from backend.app.models.annotation import ReviewItem
from backend.app.models.prompt import TargetEntry, TargetMap


def _filter_markers(markers: list, duration_secs: float) -> list:
    """Drop markers whose `in.secs` is past the clip end; clamp `out.secs`
    to the clip end when it overshoots. Markers without numeric `in.secs`
    are passed through unchanged."""
    out: list = []
    for m in markers:
        if not isinstance(m, dict):
            continue
        in_part = m.get("in")
        if not isinstance(in_part, dict) or "secs" not in in_part:
            out.append(m)
            continue
        try:
            in_secs = float(in_part["secs"])
        except (TypeError, ValueError):
            out.append(m)
            continue
        if in_secs >= duration_secs:
            continue
        out_part = m.get("out")
        if isinstance(out_part, dict) and "secs" in out_part:
            try:
                out_secs = float(out_part["secs"])
            except (TypeError, ValueError):
                out.append(m)
                continue
            if out_secs > duration_secs:
                m = {**m, "out": {**out_part, "secs": duration_secs}}
        out.append(m)
    return out


def expand(
    structured: dict[str, Any],
    target_map: TargetMap,
    *,
    annotation_id: int,
    catdv_clip_id: int,
    clip_duration_secs: float | None = None,
) -> list[ReviewItem]:
    """Walk target_map; emit one ReviewItem per concrete change.

    `clip_duration_secs`, if supplied, is used to drop or clamp marker
    timestamps that fall outside the clip — Gemini occasionally hallucinates
    content past the end on multi-minute video.
    """
    items: list[ReviewItem] = []
    for key, entry in target_map.fields.items():
        if key not in structured or structured[key] is None:
            continue
        value = structured[key]
        items.extend(_expand_one(entry, value, annotation_id, catdv_clip_id, clip_duration_secs))
    return items


def _expand_one(
    entry: TargetEntry,
    value: Any,
    annotation_id: int,
    catdv_clip_id: int,
    clip_duration_secs: float | None = None,
) -> list[ReviewItem]:
    if entry.kind == "markers":
        if not isinstance(value, list):
            return []
        markers = (
            _filter_markers(value, clip_duration_secs) if clip_duration_secs is not None else value
        )
        return [
            ReviewItem(
                annotation_id=annotation_id,
                catdv_clip_id=catdv_clip_id,
                kind="marker",
                proposed_value=m,
            )
            for m in markers
        ]
    if entry.kind == "field":
        return [
            ReviewItem(
                annotation_id=annotation_id,
                catdv_clip_id=catdv_clip_id,
                kind="field",
                target_identifier=entry.identifier,
                proposed_value=value,
            )
        ]
    if entry.kind == "note":
        return [
            ReviewItem(
                annotation_id=annotation_id,
                catdv_clip_id=catdv_clip_id,
                kind="note",
                target_identifier=entry.target,
                proposed_value=value,
            )
        ]
    return []
