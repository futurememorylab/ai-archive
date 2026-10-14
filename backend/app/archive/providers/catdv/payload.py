"""CatDV PUT-body builder. Translates a list of ChangeOps into the minimal
JSON body CatDV expects (markers, fields), and is the ONE place the #123
ownership rule is enforced for the CatDV provider.

The app writes only what it owns: markers with ``category == AI_MARKER_CATEGORY``
and the fields in ``AI_OWNED_FIELDS``. Non-AI markers (human, or legacy app
markers) pass through byte-identical; the AI marker set is replaced wholesale;
any field/note target outside ``AI_OWNED_FIELDS`` raises ``FatalProviderError``
rather than being silently written. See ADR 0127.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from backend.app.archive.errors import FatalProviderError
from backend.app.archive.model import (
    AI_MARKER_CATEGORY,
    AI_OWNED_FIELDS,
    AddMarkers,
    AppendNote,
    ChangeOp,
    Marker,
    ReconcileMarkers,
    ReplaceAIMarkers,
    ReplaceNote,
    SetField,
)
from backend.app.archive.providers.catdv.mapping import marker_to_catdv

NOTE_SEPARATOR = "\n\n---\n\n"
DEFAULT_FPS = 25.0

# The marker ops that resolve to "replace the AI set with these markers". Only
# ReplaceAIMarkers is emitted now; AddMarkers / ReconcileMarkers are legacy rows
# that may still be queued and are routed through the SAME ownership rule.
_MARKER_OPS = (ReplaceAIMarkers, AddMarkers, ReconcileMarkers)


def _require_owned_field(target: str) -> None:
    """Refuse any write to a field/note target the app does not own.

    This is the guard that makes the ownership rule un-bypassable: a SetField or
    note op for anything but ``AI_OWNED_FIELDS`` (``pragafilm.popis.materialu``,
    the decade/year fields, the top-level ``notes`` / ``bigNotes``, …) fails
    loudly instead of overwriting human data. Legacy queued ops to old targets
    hit this too, so a stale row can never silently clobber a human field.
    """
    if target not in AI_OWNED_FIELDS:
        raise FatalProviderError(
            f"refusing to write non-AI-owned target {target!r}: the app owns only "
            f"{AI_OWNED_FIELDS} and AI-category markers (ADR 0127)"
        )


def build_put_payload(
    *,
    current: dict[str, Any],
    ops: Iterable[ChangeOp],
) -> dict[str, Any]:
    """Build a minimal CatDV PUT body from a list of ChangeOps.

    Invariants:
      - the markers array is replaced wholesale by CatDV PUT, so it is sent as
        [every live non-AI marker, byte-identical] + [the desired AI set];
      - only ``AI_OWNED_FIELDS`` may be written; any other target raises;
      - arrays/fields not touched by ops do NOT appear in the payload;
      - AppendNote to an owned field joins with a separator when prior text
        exists, and is idempotent on re-drain.
    """
    payload: dict[str, Any] = {}

    ops_list = list(ops)
    marker_ops = [o for o in ops_list if isinstance(o, _MARKER_OPS)]
    if marker_ops:
        payload["markers"] = _owned_markers(current, marker_ops)

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
        field_changes[target] = text

    for op in ops_list:
        if isinstance(op, SetField):
            _require_owned_field(op.identifier)
            field_changes[op.identifier] = op.value
        elif isinstance(op, AppendNote):
            _require_owned_field(op.target)
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
            _require_owned_field(op.target)
            _emit_note(op.target, op.text)

    if field_changes:
        payload["fields"] = field_changes
    return payload


def _owned_markers(
    current: dict[str, Any],
    marker_ops: list[ChangeOp],
) -> list[dict[str, Any]]:
    """The full markers array to PUT under the ownership rule.

    CatDV replaces the markers array wholesale on PUT, so the result is:

        [m for m in live if m.category != AI_MARKER_CATEGORY]   # human: untouched
      + [marker_to_catdv(d) for d in desired]                   # ours: full replace

    which gives update AND delete of the AI set without per-marker IDs, while
    every non-AI marker (human, or a legacy app marker) is preserved exactly.
    A same-frame human/AI collision no longer matters: both stay.

    ReplaceAIMarkers is a *replace*, so when several apply batches for one clip
    merge into one ChangeSet (oldest-first), the FRESHEST marker op defines the
    AI set — concatenating them would resurrect markers a later batch deleted.
    """
    fps = _clip_fps(current)
    live = list(current.get("markers") or [])
    kept = [m for m in live if _category_of(m) != AI_MARKER_CATEGORY]

    desired_markers = _desired_of(marker_ops[-1])
    desired = [marker_to_catdv(m, fps) for m in desired_markers]
    # Marker text is sent as-is: _call_json declares charset=utf-8, so CatDV
    # stores our UTF-8 exactly; the kept non-AI markers are passed through
    # unchanged, with no write-side repair.
    return kept + desired


def _desired_of(op: ChangeOp) -> tuple[Marker, ...]:
    """The marker list a (possibly legacy) marker op wants as the AI set."""
    if isinstance(op, (ReplaceAIMarkers, ReconcileMarkers)):
        return op.desired
    if isinstance(op, AddMarkers):
        return op.markers
    return ()


def _category_of(marker: dict[str, Any]) -> str | None:
    return marker.get("category") if isinstance(marker, dict) else None


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


def _existing_text(current: dict[str, Any], identifier: str) -> str | None:
    # Owned targets are user-defined fields (never the top-level notes/bigNotes),
    # so the running note seeds from the clip's `fields` map.
    fields = current.get("fields") or {}
    v = fields.get(identifier)
    return v if isinstance(v, str) else None
