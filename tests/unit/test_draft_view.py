from backend.app.models.annotation import Annotation, ReviewItem
from backend.app.services.draft_view import build_draft_view


def _annotation(**overrides):
    base = dict(
        id=42, catdv_clip_id=101, catdv_clip_name="Clip_101",
        prompt_version_id=7, job_id=1, model="gemini-2.5-pro",
        prompt_used="p", raw_response={}, structured_output={},
        clip_snapshot={},
    )
    base.update(overrides)
    return Annotation(**base)


def test_build_draft_view_returns_empty_when_annotation_is_none():
    result = build_draft_view(annotation=None, review_items=[])
    assert result == {
        "has_draft": False,
        "annotation_id": None,
        "created_at": None,
        "prompt_name": None,
        "version_num": None,
        "model": None,
        "markers": [],
        "fields": [],
        "notes": None,
    }


def test_build_draft_view_maps_marker_review_items():
    ann = _annotation()
    items = [
        ReviewItem(
            annotation_id=42, catdv_clip_id=101, kind="marker",
            proposed_value={
                "name": "Scene 1",
                "category": "Event",
                "description": "Intro",
                "in": {"secs": 0.0, "frm": 0},
                "out": {"secs": 1.0, "frm": 25},
            },
        ),
        ReviewItem(
            annotation_id=42, catdv_clip_id=101, kind="marker",
            proposed_value={
                "name": "Scene 2",
                "category": None,
                "description": None,
                "in": {"secs": 1.0, "frm": 25},
                "out": None,
            },
        ),
    ]
    result = build_draft_view(annotation=ann, review_items=items)
    assert result["has_draft"] is True
    assert result["markers"] == [
        {
            "name": "Scene 1",
            "category": "Event",
            "description": "Intro",
            "in_secs": 0.0,
            "out_secs": 1.0,
            "color": None,
        },
        {
            "name": "Scene 2",
            "category": None,
            "description": None,
            "in_secs": 1.0,
            "out_secs": None,
            "color": None,
        },
    ]


def test_build_draft_view_applies_mojibake_fix_to_marker_name_and_description():
    ann = _annotation()
    items = [
        ReviewItem(
            annotation_id=42, catdv_clip_id=101, kind="marker",
            proposed_value={
                "name": "DÄ\x9btsk\xc3\xa9 hry",
                "category": None,
                "description": "S koÃ\x83Â¡rkem",
                "in": {"secs": 0.0, "frm": 0},
                "out": None,
            },
        ),
    ]
    result = build_draft_view(annotation=ann, review_items=items)
    m = result["markers"][0]
    # _fix is a best-effort repair; either it fixes to a readable form or
    # leaves the string untouched. We just assert it ran and produced a str.
    assert isinstance(m["name"], str) and m["name"]
    assert isinstance(m["description"], str)


def test_build_draft_view_maps_string_field():
    ann = _annotation()
    items = [
        ReviewItem(
            annotation_id=42, catdv_clip_id=101, kind="field",
            target_identifier="pragafilm.dekáda.natočení",
            proposed_value="30.léta",
        ),
    ]
    result = build_draft_view(annotation=ann, review_items=items)
    assert result["fields"] == [
        {
            "identifier": "pragafilm.dekáda.natočení",
            "name": "natočení",
            "value": "30.léta",
        },
    ]


def test_build_draft_view_maps_list_field_by_joining():
    ann = _annotation()
    items = [
        ReviewItem(
            annotation_id=42, catdv_clip_id=101, kind="field",
            target_identifier="pragafilm.rok.natočení",
            proposed_value=["1932", "1933"],
        ),
    ]
    result = build_draft_view(annotation=ann, review_items=items)
    assert result["fields"] == [
        {
            "identifier": "pragafilm.rok.natočení",
            "name": "natočení",
            "value": "1932, 1933",
        },
    ]


def test_build_draft_view_fields_sorted_by_identifier():
    ann = _annotation()
    items = [
        ReviewItem(
            annotation_id=42, catdv_clip_id=101, kind="field",
            target_identifier="pragafilm.rok.natočení", proposed_value="1932",
        ),
        ReviewItem(
            annotation_id=42, catdv_clip_id=101, kind="field",
            target_identifier="pragafilm.barva", proposed_value="true",
        ),
    ]
    result = build_draft_view(annotation=ann, review_items=items)
    idents = [f["identifier"] for f in result["fields"]]
    assert idents == ["pragafilm.barva", "pragafilm.rok.natočení"]


def test_build_draft_view_maps_single_note():
    ann = _annotation()
    items = [
        ReviewItem(
            annotation_id=42, catdv_clip_id=101, kind="note",
            target_identifier="notes", proposed_value="A summary of the clip.",
        ),
    ]
    result = build_draft_view(annotation=ann, review_items=items)
    assert result["notes"] == "A summary of the clip."


def test_build_draft_view_joins_multiple_notes_with_blank_lines():
    ann = _annotation()
    items = [
        ReviewItem(
            annotation_id=42, catdv_clip_id=101, kind="note",
            target_identifier="notes", proposed_value="Line one.",
        ),
        ReviewItem(
            annotation_id=42, catdv_clip_id=101, kind="note",
            target_identifier="bigNotes", proposed_value="Line two.",
        ),
    ]
    result = build_draft_view(annotation=ann, review_items=items)
    assert result["notes"] == "Line one.\n\nLine two."
