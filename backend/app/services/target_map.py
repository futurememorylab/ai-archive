"""TargetMap expansion — turns a Gemini structured output + a prompt's
TargetMap into ReviewItems (markers / fields / notes), with clamping of
out-of-range timestamps."""

import logging
from bisect import bisect_right
from typing import Any

from backend.app.models.annotation import ReviewItem
from backend.app.models.prompt import TargetEntry, TargetMap

log = logging.getLogger(__name__)


def _secs(part: Any) -> float | None:
    """`part["secs"]` as a float, or None when missing / non-numeric."""
    if not isinstance(part, dict) or "secs" not in part:
        return None
    try:
        return float(part["secs"])
    except (TypeError, ValueError):
        return None


def _parse_marker(m: Any, duration_secs: float | None) -> tuple[float, float | None] | None:
    """(in, out) for one raw marker, clamped to [0, duration]; None drops it.

    Drops markers without a usable `in.secs` (earlier versions passed them
    through and renderers defaulted them to phantom markers at 0.0), whose
    `in.secs` is past the clip end, or whose `out.secs` is non-numeric.
    A missing `out` makes a point marker. A negative `in` becomes 0 and an
    overshooting `out` becomes the clip end."""
    if not isinstance(m, dict):
        return None
    in_secs = _secs(m.get("in"))
    if in_secs is None:
        return None
    in_secs = max(in_secs, 0.0)
    if duration_secs is not None and in_secs >= duration_secs:
        return None
    out_part = m.get("out")
    if not isinstance(out_part, dict) or "secs" not in out_part:
        return in_secs, None
    out_secs = _secs(out_part)
    if out_secs is None:
        return None
    if duration_secs is not None:
        out_secs = min(out_secs, duration_secs)
    return in_secs, out_secs


def _repaired_out(in_secs: float, out_secs: float | None, next_in: float | None) -> float | None:
    """Fix an `out` that ends at/before its own `in` (#173: "07:32 – 07:03")
    or runs past `next_in`: the next later range marker's `in`, or the clip
    end. A backwards `out` is not trusted: the marker runs to `next_in`, or
    loses its `out` when there is nothing later to run to."""
    if out_secs is None:
        return None
    if out_secs <= in_secs or (next_in is not None and out_secs > next_in):
        return next_in
    return out_secs


def _next_start(starts: list[float], in_secs: float, end: float | None) -> float | None:
    """First of the sorted `starts` strictly after `in_secs`, else `end`
    (`_parse_marker` already keeps every `in` before the clip end)."""
    i = bisect_right(starts, in_secs)
    return starts[i] if i < len(starts) else end


def _with_secs(m: dict, in_secs: float, out_secs: float | None) -> dict:
    """Copy of `m` with the given in/out secs; drops `out` when None."""
    m = {**m, "in": {**m["in"], "secs": in_secs}}
    if out_secs is None:
        m.pop("out", None)
    else:
        m["out"] = {**m["out"], "secs": out_secs}
    return m


def _filter_markers(markers: list, duration_secs: float | None) -> list:
    """Validate and repair a Gemini marker list before it becomes a draft.

    Gemini's timestamps are not trustworthy (#173): markers come back out
    of order, overlapping, with `out` before `in`, or past the clip end.
    Drop what can't be placed (`_parse_marker`), sort by `in`, then make
    every `out` end after its own `in` and no later than the next later
    range marker's `in` (`_repaired_out`). Point markers (no `out`) stay
    point markers and don't cut ranges. Scenes that share a start can't be
    told apart and are left overlapping each other."""
    parsed = [(p, m) for m in markers if (p := _parse_marker(m, duration_secs)) is not None]
    parsed.sort(key=lambda pm: pm[0][0])
    # Only range markers bound each other; a point marker overlaps nothing.
    starts = [i for (i, o), _ in parsed if o is not None]
    out: list = []
    repaired = 0
    for (in_secs, out_secs), m in parsed:
        next_in = _next_start(starts, in_secs, duration_secs)
        fixed = _repaired_out(in_secs, out_secs, next_in)
        repaired += fixed != out_secs
        out.append(_with_secs(m, in_secs, fixed))
    if repaired:
        log.warning("repaired %d marker out-point(s) (backwards or overlapping)", repaired)
    return out


def expand(
    structured: dict[str, Any],
    target_map: TargetMap,
    *,
    catdv_clip_id: int,
    annotation_id: int | None = None,
    studio_run_id: int | None = None,
    clip_duration_secs: float | None = None,
) -> list[ReviewItem]:
    """Walk target_map; emit one ReviewItem per concrete change.

    Exactly one of `annotation_id` or `studio_run_id` must be supplied.
    `clip_duration_secs`, if supplied, is used to drop or clamp marker
    timestamps that fall outside the clip — Gemini occasionally hallucinates
    content past the end on multi-minute video.
    """
    if (annotation_id is None) == (studio_run_id is None):
        raise ValueError("expand() requires exactly one of annotation_id or studio_run_id")
    items: list[ReviewItem] = []
    for key, entry in target_map.fields.items():
        if key not in structured or structured[key] is None:
            continue
        value = structured[key]
        items.extend(
            _expand_one(
                entry,
                value,
                annotation_id=annotation_id,
                studio_run_id=studio_run_id,
                catdv_clip_id=catdv_clip_id,
                clip_duration_secs=clip_duration_secs,
            )
        )
    return items


def _expand_one(
    entry: TargetEntry,
    value: Any,
    *,
    annotation_id: int | None,
    studio_run_id: int | None,
    catdv_clip_id: int,
    clip_duration_secs: float | None = None,
) -> list[ReviewItem]:
    if entry.kind == "markers":
        if not isinstance(value, list):
            return []
        # Always run _filter_markers (even when duration is unknown) so
        # malformed markers without in.secs are dropped at the boundary
        # rather than rendered as phantom markers at t=0 downstream.
        markers = _filter_markers(value, clip_duration_secs)
        return [
            ReviewItem(
                annotation_id=annotation_id,
                studio_run_id=studio_run_id,
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
                studio_run_id=studio_run_id,
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
                studio_run_id=studio_run_id,
                catdv_clip_id=catdv_clip_id,
                kind="note",
                target_identifier=entry.target,
                proposed_value=value,
            )
        ]
    return []
