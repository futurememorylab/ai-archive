import json

from backend.app.archive.change_set_json import (
    change_op_from_dict,
    change_op_from_json,
    change_op_to_dict,
    change_op_to_json,
    change_set_from_dict,
    change_set_to_dict,
)
from backend.app.archive.model import (
    AddMarkers,
    AppendNote,
    ChangeSet,
    Marker,
    ReconcileMarkers,
    ReplaceAIMarkers,
    ReplaceNote,
    SetField,
    Timecode,
)


def test_set_field_round_trip_json():
    op = SetField(identifier="pragafilm.theme", value=["a", "b"])
    assert change_op_from_json(change_op_to_json(op)) == op


def test_append_note_round_trip_json():
    op = AppendNote(target="notes", text="hello")
    assert change_op_from_json(change_op_to_json(op)) == op


def test_replace_note_round_trip_json():
    op = ReplaceNote(target="bigNotes", text="goodbye")
    assert change_op_from_json(change_op_to_json(op)) == op


def test_add_markers_round_trip_json():
    m = Marker(
        name="a",
        in_=Timecode(secs=0.0, fps=25.0, frm=0),
        out=Timecode(secs=1.0, fps=25.0, frm=25),
        description="d",
        category="c",
        color="#ff0000",
    )
    op = AddMarkers(markers=(m,))
    decoded = change_op_from_json(change_op_to_json(op))
    assert decoded == op


def test_change_set_round_trip_dict():
    cs = ChangeSet(
        clip_key=("catdv", "42"),
        ops=(
            SetField(identifier="x", value=1),
            AppendNote(target="notes", text="hi"),
        ),
        expected_etag="2026-05-19T00:00:00Z",
    )
    payload = change_set_to_dict(cs)
    # must round-trip through json (the wire format).
    rehydrated = json.loads(json.dumps(payload))
    decoded = change_set_from_dict(rehydrated)
    assert decoded == cs


def test_change_op_from_dict_rejects_unknown_kind():
    try:
        change_op_from_dict({"kind": "Frobnicate"})
    except ValueError as exc:
        assert "Frobnicate" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected ValueError")


def test_change_op_to_dict_rejects_unknown_type():
    class Bogus:
        pass

    try:
        change_op_to_dict(Bogus())  # type: ignore[arg-type]
    except TypeError as exc:
        assert "Bogus" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected TypeError")


def test_reconcile_markers_round_trips():
    op = ReconcileMarkers(
        desired=(Marker(name="A", in_=Timecode(secs=4.0, fps=0.0), out=None),),
        drop_secs=(8.0, 12.0),
    )
    back = change_op_from_json(change_op_to_json(op))
    assert back == op
    assert isinstance(back, ReconcileMarkers)


def test_replace_ai_markers_round_trips():
    op = ReplaceAIMarkers(
        desired=(
            Marker(
                name="[Interview] Two men talk",
                in_=Timecode(secs=4.0, fps=0.0),
                out=None,
                description="d",
                category="AI",
            ),
        )
    )
    back = change_op_from_json(change_op_to_json(op))
    assert back == op
    assert isinstance(back, ReplaceAIMarkers)


def test_replace_ai_markers_empty_desired_round_trips():
    # A fields-only draft emits ReplaceAIMarkers with no markers only when it
    # has marker items; but the op itself must still serialise with an empty
    # desired list without error.
    op = ReplaceAIMarkers(desired=())
    back = change_op_from_json(change_op_to_json(op))
    assert back == op


def test_legacy_add_and_reconcile_markers_still_deserialize():
    # Rows already queued before this change must still drain: the two legacy
    # ops keep deserialising from their on-the-wire JSON.
    add_json = (
        '{"kind": "AddMarkers", "markers": [{"name": "legacy", '
        '"in_": {"secs": 1.0, "fps": 25.0, "frm": 25, "txt": null}, '
        '"out": null, "description": null, "category": "Scene", "color": null}]}'
    )
    reconcile_json = '{"kind": "ReconcileMarkers", "desired": [], "drop_secs": [1.0, 2.0]}'
    add = change_op_from_json(add_json)
    reconcile = change_op_from_json(reconcile_json)
    assert isinstance(add, AddMarkers)
    assert add.markers[0].name == "legacy"
    assert isinstance(reconcile, ReconcileMarkers)
    assert reconcile.drop_secs == (1.0, 2.0)
