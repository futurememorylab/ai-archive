"""CatDV PUT-body builder. Translates a list of ChangeOps into the minimal
JSON body CatDV expects (markers, notes, fields)."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from backend.app.archive.model import (
    AddMarkers,
    AppendNote,
    ChangeOp,
    ReconcileMarkers,
    ReplaceNote,
    SetField,
)
from backend.app.archive.providers.catdv.mapping import marker_to_catdv
from backend.app.archive.providers.catdv.text_repair import demojibake_marker

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
    add_ops = [o for o in ops_list if isinstance(o, AddMarkers)]
    reconcile_ops = [o for o in ops_list if isinstance(o, ReconcileMarkers)]
    if add_ops or reconcile_ops:
        # CatDV replaces the markers array wholesale on PUT, so `working` is the
        # final set we send. Start from the live clip's markers and apply ops.
        working = list(current.get("markers") or [])
        # AddMarkers (additive publish-forward): our markers win on a same-frm
        # conflict (anti-mojibake); markers we don't touch are preserved.
        if add_ops:
            new_markers: list[dict[str, Any]] = []
            new_frms: set[int] = set()
            for op in add_ops:
                for marker in op.markers:
                    raw = marker_to_catdv(marker, fps)
                    frm = _in_frm(raw)
                    if frm is not None and frm in new_frms:
                        continue
                    new_markers.append(raw)
                    if frm is not None:
                        new_frms.add(frm)
            working = [m for m in working if _in_frm(m) not in new_frms] + new_markers
        # ReconcileMarkers (switch / Make-live): drop OUR other-version markers
        # (drop_frm) and re-assert the target's (desired), but KEEP markers we
        # never authored (pre-existing / human). Frames derive from the clip's
        # real fps via the Timecode fps=0.0 sentinel.
        for op in reconcile_ops:
            desired = [marker_to_catdv(m, fps) for m in op.desired]
            desired_frm = {_in_frm(m) for m in desired}
            drop_frm = {round(s * fps) for s in op.drop_secs}
            working = [
                m
                for m in working
                if _in_frm(m) not in drop_frm and _in_frm(m) not in desired_frm
            ] + desired
        # Never PUT compounding mojibake: repair every marker's text before send
        # so CatDV's per-write mis-encoding can add at most one layer (it can no
        # longer grow unbounded and overflow its length-limited column). Covers
        # both kept (foreign) and our re-asserted markers. See text_repair.
        payload["markers"] = [demojibake_marker(m) for m in working]

    field_changes: dict[str, Any] = {}
    # Accumulate note text per target across this batch's ops. The SyncEngine
    # merges every pending op for a clip into ONE ChangeSet, so two AppendNotes
    # to the same target arrive together; reading the live `current` afresh for
    # each would make the second clobber the first (silent data loss). Seed
    # lazily from the live clip on first touch, then chain off the running value.
    note_text: dict[str, str] = {}

    def _current_note_text(target: str) -> str:
        if target not in note_text:
            note_text[target] = _existing_text(current, target) or ""
        return note_text[target]

    def _emit_note(target: str, text: str) -> None:
        note_text[target] = text
        if target in TOP_LEVEL_NOTE_TARGETS:
            payload[target] = text
        else:
            field_changes[target] = text

    for op in ops_list:
        if isinstance(op, SetField):
            field_changes[op.identifier] = op.value
        elif isinstance(op, AppendNote):
            existing_text = _current_note_text(op.target)
            if existing_text == op.text or existing_text.endswith(NOTE_SEPARATOR + op.text):
                # Idempotent re-drain: the append already landed (the running
                # note is the text, or ends with the separated segment).
                # Re-applying after a crash / lost PUT response would duplicate
                # it, so skip.
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
    if identifier in TOP_LEVEL_NOTE_TARGETS:
        v = current.get(identifier)
        return v if isinstance(v, str) else None
    fields = current.get("fields") or {}
    v = fields.get(identifier)
    return v if isinstance(v, str) else None
