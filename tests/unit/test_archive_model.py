from datetime import UTC, datetime

import pytest

from backend.app.archive.model import (
    AddMarkers,
    AppendNote,
    CanonicalClip,
    ChangeSet,
    ClipKey,
    FieldValue,
    Marker,
    MediaRef,
    ReplaceNote,
    SetField,
    Timecode,
)


def test_clip_key_is_tuple_like():
    key: ClipKey = ("catdv", "12345")
    assert key[0] == "catdv"
    assert key[1] == "12345"


def test_timecode_holds_secs_and_fps():
    tc = Timecode(secs=4.0, fps=25.0)
    assert tc.secs == 4.0
    assert tc.fps == 25.0
    assert tc.frm is None
    assert tc.txt is None


def test_marker_requires_in_allows_optional_out():
    m = Marker(name="scene", in_=Timecode(secs=0.0, fps=25.0), out=None)
    assert m.out is None
    assert m.description is None


def test_canonical_clip_is_frozen():
    clip = CanonicalClip(
        key=("catdv", "1"),
        name="x",
        duration_secs=10.0,
        fps=25.0,
        markers=[],
        fields={},
        notes={},
        media=MediaRef(
            mime_type="video/quicktime",
            size_bytes=None,
            cached_path=None,
            upstream_handle="1",
        ),
        provider_data={"ID": 1},
        fetched_at=datetime.now(UTC),
    )
    with pytest.raises(Exception):
        clip.name = "y"  # type: ignore[misc]


def test_change_ops_are_distinct_dataclasses():
    add = AddMarkers(markers=[Marker(name="s", in_=Timecode(secs=0.0, fps=25.0), out=None)])
    sf = SetField(identifier="pragafilm.dekáda.natočení", value="30.léta")
    an = AppendNote(target="notes", text="extra")
    rn = ReplaceNote(target="bigNotes", text="replaced")
    assert add != sf
    assert isinstance(add, AddMarkers)
    assert isinstance(sf, SetField)
    assert isinstance(an, AppendNote)
    assert isinstance(rn, ReplaceNote)


def test_change_set_groups_ops_for_one_clip():
    cs = ChangeSet(
        clip_key=("catdv", "1"),
        ops=[SetField(identifier="a", value=1), SetField(identifier="b", value=2)],
        expected_etag=None,
    )
    assert len(cs.ops) == 2
    assert cs.clip_key == ("catdv", "1")


def test_field_value_defaults_to_single_value():
    fv = FieldValue(identifier="x", value=1)
    assert fv.is_multi is False
