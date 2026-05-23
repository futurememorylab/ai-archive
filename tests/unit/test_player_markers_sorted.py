"""Markers in clip_detail() must be sorted ascending by in_secs.

The frontend player's prev/next-marker navigation depends on this ordering;
sorting in the view-model keeps the template + JS simple.
"""

from datetime import UTC, datetime

from backend.app.archive.model import CanonicalClip, Marker, MediaRef, Timecode
from backend.app.ui.view_models import clip_detail


def _clip_with_markers(*in_secs: float) -> CanonicalClip:
    return CanonicalClip(
        key=("catdv", "12041"),
        name="Test_Clip",
        duration_secs=600.0,
        fps=25.0,
        markers=tuple(
            Marker(
                name=f"m@{s}",
                in_=Timecode(secs=s, fps=25.0),
                out=Timecode(secs=s + 1.0, fps=25.0),
            )
            for s in in_secs
        ),
        fields={},
        notes={},
        media=MediaRef(
            mime_type="video/quicktime",
            size_bytes=None,
            cached_path=None,
            upstream_handle="12041",
        ),
        provider_data={"ID": 12041, "name": "Test_Clip"},
        fetched_at=datetime.now(UTC),
    )


def test_clip_detail_markers_sorted_ascending_by_in_secs():
    clip = _clip_with_markers(120.0, 30.0, 250.5, 90.0)
    d = clip_detail(clip)
    in_secs = [m["in_secs"] for m in d["clip"]["markers"]]
    assert in_secs == [30.0, 90.0, 120.0, 250.5]


def test_clip_detail_markers_sort_is_stable_for_equal_in_secs():
    clip = _clip_with_markers(50.0, 50.0, 50.0)
    d = clip_detail(clip)
    names = [m["name"] for m in d["clip"]["markers"]]
    # All three have in_secs == 50.0; original insertion order preserved.
    assert names == ["m@50.0", "m@50.0", "m@50.0"]


def test_clip_detail_handles_zero_markers():
    clip = _clip_with_markers()
    d = clip_detail(clip)
    assert d["clip"]["markers"] == []
