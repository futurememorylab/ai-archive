# tests/unit/test_version_switch_fidelity.py
"""Offline proof of the full 'Make live' switch path under the #123 ownership
rule: a version's snapshot, through _switch_ops, through build_put_payload,
against a live clip that carries a human marker and a stale AI marker.

Make live re-asserts the target version's markers as the AI set (so a later
AI marker is dropped) while every non-AI marker survives byte-identical, and it
emits NOTHING for notes, popis.materialu or the decade/year fields — the app
writes only what it owns.
"""

from backend.app.archive.model import ReplaceAIMarkers, SetField
from backend.app.archive.providers.catdv.payload import build_put_payload
from backend.app.models.annotation import ClipVersion
from backend.app.services.publish_service import _switch_ops


def _ver(num, markers, state, fields=None, notes=None):
    return ClipVersion(
        catdv_clip_id=1,
        version_num=num,
        snapshot={"markers": markers, "fields": fields or {}, "notes": notes},
        origin="publish",
        publish_state=state,
    )


def test_switch_preserves_human_marker_and_drops_later_ai():
    v1 = _ver(1, [{"name": "A", "category": "Scene", "in": {"secs": 4.0}}], "superseded")
    v2 = _ver(
        2,
        [
            {"name": "A", "category": "Scene", "in": {"secs": 4.0}},
            {"name": "B", "category": "Scene", "in": {"secs": 8.0}},
        ],
        "live",
    )
    ops = _switch_ops(v1, [v2, v1])

    # The live clip: our two AI markers (category "AI") plus a human one.
    current = {
        "ID": 1,
        "fps": 25.0,
        "fields": {},
        "markers": [
            {"name": "[Scene] A", "category": "AI", "in": {"frm": 100, "fmt": 25.0, "secs": 4.0}},
            {"name": "[Scene] B", "category": "AI", "in": {"frm": 200, "fmt": 25.0, "secs": 8.0}},
            {
                "name": "HUMAN",
                "category": "Interview",
                "in": {"frm": 999, "fmt": 25.0, "secs": 39.96},
            },
        ],
    }
    payload = build_put_payload(current=current, ops=ops)
    names = sorted(m["name"] for m in payload["markers"])
    # v1's AI set is just A; B (a later AI marker) is dropped; HUMAN survives.
    assert names == ["HUMAN", "[Scene] A"]
    human = next(m for m in payload["markers"] if m["name"] == "HUMAN")
    assert human["category"] == "Interview"  # untouched


def test_switch_emits_replace_ai_markers_not_reconcile():
    v1 = _ver(1, [{"name": "A", "in": {"secs": 4.0}}], "live")
    ops = _switch_ops(v1, [v1])
    assert any(isinstance(o, ReplaceAIMarkers) for o in ops)


def test_switch_never_emits_non_owned_fields_or_notes():
    """Make live writes only the owned field; it never emits the decade, the
    legacy popis.materialu, or the top-level notes/bigNotes."""
    v1 = _ver(
        1,
        [{"name": "A", "in": {"secs": 4.0}}],
        "live",
        fields={
            "pragafilm.popis.ai": "shrnutí",  # owned → emitted
            "pragafilm.popis.materialu": "human",  # NOT owned
            "pragafilm.dekáda.natočení": "30.léta",  # NOT owned
            "pragafilm.rok.natočení": "1932",  # NOT owned
        },
        notes="top-level note",
    )
    ops = _switch_ops(v1, [v1])
    field_ops = [o for o in ops if isinstance(o, SetField)]
    assert [o.identifier for o in field_ops] == ["pragafilm.popis.ai"]
    # no note op of any kind
    assert all(type(o).__name__ not in ("AppendNote", "ReplaceNote") for o in ops)
    # and the whole op list, through the payload builder, never touches a
    # non-owned target (would raise FatalProviderError if it tried).
    payload = build_put_payload(
        current={"ID": 1, "fps": 25.0, "markers": [], "fields": {}}, ops=ops
    )
    assert set(payload.get("fields", {})) <= {"pragafilm.popis.ai"}
    assert "notes" not in payload and "bigNotes" not in payload
