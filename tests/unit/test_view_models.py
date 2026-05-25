from datetime import UTC, datetime

from backend.app.archive.model import (
    CanonicalClip,
    FieldValue,
    Marker,
    MediaRef,
    Timecode,
)
from backend.app.ui.view_models import clip_detail, clip_summary


def _canonical(
    *,
    clip_id: int = 12041,
    name: str = "Abramcukova_Anna_09",
    duration: float = 522.0,
    markers: tuple[Marker, ...] = (),
    fields: dict[str, FieldValue] | None = None,
    notes: dict[str, str] | None = None,
    provider_data: dict | None = None,
) -> CanonicalClip:
    return CanonicalClip(
        key=("catdv", str(clip_id)),
        name=name,
        duration_secs=duration,
        fps=25.0,
        markers=markers,
        fields=fields or {},
        notes=notes or {},
        media=MediaRef(
            mime_type="video/quicktime",
            size_bytes=None,
            cached_path=None,
            upstream_handle=str(clip_id),
        ),
        provider_data=provider_data or {"ID": clip_id, "name": name},
        fetched_at=datetime.now(UTC),
    )


def test_clip_summary_minimal():
    clip = _canonical()
    s = clip_summary(clip)
    assert s["id"] == 12041
    assert s["name"] == "Abramcukova_Anna_09"
    assert s["duration_secs"] == 522.0
    assert s["year"] is None
    assert s["decade"] is None
    assert s["marker_count"] == 0


def test_clip_summary_includes_thumb_and_list_keys():
    clip = _canonical(clip_id=42)
    row = clip_summary(clip)
    assert row["thumb_url"] == "/api/media/42/thumb"
    assert row["select_value"] == "catdv/42"
    assert row["row_href"] == "/clips/42"


def test_clip_summary_with_year_decade_and_markers():
    fields = {
        "pragafilm.rok.natočení": FieldValue(
            identifier="pragafilm.rok.natočení", value=["1932"], is_multi=True
        ),
        "pragafilm.dekáda.natočení": FieldValue(
            identifier="pragafilm.dekáda.natočení", value="30.léta"
        ),
    }
    markers = (
        Marker(
            name="m1",
            in_=Timecode(secs=10.0, fps=25.0),
            out=Timecode(secs=20.0, fps=25.0),
        ),
        Marker(
            name="m2",
            in_=Timecode(secs=30.0, fps=25.0),
            out=None,
        ),
    )
    s = clip_summary(_canonical(fields=fields, markers=markers))
    assert s["year"] == "1932"
    assert s["decade"] == "30.léta"
    assert s["marker_count"] == 2


def test_clip_detail_includes_markers_with_secs():
    markers = (
        Marker(
            name="Anna na zahradě",
            in_=Timecode(secs=83.48, fps=25.0),
            out=Timecode(secs=105.12, fps=25.0),
            description="Detailní záběr",
            category="scene",
            color="amber",
        ),
    )
    d = clip_detail(_canonical(markers=markers))
    assert d["clip"]["markers"][0]["name"] == "Anna na zahradě"
    assert d["clip"]["markers"][0]["in_secs"] == 83.48
    assert d["clip"]["markers"][0]["out_secs"] == 105.12
    assert d["clip"]["markers"][0]["description"] == "Detailní záběr"


def test_clip_detail_marker_without_out_has_none():
    markers = (Marker(name="point", in_=Timecode(secs=10.0, fps=25.0), out=None),)
    d = clip_detail(_canonical(markers=markers))
    assert d["clip"]["markers"][0]["out_secs"] is None


def test_clip_detail_pragafilm_fields_only():
    fields = {
        "pragafilm.dekáda.natočení": FieldValue(
            identifier="pragafilm.dekáda.natočení", value="30.léta"
        ),
        "builtin.something": FieldValue(identifier="builtin.something", value="x"),
    }
    d = clip_detail(_canonical(fields=fields))
    idents = [f["identifier"] for f in d["clip"]["fields"]]
    assert "pragafilm.dekáda.natočení" in idents
    assert "builtin.something" not in idents


def test_clip_detail_notes_and_format_from_provider_data():
    provider_data = {
        "ID": 12041,
        "name": "Abramcukova_Anna_09",
        "notes": "krátká poznámka",
        "bigNotes": "delší poznámka přes více řádků",
        "format": "QuickTime",
        "media": {"width": 720, "height": 576, "codec": "H.264"},
    }
    d = clip_detail(_canonical(provider_data=provider_data))
    assert d["clip"]["notes"] == "krátká poznámka"
    assert d["clip"]["big_notes"] == "delší poznámka přes více řádků"
    assert "720" in d["clip"]["format"] and "576" in d["clip"]["format"]


def test_clip_detail_media_url_uses_clip_id():
    d = clip_detail(_canonical(clip_id=7))
    assert d["clip"]["media_url"] == "/api/media/7"


def test_clip_detail_fixes_mojibake_in_markers_and_notes():
    """CatDV legacy data has marker names/descriptions stored as Latin-1
    re-encoded as UTF-8 (sometimes twice). The view-model fixer should
    clean them on read. Fixtures here are built from the real byte
    patterns observed on the live server (clip 888733).
    """
    # 'kočárkem' DOUBLE-mojibaked: UTF-8 of 'č' (c4 8d) → mangled once to
    # (c3 84 c2 8d) → mangled again to (c3 83 c2 84 c3 82 c2 8d). Same for 'á'.
    bad_desc = (
        b"matka s ko"
        b"\xc3\x83\xc2\x84\xc3\x82\xc2\x8d"  # č
        b"\xc3\x83\xc2\x83\xc3\x82\xc2\xa1"  # á
        b"rkem"
    ).decode("utf-8")
    # 'Žena pomáhá' SINGLE-mojibaked: Ž (c5 bd) → (c3 85 c2 bd), á → (c3 82 c2 a1).
    bad_name = (
        b"\xc3\x85\xc2\xbd"  # Ž
        b"ena pom"
        b"\xc3\x83\xc2\xa1h\xc3\x83\xc2\xa1"  # á…á
        b" batoleti"
    ).decode("utf-8")
    markers = (
        Marker(
            name=bad_name,
            in_=Timecode(secs=10.0, fps=25.0),
            out=None,
            description=bad_desc,
        ),
    )
    provider_data = {
        "ID": 1,
        "name": "x",
        # Single-mojibaked 'Žena s dítětem'
        "notes": (b"\xc3\x85\xc2\xbdena s d\xc3\x83\xc2\xadt\xc3\x84\xc2\x9btem").decode("utf-8"),
        "bigNotes": None,
    }
    d = clip_detail(_canonical(markers=markers, provider_data=provider_data))
    m0 = d["clip"]["markers"][0]
    assert "kočárkem" in m0["description"]
    assert "Žena pomáhá" in m0["name"]
    assert "Žena s dítětem" in d["clip"]["notes"]


def test_clip_detail_leaves_clean_text_unchanged():
    """Already-correct strings must pass through ftfy untouched."""
    markers = (
        Marker(
            name="Anna na zahradě",
            in_=Timecode(secs=10.0, fps=25.0),
            out=None,
            description="Test marker added via REST API to verify write access.",
        ),
    )
    d = clip_detail(_canonical(markers=markers))
    assert d["clip"]["markers"][0]["name"] == "Anna na zahradě"
    assert d["clip"]["markers"][0]["description"] == (
        "Test marker added via REST API to verify write access."
    )
