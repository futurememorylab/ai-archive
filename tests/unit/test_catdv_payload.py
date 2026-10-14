"""CatDV PUT-body builder under the #123 ownership rule.

The app writes ONLY what it owns: markers with ``category == "AI"`` and the
field ``pragafilm.popis.ai``. Non-AI markers (human, or legacy app markers
written before #123) pass through byte-identical; the AI set is replaced
wholesale; any field/note target outside ``AI_OWNED_FIELDS`` raises
``FatalProviderError``.
"""

import pytest

from backend.app.archive.errors import FatalProviderError
from backend.app.archive.model import (
    AddMarkers,
    AppendNote,
    Marker,
    ReconcileMarkers,
    ReplaceAIMarkers,
    ReplaceNote,
    SetField,
    Timecode,
)
from backend.app.archive.providers.catdv.mapping import MARKER_NAME_MAX
from backend.app.archive.providers.catdv.payload import build_put_payload

OWNED_FIELD = "pragafilm.popis.ai"


def _clip(markers=None, fields=None, notes=None, big_notes=None, fps=25.0):
    out = {"ID": 1, "name": "c", "fps": fps, "markers": markers or [], "fields": fields or {}}
    if notes is not None:
        out["notes"] = notes
    if big_notes is not None:
        out["bigNotes"] = big_notes
    return out


def _ai_marker(name, secs, category=None):
    return Marker(name=name, in_=Timecode(secs=secs, fps=25.0), out=None, category=category)


# --- markers: AI replace + non-AI passthrough -----------------------------


def test_no_ops_returns_empty_payload():
    assert build_put_payload(current=_clip(), ops=[]) == {}


def test_replace_ai_markers_writes_the_desired_set_as_ai():
    op = ReplaceAIMarkers(desired=(_ai_marker("Two men talk", 4.0, category="Interview"),))
    payload = build_put_payload(current=_clip(), ops=[op])
    assert len(payload["markers"]) == 1
    m = payload["markers"][0]
    assert m["category"] == "AI"
    assert m["name"] == "[Interview] Two men talk"  # Gemini category -> name prefix
    assert m["in"]["frm"] == 100
    assert "color" not in m  # color is no longer sent


def test_human_marker_on_the_same_frame_survives_byte_identical():
    """A human (non-AI) marker and an AI marker on the same frame BOTH stay;
    the human one is passed through exactly as it is on the live clip."""
    human = {
        "name": "Žena u řeky",
        "category": "Exteriér",
        "in": {"frm": 100, "fmt": 25.0, "secs": 4.0, "txt": "00:00:04:00"},
        "color": "#abcdef",
    }
    op = ReplaceAIMarkers(desired=(_ai_marker("AI scene", 4.0, category="Scene"),))
    payload = build_put_payload(current=_clip(markers=[human]), ops=[op])
    names = [m["name"] for m in payload["markers"]]
    assert "[Scene] AI scene" in names
    # the human marker is kept, unchanged, including its color and category
    kept = next(m for m in payload["markers"] if m["name"] == "Žena u řeky")
    assert kept == human


def test_legacy_markers_survive_a_replace():
    """Markers the app wrote before #123 carry Gemini categories (not 'AI'),
    so they are treated as human: kept and never touched."""
    legacy = {
        "name": "Interiér",
        "category": "Interiér",
        "in": {"frm": 50, "fmt": 25.0, "secs": 2.0},
    }
    op = ReplaceAIMarkers(desired=(_ai_marker("fresh", 8.0),))
    payload = build_put_payload(current=_clip(markers=[legacy]), ops=[op])
    kept = [m for m in payload["markers"] if m["category"] != "AI"]
    assert kept == [legacy]


def test_replace_ai_markers_removes_the_old_ai_set():
    """Re-publishing replaces the AI set: an AI marker present on the live clip
    but absent from `desired` is dropped (update/delete without marker IDs)."""
    old_ai = {"name": "[Old] gone", "category": "AI", "in": {"frm": 25, "fmt": 25.0, "secs": 1.0}}
    human = {"name": "keep", "category": "Human", "in": {"frm": 0, "fmt": 25.0, "secs": 0.0}}
    op = ReplaceAIMarkers(desired=(_ai_marker("kept", 4.0),))
    payload = build_put_payload(current=_clip(markers=[old_ai, human]), ops=[op])
    names = {m["name"] for m in payload["markers"]}
    assert names == {"keep", "kept"}  # old AI marker gone, human kept, new AI added


def test_empty_desired_still_replaces_ai_set_to_nothing():
    old_ai = {"name": "[Old] gone", "category": "AI", "in": {"frm": 25, "fmt": 25.0, "secs": 1.0}}
    human = {"name": "keep", "category": None, "in": {"frm": 0, "fmt": 25.0, "secs": 0.0}}
    op = ReplaceAIMarkers(desired=())
    payload = build_put_payload(current=_clip(markers=[old_ai, human]), ops=[op])
    assert [m["name"] for m in payload["markers"]] == ["keep"]


