from typing import Any

from backend.app.models.annotation import ReviewItem
from backend.app.models.template import TargetMap


NOTE_SEPARATOR = "\n\n---\n\n"


def build_put_payload(
    *,
    current: dict[str, Any],
    accepted_items: list[ReviewItem],
    target_map: TargetMap,
) -> dict[str, Any]:
    """Build the minimal PUT payload for CatDV from accepted review_items.

    Critical invariants:
    - PUT replaces the markers array wholesale; we MUST include all existing markers
      whenever any new marker is added.
    - Other arrays/fields not touched by accepted_items must NOT appear in the payload.
    - Edited values win over proposed values.
    - Markers are deduped on existing in.frm to avoid double-writes on retry.
    """
    payload: dict[str, Any] = {}

    accepted = [it for it in accepted_items if it.decision == "accepted"]

    marker_items = [it for it in accepted if it.kind == "marker"]
    if marker_items:
        existing = current.get("markers", [])
        existing_in_frms = {_in_frm(m) for m in existing if _in_frm(m) is not None}
        new_markers = []
        for it in marker_items:
            value = it.edited_value if it.edited_value is not None else it.proposed_value
            if not isinstance(value, dict):
                continue
            if _in_frm(value) in existing_in_frms:
                continue
            new_markers.append(value)
            existing_in_frms.add(_in_frm(value))
        payload["markers"] = list(existing) + new_markers

    field_changes: dict[str, Any] = {}

    for it in accepted:
        value = it.edited_value if it.edited_value is not None else it.proposed_value
        if it.kind == "field":
            if it.target_identifier is None:
                continue
            field_changes[it.target_identifier] = _unwrap_value(value)
        elif it.kind == "note":
            if it.target_identifier is None:
                continue
            mode = _note_mode(target_map, it.target_identifier)
            new_text = _unwrap_value(value)
            if mode == "append":
                existing_text = _existing_text(current, it.target_identifier)
                if existing_text:
                    field_changes[it.target_identifier] = (
                        existing_text + NOTE_SEPARATOR + str(new_text)
                    )
                else:
                    field_changes[it.target_identifier] = str(new_text)
            else:
                field_changes[it.target_identifier] = str(new_text)

    if field_changes:
        payload["fields"] = field_changes

    return payload


def _in_frm(marker: dict[str, Any]) -> int | None:
    in_obj = marker.get("in") if isinstance(marker, dict) else None
    if isinstance(in_obj, dict):
        v = in_obj.get("frm")
        if isinstance(v, int):
            return v
    return None


def _unwrap_value(value: Any) -> Any:
    if isinstance(value, dict) and "value" in value and "evidence_secs" in value:
        return value["value"]
    return value


def _note_mode(target_map: TargetMap, identifier: str) -> str:
    for entry in target_map.fields.values():
        if entry.kind == "note" and entry.target == identifier:
            return entry.mode
    return "append"


def _existing_text(current: dict[str, Any], identifier: str) -> str | None:
    if identifier in current.get("fields", {}):
        v = current["fields"][identifier]
        return v if isinstance(v, str) else None
    if identifier in ("notes", "bigNotes") and identifier in current:
        v = current[identifier]
        return v if isinstance(v, str) else None
    return None
