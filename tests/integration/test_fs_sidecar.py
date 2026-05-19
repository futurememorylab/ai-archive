"""Tests for backend.app.archive.providers.fs.sidecar."""

from __future__ import annotations

from backend.app.archive.model import FieldValue, Marker, Timecode
from backend.app.archive.providers.fs.sidecar import (
    dumps_sidecar,
    loads_sidecar,
    parse_sidecar,
    render_sidecar,
)


def test_parse_empty_sidecar_returns_defaults():
    markers, fields, notes, provider_data = parse_sidecar(None, default_fps=25.0)
    assert markers == ()
    assert fields == {}
    assert notes == {}
    assert provider_data == {}


def test_parse_extracts_markers_and_fields_and_notes():
    raw = {
        "markers": [
            {
                "name": "m1",
                "in": {"secs": 1.0, "frm": 25, "fps": 25.0},
                "out": {"secs": 2.0, "frm": 50, "fps": 25.0},
                "description": "d",
                "category": "c",
                "color": "#fff",
            }
        ],
        "fields": {
            "f1": {"value": "v", "is_multi": False},
            "f2": {"value": [1, 2], "is_multi": True},
        },
        "notes": {"notes": "hello"},
    }
    markers, fields, notes, provider_data = parse_sidecar(raw, default_fps=25.0)
    assert len(markers) == 1
    assert markers[0].name == "m1"
    assert markers[0].in_.secs == 1.0
    assert markers[0].out is not None
    assert markers[0].out.frm == 50
    assert fields["f1"].value == "v"
    assert fields["f2"].is_multi is True
    assert notes["notes"] == "hello"
    assert provider_data == {}


def test_unknown_top_keys_are_preserved_in_provider_data():
    raw = {"markers": [], "fields": {}, "notes": {}, "vendor_x": {"k": 1}}
    _, _, _, provider_data = parse_sidecar(raw, default_fps=25.0)
    assert provider_data["vendor_x"] == {"k": 1}


def test_round_trip_preserves_all_canonical_fields():
    fields = {
        "f1": FieldValue(identifier="f1", value="v", is_multi=False),
        "f2": FieldValue(identifier="f2", value=["a", "b"], is_multi=True),
    }
    markers = (
        Marker(
            name="m",
            in_=Timecode(secs=1.5, fps=25.0, frm=37),
            out=Timecode(secs=3.0, fps=25.0, frm=75),
            description="d",
            category="c",
            color="#0f0",
        ),
    )
    notes = {"notes": "n", "bigNotes": "long"}
    provider_data = {"vendor_x": {"k": 9}}

    rendered = render_sidecar(
        markers=markers, fields=fields, notes=notes, provider_data=provider_data
    )
    blob = dumps_sidecar(rendered)
    parsed = loads_sidecar(blob)
    m2, f2, n2, pd2 = parse_sidecar(parsed, default_fps=25.0)

    assert n2 == notes
    assert pd2["vendor_x"] == {"k": 9}
    assert len(m2) == 1 and m2[0].description == "d"
    assert f2["f1"].value == "v" and f2["f2"].is_multi is True


def test_marker_in_dict_defaults_frm_from_secs_and_fps():
    m = Marker(
        name="m",
        in_=Timecode(secs=2.0, fps=25.0, frm=None),
        out=None,
    )
    doc = render_sidecar(
        markers=(m,), fields={}, notes={}, provider_data={}
    )
    assert doc["markers"][0]["in"]["frm"] == 50