def test_freshest_marker_op_wins_when_batches_merge():
    """Two apply batches for one clip merge into one ChangeSet (oldest-first).
    ReplaceAIMarkers is a replace: the freshest op defines the AI set."""
    op1 = ReplaceAIMarkers(desired=(_ai_marker("stale", 4.0), _ai_marker("also-stale", 8.0)))
    op2 = ReplaceAIMarkers(desired=(_ai_marker("fresh", 4.0),))
    payload = build_put_payload(current=_clip(), ops=[op1, op2])
    ai = [m["name"] for m in payload["markers"] if m["category"] == "AI"]
    assert ai == ["fresh"]


def test_marker_name_prefix_stays_within_max():
    long = "y" * (MARKER_NAME_MAX + 50)
    op = ReplaceAIMarkers(desired=(_ai_marker(long, 4.0, category="Interview"),))
    payload = build_put_payload(current=_clip(), ops=[op])
    m = payload["markers"][0]
    assert len(m["name"]) <= MARKER_NAME_MAX
    assert m["name"].startswith("[Interview] ")
    assert m["name"].endswith("…")


# --- legacy marker ops route through the same AI rule ----------------------


def test_legacy_add_markers_routes_through_ai_rule():
    """A queued legacy AddMarkers drains through the ownership rule: its markers
    become the AI set, live non-AI markers are kept."""
    human = {"name": "keep", "category": "X", "in": {"frm": 0, "fmt": 25.0, "secs": 0.0}}
    op = AddMarkers(markers=(_ai_marker("added", 4.0),))
    payload = build_put_payload(current=_clip(markers=[human]), ops=[op])
    assert {m["name"] for m in payload["markers"]} == {"keep", "added"}
    assert next(m for m in payload["markers"] if m["name"] == "added")["category"] == "AI"


def test_legacy_reconcile_markers_routes_through_ai_rule_and_ignores_drop_secs():
    human = {"name": "keep", "category": None, "in": {"frm": 0, "fmt": 25.0, "secs": 0.0}}
    old_ai = {"name": "[old] x", "category": "AI", "in": {"frm": 50, "fmt": 25.0, "secs": 2.0}}
    op = ReconcileMarkers(
        desired=(Marker(name="target", in_=Timecode(secs=8.0, fps=0.0), out=None),),
        drop_secs=(2.0,),  # ignored now
    )
    payload = build_put_payload(current=_clip(markers=[human, old_ai]), ops=[op])
    names = {m["name"] for m in payload["markers"]}
    assert names == {"keep", "target"}  # human kept, old AI replaced


# --- the owned field -------------------------------------------------------


def test_replace_note_to_owned_field_writes_under_fields():
    op = ReplaceNote(target=OWNED_FIELD, text="shrnutí")
    payload = build_put_payload(current=_clip(), ops=[op])
    assert payload == {"fields": {OWNED_FIELD: "shrnutí"}}


def test_set_field_to_owned_field_is_allowed():
    op = SetField(identifier=OWNED_FIELD, value="x")
    payload = build_put_payload(current=_clip(), ops=[op])
    assert payload == {"fields": {OWNED_FIELD: "x"}}


# --- the guard: non-owned targets raise ------------------------------------


def test_write_to_popis_materialu_raises():
    op = ReplaceNote(target="pragafilm.popis.materialu", text="x")
    with pytest.raises(FatalProviderError):
        build_put_payload(current=_clip(), ops=[op])


def test_write_to_decade_field_raises():
    op = SetField(identifier="pragafilm.dekáda.natočení", value="30.léta")
    with pytest.raises(FatalProviderError):
        build_put_payload(current=_clip(), ops=[op])


def test_write_to_top_level_notes_raises():
    op = AppendNote(target="notes", text="x")
    with pytest.raises(FatalProviderError):
        build_put_payload(current=_clip(notes="old"), ops=[op])


def test_write_to_big_notes_raises():
    op = ReplaceNote(target="bigNotes", text="x")
    with pytest.raises(FatalProviderError):
        build_put_payload(current=_clip(big_notes="old"), ops=[op])


# --- note accumulation still works for the one owned field -----------------


def test_append_note_to_owned_field_chains_not_clobber():
    ops = [
        AppendNote(target=OWNED_FIELD, text="a"),
        AppendNote(target=OWNED_FIELD, text="b"),
    ]
    payload = build_put_payload(current=_clip(), ops=ops)
    assert payload["fields"][OWNED_FIELD] == "a\n\n---\n\nb"


def test_append_note_to_owned_field_is_idempotent():
    op = AppendNote(target=OWNED_FIELD, text="summary")
    payload = build_put_payload(
        current=_clip(fields={OWNED_FIELD: "old\n\n---\n\nsummary"}), ops=[op]
    )
    assert payload == {}


def test_markers_and_owned_field_combine_in_one_payload():
    op_m = ReplaceAIMarkers(desired=(_ai_marker("m", 2.0),))
    op_n = ReplaceNote(target=OWNED_FIELD, text="x")
    payload = build_put_payload(current=_clip(), ops=[op_m, op_n])
    assert payload["markers"][0]["category"] == "AI"
    assert payload["fields"][OWNED_FIELD] == "x"
