import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from backend.app.archive.model import Marker, Timecode
from backend.app.archive.providers.catdv.mapping import (
    from_catdv_clip,
    marker_to_catdv,
)

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "catdv_clip_sample.json"


@pytest.fixture
def raw() -> dict:
    return json.loads(FIXTURE.read_text())


def test_from_catdv_clip_sets_key(raw):
    clip = from_catdv_clip(raw, fetched_at=datetime.now(timezone.utc))
    assert clip.key == ("catdv", "12345")
    assert clip.name == "Abramcukova_Anna_09"


def test_from_catdv_clip_extracts_fps_and_duration(raw):
    clip = from_catdv_clip(raw, fetched_at=datetime.now(timezone.utc))
    assert clip.fps == 25.0
    assert clip.duration_secs == 330.0


def test_from_catdv_clip_extracts_markers(raw):
    clip = from_catdv_clip(raw, fetched_at=datetime.now(timezone.utc))
    assert len(clip.markers) == 1
    m = clip.markers[0]
    assert m.name == "Anna na zahradě"
    assert m.in_.secs == 60.0
    assert m.in_.fps == 25.0
    assert m.in_.frm == 1500
    assert m.out is not None and m.out.secs == 70.0


def test_from_catdv_clip_preserves_provider_data_verbatim(raw):
    clip = from_catdv_clip(raw, fetched_at=datetime.now(timezone.utc))
    assert clip.provider_data == raw  # exact round-trip pointer


def test_from_catdv_clip_extracts_notes(raw):
    clip = from_catdv_clip(raw, fetched_at=datetime.now(timezone.utc))
    assert clip.notes["notes"] == "Czech home movie, 9.5mm"
    assert clip.notes["bigNotes"] == "Longer description here."


def test_from_catdv_clip_extracts_pragafilm_fields(raw):
    clip = from_catdv_clip(raw, fetched_at=datetime.now(timezone.utc))
    assert "pragafilm.dekáda.natočení" in clip.fields
    fv = clip.fields["pragafilm.dekáda.natočení"]
    assert fv.value == "30.léta"
    assert fv.is_multi is False
    fv_years = clip.fields["pragafilm.rok.natočení"]
    assert fv_years.value == ["1932", "1933"]
    assert fv_years.is_multi is True


def test_marker_to_catdv_expands_partial_timecode():
    m = Marker(
        name="scene-1",
        in_=Timecode(secs=4.0, fps=25.0),
        out=Timecode(secs=6.0, fps=25.0),
    )
    raw = marker_to_catdv(m, fps=25.0)
    assert raw["name"] == "scene-1"
    assert raw["in"]["secs"] == 4.0
    assert raw["in"]["frm"] == 100
    assert raw["in"]["fmt"] == 25.0
    # secs_to_smpte (existing public API) uses 2-digit hours; preserves prior behavior.
    assert raw["in"]["txt"] == "00:00:04:00"
    assert raw["out"]["frm"] == 150
