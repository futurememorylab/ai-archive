"""JSON (de)serialisation for ChangeOp and ChangeSet.

Round-trips through `pending_operations.op_json` (one row per ChangeOp) and
through any other channel where a ChangeSet needs to cross a process boundary.
"""

from __future__ import annotations

import json
from typing import Any

from backend.app.archive.model import (
    AddMarkers,
    AppendNote,
    ChangeOp,
    ChangeSet,
    Marker,
    ReconcileMarkers,
    ReplaceNote,
    SetField,
    Timecode,
)


def _tc_to_dict(tc: Timecode) -> dict[str, Any]:
    return {"secs": tc.secs, "fps": tc.fps, "frm": tc.frm, "txt": tc.txt}


def _tc_from_dict(d: dict[str, Any]) -> Timecode:
    return Timecode(
        secs=float(d["secs"]),
        fps=float(d["fps"]),
        frm=d.get("frm"),
        txt=d.get("txt"),
    )


def _marker_to_dict(m: Marker) -> dict[str, Any]:
    return {
        "name": m.name,
        "in_": _tc_to_dict(m.in_),
        "out": _tc_to_dict(m.out) if m.out is not None else None,
        "description": m.description,
        "category": m.category,
        "color": m.color,
    }


def _marker_from_dict(d: dict[str, Any]) -> Marker:
    return Marker(
        name=d["name"],
        in_=_tc_from_dict(d["in_"]),
        out=_tc_from_dict(d["out"]) if d.get("out") else None,
        description=d.get("description"),
        category=d.get("category"),
        color=d.get("color"),
    )


def change_op_to_dict(op: ChangeOp) -> dict[str, Any]:
    if isinstance(op, AddMarkers):
        return {
            "kind": "AddMarkers",
            "markers": [_marker_to_dict(m) for m in op.markers],
        }
    if isinstance(op, SetField):
        return {"kind": "SetField", "identifier": op.identifier, "value": op.value}
    if isinstance(op, AppendNote):
        return {"kind": "AppendNote", "target": op.target, "text": op.text}
    if isinstance(op, ReplaceNote):
        return {"kind": "ReplaceNote", "target": op.target, "text": op.text}
    if isinstance(op, ReconcileMarkers):
        return {
            "kind": "ReconcileMarkers",
            "desired": [_marker_to_dict(m) for m in op.desired],
            "drop_secs": list(op.drop_secs),
        }
    raise TypeError(f"unknown ChangeOp: {type(op).__name__}")


def change_op_from_dict(d: dict[str, Any]) -> ChangeOp:
    k = d.get("kind")
    if k == "AddMarkers":
        return AddMarkers(markers=tuple(_marker_from_dict(m) for m in d["markers"]))
    if k == "SetField":
        return SetField(identifier=d["identifier"], value=d["value"])
    if k == "AppendNote":
        return AppendNote(target=d["target"], text=d["text"])
    if k == "ReplaceNote":
        return ReplaceNote(target=d["target"], text=d["text"])
    if k == "ReconcileMarkers":
        return ReconcileMarkers(
            desired=tuple(_marker_from_dict(m) for m in d["desired"]),
            drop_secs=tuple(float(s) for s in d.get("drop_secs", [])),
        )
    raise ValueError(f"unknown ChangeOp kind: {k!r}")


def change_op_to_json(op: ChangeOp) -> str:
    return json.dumps(change_op_to_dict(op), ensure_ascii=False)


def change_op_from_json(raw: str) -> ChangeOp:
    return change_op_from_dict(json.loads(raw))


def change_set_to_dict(cs: ChangeSet) -> dict[str, Any]:
    return {
        "clip_key": list(cs.clip_key),
        "ops": [change_op_to_dict(o) for o in cs.ops],
        "expected_etag": cs.expected_etag,
    }


def change_set_from_dict(d: dict[str, Any]) -> ChangeSet:
    key = d["clip_key"]
    return ChangeSet(
        clip_key=(key[0], key[1]),
        ops=tuple(change_op_from_dict(o) for o in d["ops"]),
        expected_etag=d.get("expected_etag"),
    )
