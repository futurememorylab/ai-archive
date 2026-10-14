"""Guard: the app writes ONLY what it owns (#123, ADR 0127).

THE RULE
--------
The app owns exactly two things in the archive:

  1. markers whose ``category == AI_MARKER_CATEGORY`` ("AI"), and
  2. the fields listed in ``AI_OWNED_FIELDS`` (currently only
     ``pragafilm.popis.ai``).

Everything else is human and is NEVER modified, deleted or overwritten —
human markers, legacy app markers written before #123 (they carry Gemini
categories, so ``category != "AI"``), ``pragafilm.popis.materialu``, the
decade/year fields, and the top-level ``notes`` / ``bigNotes``.

The rule is enforced in ONE place per provider — the payload builder — not in
each caller. For CatDV that is ``build_put_payload``: a ChangeOp that would
write a non-AI marker or a non-owned field/note target raises
``FatalProviderError`` and is never silently written. This test fails if that
enforcement is removed or weakened, so the ownership guarantee can never
regress to a silent write.
"""

import pytest

from backend.app.archive.errors import FatalProviderError
from backend.app.archive.model import (
    AI_MARKER_CATEGORY,
    AI_OWNED_FIELDS,
    AppendNote,
    Marker,
    ReplaceAIMarkers,
    ReplaceNote,
    SetField,
    Timecode,
)
from backend.app.archive.providers.catdv.payload import build_put_payload


def _clip(markers=None, fields=None):
    return {"ID": 1, "name": "c", "fps": 25.0, "markers": markers or [], "fields": fields or {}}


def test_owned_constants_are_what_the_app_claims():
    assert AI_MARKER_CATEGORY == "AI"
    assert AI_OWNED_FIELDS == ("pragafilm.popis.ai",)


@pytest.mark.parametrize(
    "op",
    [
        SetField(identifier="pragafilm.popis.materialu", value="x"),
        SetField(identifier="pragafilm.dekáda.natočení", value="30.léta"),
        ReplaceNote(target="notes", text="x"),
        ReplaceNote(target="bigNotes", text="x"),
        AppendNote(target="pragafilm.rok.natočení", text="1932"),
    ],
)
def test_any_non_owned_field_or_note_target_raises(op):
    with pytest.raises(FatalProviderError):
        build_put_payload(current=_clip(), ops=[op])


def test_the_app_never_touches_a_non_ai_marker():
    """Every non-AI marker on the live clip is returned byte-identical, no
    matter what the app PUTs next to it."""
    human = {"name": "H", "category": "Human", "in": {"frm": 0, "fmt": 25.0, "secs": 0.0}}
    legacy = {"name": "L", "category": "Interview", "in": {"frm": 25, "fmt": 25.0, "secs": 1.0}}
    op = ReplaceAIMarkers(
        desired=(Marker(name="ai", in_=Timecode(secs=4.0, fps=25.0), out=None),),
    )
    payload = build_put_payload(current=_clip(markers=[human, legacy]), ops=[op])
    kept = [m for m in payload["markers"] if m["category"] != AI_MARKER_CATEGORY]
    assert kept == [human, legacy]


def test_the_only_allowed_field_is_written_through():
    op = ReplaceNote(target=AI_OWNED_FIELDS[0], text="shrnutí")
    payload = build_put_payload(current=_clip(), ops=[op])
    assert payload["fields"] == {AI_OWNED_FIELDS[0]: "shrnutí"}
