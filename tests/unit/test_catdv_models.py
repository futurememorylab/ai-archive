import json
from pathlib import Path

from backend.app.models.catdv import Clip, Envelope, Marker, TimecodeQuad


FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "clip_sample.json"


def test_clip_parses_fixture():
    raw = json.loads(FIXTURE.read_text())
    clip = Clip.model_validate(raw)
    assert clip.id == 12345
    assert clip.name == "Sample_Clip_01"
    assert clip.fps == 25
    assert clip.duration.secs == 60.0
    assert len(clip.markers) == 1
    assert clip.markers[0].name == "Scene A"
    assert clip.fields["pragafilm.dekáda.natočení"] == "30.léta"


def test_clip_round_trips_through_json():
    raw = json.loads(FIXTURE.read_text())
    clip = Clip.model_validate(raw)
    again = clip.model_dump(mode="json", by_alias=True, exclude_none=False)
    assert again["ID"] == 12345
    assert again["markers"][0]["in"]["frm"] == 250


def test_marker_optional_out():
    m = Marker.model_validate(
        {
            "name": "Point",
            "in": {"frm": 100, "fmt": 25, "secs": 4.0, "txt": "00:00:04:00"},
        }
    )
    assert m.out is None


def test_envelope_ok():
    env = Envelope.model_validate({"status": "OK", "errorMessage": None, "data": {"a": 1}})
    assert env.is_ok
    assert env.data == {"a": 1}


def test_envelope_auth():
    env = Envelope.model_validate({"status": "AUTH", "errorMessage": None, "data": None})
    assert not env.is_ok
    assert env.requires_reauth
