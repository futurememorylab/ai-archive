from typing import Any

from backend.app.models.annotation import ReviewItem
from backend.app.models.template import TargetEntry, TargetMap


def expand(
    structured: dict[str, Any],
    target_map: TargetMap,
    *,
    annotation_id: int,
    catdv_clip_id: int,
) -> list[ReviewItem]:
    """Walk target_map; emit one ReviewItem per concrete change."""
    items: list[ReviewItem] = []
    for key, entry in target_map.fields.items():
        if key not in structured or structured[key] is None:
            continue
        value = structured[key]
        items.extend(_expand_one(entry, value, annotation_id, catdv_clip_id))
    return items


def _expand_one(
    entry: TargetEntry, value: Any, annotation_id: int, catdv_clip_id: int
) -> list[ReviewItem]:
    if entry.kind == "markers":
        if not isinstance(value, list):
            return []
        return [
            ReviewItem(
                annotation_id=annotation_id,
                catdv_clip_id=catdv_clip_id,
                kind="marker",
                proposed_value=m,
            )
            for m in value
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
