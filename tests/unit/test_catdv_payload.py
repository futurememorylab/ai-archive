import pytest

from backend.app.archive.model import (
    AddMarkers,
    AppendNote,
    Marker,
    ReplaceNote,
    SetField,
    Timecode,
)
from backend.app.archive.providers.catdv.payload import build_put_payload


def _clip(markers=None, fields=None, notes=None, big_notes=None, fps=25.0):
    out = {"ID": 1, "name": "c", "fps": fps, "markers": markers or [], "fields": fields or {}}
    if notes is not None:
        out["notes"] = notes
    if big_notes is not None:
        out["bigNotes"] = big_notes
    return out


def test_no_ops_returns_empty_payload():
    assert build_put_payload(current=_clip(), ops=[]) == {}


def test_add_markers_appends_to_existing_and_normalizes_timecode():
    existing = [
        {
            "name": "m0",
            "in": {"frm": 0, "fmt": 25.0, "secs": 0.0, "txt": "0:00:00:00"},
            "out": {"frm": 25, "fmt": 25.0, "secs": 1.0, "txt": "0:00:01:00"},
        }
    ]
    op = AddMarkers(
        markers=[
            Marker(
                name="m1",
                in_=Timecode(secs=4.0, fps=25.0),
                out=Timecode(secs=6.0, fps=25.0),
            )
        ]
    )
    payload = build_put_payload(current=_clip(markers=existing), ops=[op])
    assert "markers" in payload
    assert len(payload["markers"]) == 2
    new_m = payload["markers"][1]
    assert new_m["in"]["frm"] == 100
    assert new_m["in"]["fmt"] == 25.0
    assert new_m["in"]["txt"] == "00:00:04:00"


def test_add_markers_dedupes_on_existing_in_frm():
    existing = [
        {
            "name": "m0",
            "in": {"frm": 100, "fmt": 25.0, "secs": 4.0, "txt": "0:00:04:00"},
            "out": {"frm": 150, "fmt": 25.0, "secs": 6.0, "txt": "0:00:06:00"},
        }
    ]
    op = AddMarkers(
        markers=[Marker(name="dup", in_=Timecode(secs=4.0, fps=25.0), out=None)]
    )
    payload = build_put_payload(current=_clip(markers=existing), ops=[op])
    assert len(payload["markers"]) == 1


def test_set_field_writes_to_fields_map():
    op = SetField(identifier="pragafilm.dekáda.natočení", value="30.léta")
    payload = build_put_payload(current=_clip(), ops=[op])
    assert payload == {"fields": {"pragafilm.dekáda.natočení": "30.léta"}}


def test_append_note_joins_with_separator_when_existing_present():
    op = AppendNote(target="notes", text="new line")
    payload = build_put_payload(current=_clip(notes="old"), ops=[op])
    assert payload["fields"]["notes"] == "old\n\n---\n\nnew line"


def test_append_note_writes_directly_when_no_existing():
    op = AppendNote(target="notes", text="new")
    payload = build_put_payload(current=_clip(), ops=[op])
    assert payload["fields"]["notes"] == "new"


def test_replace_note_overrides_existing():
    op = ReplaceNote(target="bigNotes", text="fresh")
    payload = build_put_payload(current=_clip(big_notes="old"), ops=[op])
    assert payload["fields"]["bigNotes"] == "fresh"


def test_multiple_ops_combined_in_one_payload():
    op_m = AddMarkers(
        markers=[Marker(name="m", in_=Timecode(secs=2.0, fps=25.0), out=None)]
    )
    op_f = SetField(identifier="pragafilm.barva", value="true")
    op_n = AppendNote(target="notes", text="x")
    payload = build_put_payload(current=_clip(), ops=[op_m, op_f, op_n])
    assert "markers" in payload
    assert payload["fields"]["pragafilm.barva"] == "true"
    assert payload["fields"]["notes"] == "x"
