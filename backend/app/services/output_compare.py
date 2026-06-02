"""Align two prompt versions' annotation outputs into a scene-level compare
model for the Studio compare table and the linked timeline.

Pure, no I/O. Consumes the `panels` dicts that
backend/app/services/draft_view.build_draft_view produces (markers / fields /
notes) and returns aligned rows. Each row carries one word_diff; the table
renders eq+del on the left (cmp / older) and eq+ins on the right (cur / newer).
"""

from __future__ import annotations

from typing import Any, Literal

from backend.app.services.word_diff import word_diff

SceneStatus = Literal["unchanged", "changed", "added", "removed"]


def _tc(secs: float | None) -> str:
    s = int(round(secs or 0))
    return f"{s // 60}:{s % 60:02d}"


def _out_or_default(m: dict) -> float:
    out = m.get("out_secs")
    # treat None or 0.0 (no real out) as in+1
    return float(out) if out else float(m.get("in_secs") or 0.0) + 1.0


def _dur_s(m: dict) -> int:
    return int(round(_out_or_default(m) - float(m.get("in_secs") or 0.0)))


def _marker_text(m: dict) -> str:
    name = (m.get("name") or "").strip()
    desc = (m.get("description") or "").strip()
    return f"{name}\n{desc}" if desc else name


def _overlaps(a: dict, b: dict) -> bool:
    return (
        float(a.get("in_secs") or 0.0) < _out_or_default(b)
        and float(b.get("in_secs") or 0.0) < _out_or_default(a)
    )


def _side(m: dict) -> dict[str, Any]:
    return {
        "in_secs": float(m.get("in_secs") or 0.0),
        "out_secs": m.get("out_secs"),
        "tc": _tc(m.get("in_secs")),
        "dur_s": _dur_s(m),
        "name": (m.get("name") or "").strip(),
    }


def _time_changed(cmp_m: dict | None, cur_m: dict | None) -> bool:
    """True only for a paired scene whose DISPLAYED start time or duration
    differs. The table shows the in-point timecode + a rounded duration, so we
    compare at that display precision — a sub-second wobble that renders
    identically (e.g. 6.2s vs 6.4s, both "6s") must NOT flag as changed.
    Independent of whether the marker *text* changed."""
    if not cmp_m or not cur_m:
        return False
    return (
        _tc(cmp_m.get("in_secs")) != _tc(cur_m.get("in_secs"))
        or _dur_s(cmp_m) != _dur_s(cur_m)
    )


def _scene_row(
    idx: int, status: SceneStatus, cmp_m: dict | None, cur_m: dict | None, segs: list
) -> dict[str, Any]:
    return {
        "key": f"scene-{idx}",
        "status": status,
        "cmp": _side(cmp_m) if cmp_m else None,
        "cur": _side(cur_m) if cur_m else None,
        "segs": segs,
        "time_changed": _time_changed(cmp_m, cur_m),
    }


def _align_scenes(cmp_markers: list[dict], cur_markers: list[dict]) -> list[dict]:
    rows: list[dict] = []
    i = j = 0
    n, m = len(cmp_markers), len(cur_markers)
    while i < n and j < m:
        a, b = cmp_markers[i], cur_markers[j]
        if _overlaps(a, b):
            at, bt = _marker_text(a), _marker_text(b)
            status = "unchanged" if at == bt else "changed"
            rows.append(_scene_row(len(rows), status, a, b, word_diff(at, bt)))
            i += 1
            j += 1
        elif _out_or_default(a) <= float(b.get("in_secs") or 0.0):
            rows.append(_scene_row(len(rows), "removed", a, None,
                                   word_diff(_marker_text(a), "")))
            i += 1
        else:
            rows.append(_scene_row(len(rows), "added", None, b,
                                   word_diff("", _marker_text(b))))
            j += 1
    while i < n:
        a = cmp_markers[i]
        rows.append(_scene_row(len(rows), "removed", a, None,
                               word_diff(_marker_text(a), "")))
        i += 1
    while j < m:
        b = cur_markers[j]
        rows.append(_scene_row(len(rows), "added", None, b,
                               word_diff("", _marker_text(b))))
        j += 1
    return rows


def _align_fields(cmp_fields: list[dict], cur_fields: list[dict]) -> list[dict]:
    cmp_by = {f.get("identifier", ""): f for f in cmp_fields}
    cur_by = {f.get("identifier", ""): f for f in cur_fields}
    rows: list[dict] = []
    for k in sorted(set(cmp_by) | set(cur_by)):
        c, u = cmp_by.get(k), cur_by.get(k)
        cv = (c.get("value") if c else "") or ""
        uv = (u.get("value") if u else "") or ""
        if c is None:
            status = "added"
        elif u is None:
            status = "removed"
        else:
            status = "unchanged" if cv == uv else "changed"
        rows.append({
            "key": f"field-{k}",
            "identifier": k,
            "status": status,
            "has_cmp": c is not None,
            "has_cur": u is not None,
            "segs": word_diff(cv, uv),
        })
    return rows


def _notes_diff(cmp_panels: dict, cur_panels: dict) -> dict | None:
    cn = (cmp_panels.get("notes") or "").strip()
    un = (cur_panels.get("notes") or "").strip()
    if not cn and not un:
        return None
    segs = word_diff(cn, un)
    return {"segs": segs, "changed": any(s["type"] != "eq" for s in segs)}


def build_output_compare(cur_panels: dict, cmp_panels: dict) -> dict[str, Any]:
    """Align cur (newer) vs cmp (older) panels into scene/field/note rows."""
    scenes = _align_scenes(cmp_panels.get("markers") or [],
                           cur_panels.get("markers") or [])
    fields = _align_fields(cmp_panels.get("fields") or [],
                           cur_panels.get("fields") or [])
    return {
        "scene_count": len(scenes),
        "scenes": scenes,
        "fields": fields,
        "notes": _notes_diff(cmp_panels, cur_panels),
    }
