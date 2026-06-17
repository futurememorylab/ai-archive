"""CatDV PUT-body builder. Translates a list of ChangeOps into the minimal
JSON body CatDV expects (markers, notes, fields)."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from backend.app.archive.model import (
    AddMarkers,
    AppendNote,
    ChangeOp,
    ReplaceNote,
    SetField,
)
from backend.app.archive.providers.catdv.mapping import marker_to_catdv

NOTE_SEPARATOR = "\n\n---\n\n"
DEFAULT_FPS = 25.0
# CatDV's built-in note properties live at the top level of the clip JSON,
# not in the user-defined `fields` map (see mapping.from_catdv_clip, which
# reads them top-level). A note op targeting one of these must be written
# top-level so it round-trips; any other target is a user field.
TOP_LEVEL_NOTE_TARGETS = ("notes", "bigNotes")


def build_put_payload(
    *,
    current: dict[str, Any],
    ops: Iterable[ChangeOp],
) -> dict[str, Any]:
    """Build a minimal CatDV PUT body from a list of ChangeOps.

    Invariants:
      - markers array is replaced wholesale by CatDV PUT, so any AddMarkers op
        must merge with existing markers and dedupe on in.frm.
      - other arrays/fields not touched by ops do NOT appear in the payload.
      - AppendNote joins with a separator when prior text exists.
    """
    payload: dict[str, Any] = {}
    fps = _clip_fps(current)

    ops_list = list(ops)
    marker_ops = [o for o in ops_list if isinstance(o, AddMarkers)]
    if marker_ops:
        existing = list(current.get("markers") or [])
        existing_frms = {_in_frm(m) for m in existing if _in_frm(m) is not None}
        new_markers: list[dict[str, Any]] = []
        for op in marker_ops:
            for marker in op.markers:
                raw = marker_to_catdv(marker, fps)
                frm = _in_frm(raw)
                if frm is not None and frm in existing_frms:
                    continue
                new_markers.append(raw)
                if frm is not None:
                    existing_frms.add(frm)
        payload["markers"] = existing + new_markers

    field_changes: dict[str, Any] = {}

    def _emit_note(target: str, text: str) -> None:
        if target in TOP_LEVEL_NOTE_TARGETS:
            payload[target] = text
        else:
            field_changes[target] = text

    for op in ops_list:
        if isinstance(op, SetField):
            field_changes[op.identifier] = op.value
        elif isinstance(op, AppendNote):
            existing_text = _existing_text(current, op.target) or ""
            if existing_text == op.text or existing_text.endswith(NOTE_SEPARATOR + op.text):
                # Idempotent re-drain: the append already landed (the live note
                # is the text, or ends with the separated segment). Re-applying
                # after a crash / lost PUT response would duplicate it, so skip.
                continue
            if existing_text:
                _emit_note(op.target, existing_text + NOTE_SEPARATOR + op.text)
            else:
                _emit_note(op.target, op.text)
        elif isinstance(op, ReplaceNote):
            _emit_note(op.target, op.text)

    if field_changes:
        payload["fields"] = field_changes
    return payload


def _clip_fps(current: dict[str, Any]) -> float:
    fps = current.get("fps")
    if isinstance(fps, (int, float)) and fps > 0:
        return float(fps)
    for m in current.get("markers") or []:
        in_obj = m.get("in") if isinstance(m, dict) else None
        if isinstance(in_obj, dict):
            f = in_obj.get("fmt")
            if isinstance(f, (int, float)) and f > 0:
                return float(f)
    return DEFAULT_FPS


def _in_frm(marker: dict[str, Any]) -> int | None:
    in_obj = marker.get("in") if isinstance(marker, dict) else None
    if isinstance(in_obj, dict):
        v = in_obj.get("frm")
        if isinstance(v, int):
            return v
    return None


def _existing_text(current: dict[str, Any], identifier: str) -> str | None:
    if identifier in ("notes", "bigNotes"):
        v = current.get(identifier)
        return v if isinstance(v, str) else None
    fields = current.get("fields") or {}
    v = fields.get(identifier)
    return v if isinstance(v, str) else None
