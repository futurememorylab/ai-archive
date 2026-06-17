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
    op = AddMarkers(markers=[Marker(name="dup", in_=Timecode(secs=4.0, fps=25.0), out=None)])
    payload = build_put_payload(current=_clip(markers=existing), ops=[op])
    assert len(payload["markers"]) == 1


def test_set_field_writes_to_fields_map():
    op = SetField(identifier="pragafilm.dekáda.natočení", value="30.léta")
    payload = build_put_payload(current=_clip(), ops=[op])
    assert payload == {"fields": {"pragafilm.dekáda.natočení": "30.léta"}}


def test_append_note_joins_with_separator_when_existing_present():
    # notes / bigNotes are top-level clip properties in CatDV, NOT user
    # fields — writing them under payload["fields"] sets a phantom field
    # and never updates the real Notes (mapping.from_catdv_clip reads them
    # top-level, so the write must mirror that). See ADR 0090.
    op = AppendNote(target="notes", text="new line")
    payload = build_put_payload(current=_clip(notes="old"), ops=[op])
    assert payload["notes"] == "old\n\n---\n\nnew line"
    assert "fields" not in payload


def test_append_note_writes_directly_when_no_existing():
    op = AppendNote(target="notes", text="new")
    payload = build_put_payload(current=_clip(), ops=[op])
    assert payload["notes"] == "new"
    assert "fields" not in payload


def test_replace_note_overrides_existing():
    op = ReplaceNote(target="bigNotes", text="fresh")
    payload = build_put_payload(current=_clip(big_notes="old"), ops=[op])
    assert payload["bigNotes"] == "fresh"
    assert "fields" not in payload


def test_note_targeting_a_user_field_still_writes_under_fields():
    # A note whose target is a user-defined text field (not notes/bigNotes)
    # routes through the fields map, symmetric with _existing_text's read.
    op = ReplaceNote(target="pragafilm.popis.materialu", text="popis")
    payload = build_put_payload(current=_clip(), ops=[op])
    assert payload["fields"]["pragafilm.popis.materialu"] == "popis"
    assert "notes" not in payload


def test_append_note_is_idempotent_when_already_appended_with_separator():
    # Re-draining an AppendNote (crash recovery, or a retry after a PUT the
    # server applied but whose response was lost) must NOT append again. The
    # live clip already ends with the appended segment → emit nothing.
    # See ADR 0091.
    op = AppendNote(target="notes", text="summary")
    payload = build_put_payload(current=_clip(notes="old\n\n---\n\nsummary"), ops=[op])
    assert payload == {}


def test_append_note_is_idempotent_when_text_is_the_whole_note():
    # First-ever append landed (no separator, note == the appended text).
    op = AppendNote(target="bigNotes", text="only")
    payload = build_put_payload(current=_clip(big_notes="only"), ops=[op])
    assert payload == {}


def test_append_note_still_appends_when_text_differs():
    # Guard: a genuinely new append is not suppressed by the idempotency check.
    op = AppendNote(target="notes", text="second")
    payload = build_put_payload(current=_clip(notes="old\n\n---\n\nfirst"), ops=[op])
    assert payload["notes"] == "old\n\n---\n\nfirst\n\n---\n\nsecond"


def test_multiple_ops_combined_in_one_payload():
    op_m = AddMarkers(markers=[Marker(name="m", in_=Timecode(secs=2.0, fps=25.0), out=None)])
    op_f = SetField(identifier="pragafilm.barva", value="true")
    op_n = AppendNote(target="notes", text="x")
    payload = build_put_payload(current=_clip(), ops=[op_m, op_f, op_n])
    assert "markers" in payload
    assert payload["fields"]["pragafilm.barva"] == "true"
    assert payload["notes"] == "x"
    assert "notes" not in payload["fields"]
