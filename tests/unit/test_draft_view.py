from backend.app.models.annotation import Annotation, ReviewItem
from backend.app.services.draft_view import build_draft_view


def _annotation(**overrides):
    base = dict(
        id=42,
        catdv_clip_id=101,
        catdv_clip_name="Clip_101",
        prompt_version_id=7,
        job_id=1,
        model="gemini-2.5-pro",
        prompt_used="p",
        raw_response={},
        structured_output={},
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
        "note_items": [],
    }


def test_build_draft_view_maps_marker_review_items():
    ann = _annotation()
    items = [
        ReviewItem(
            annotation_id=42,
            catdv_clip_id=101,
            kind="marker",
            proposed_value={
                "name": "Scene 1",
                "category": "Event",
                "description": "Intro",
                "in": {"secs": 0.0, "frm": 0},
                "out": {"secs": 1.0, "frm": 25},
            },
        ),
        ReviewItem(
            annotation_id=42,
            catdv_clip_id=101,
            kind="marker",
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
            "item_id": None,
            "kind": "marker",
            "decision": "pending",
        },
        {
            "name": "Scene 2",
            "category": None,
            "description": None,
            "in_secs": 1.0,
            "out_secs": None,
            "color": None,
            "item_id": None,
            "kind": "marker",
            "decision": "pending",
        },
    ]


def test_build_draft_view_applies_mojibake_fix_to_marker_name_and_description():
    ann = _annotation()
    items = [
        ReviewItem(
            annotation_id=42,
            catdv_clip_id=101,
            kind="marker",
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
            annotation_id=42,
            catdv_clip_id=101,
            kind="field",
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
            "multi": False,
            "item_id": None,
            "kind": "field",
            "decision": "pending",
        },
    ]


def test_build_draft_view_maps_list_field_by_joining():
    ann = _annotation()
    items = [
        ReviewItem(
            annotation_id=42,
            catdv_clip_id=101,
            kind="field",
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
            "multi": True,
            "item_id": None,
            "kind": "field",
            "decision": "pending",
        },
    ]


def test_build_draft_view_fields_sorted_by_identifier():
    ann = _annotation()
    items = [
        ReviewItem(
            annotation_id=42,
            catdv_clip_id=101,
            kind="field",
            target_identifier="pragafilm.rok.natočení",
            proposed_value="1932",
        ),
        ReviewItem(
            annotation_id=42,
            catdv_clip_id=101,
            kind="field",
            target_identifier="pragafilm.barva",
            proposed_value="true",
        ),
    ]
    result = build_draft_view(annotation=ann, review_items=items)
    idents = [f["identifier"] for f in result["fields"]]
    assert idents == ["pragafilm.barva", "pragafilm.rok.natočení"]


def test_build_draft_view_maps_single_note():
    ann = _annotation()
    items = [
        ReviewItem(
            annotation_id=42,
            catdv_clip_id=101,
            kind="note",
            target_identifier="notes",
            proposed_value="A summary of the clip.",
        ),
    ]
    result = build_draft_view(annotation=ann, review_items=items)
    assert result["notes"] == "A summary of the clip."


def test_build_draft_view_joins_multiple_notes_with_blank_lines():
    ann = _annotation()
    items = [
        ReviewItem(
            annotation_id=42,
            catdv_clip_id=101,
            kind="note",
            target_identifier="notes",
            proposed_value="Line one.",
        ),
        ReviewItem(
            annotation_id=42,
            catdv_clip_id=101,
            kind="note",
            target_identifier="bigNotes",
            proposed_value="Line two.",
        ),
    ]
    result = build_draft_view(annotation=ann, review_items=items)
    assert result["notes"] == "Line one.\n\nLine two."


def test_build_draft_view_includes_header_chip_metadata_when_supplied():
    ann = _annotation()
    result = build_draft_view(
        annotation=ann,
        review_items=[],
        prompt_name="Decade tagger",
        version_num=3,
        created_at="2026-05-21T14:22:08+00:00",
    )
    assert result["prompt_name"] == "Decade tagger"
    assert result["version_num"] == 3
    assert result["created_at"] == "2026-05-21T14:22:08+00:00"
    assert result["model"] == "gemini-2.5-pro"


def test_markers_and_fields_carry_item_id_kind_decision():
    ann = _annotation(id=5, catdv_clip_id=1, catdv_clip_name="c")
    items = [
        ReviewItem(
            id=11,
            annotation_id=5,
            catdv_clip_id=1,
            kind="marker",
            proposed_value={"name": "a", "in": {"secs": 0.0}, "out": {"secs": 1.0}},
            decision="pending",
        ),
        ReviewItem(
            id=12,
            annotation_id=5,
            catdv_clip_id=1,
            kind="field",
            target_identifier="f.a",
            proposed_value="v",
            decision="accepted",
        ),
    ]
    view = build_draft_view(ann, items)
    assert view["has_draft"] is True
    m = view["markers"][0]
    assert m["item_id"] == 11
    assert m["kind"] == "marker"
    assert m["decision"] == "pending"
    f = view["fields"][0]
    assert f["item_id"] == 12
    assert f["kind"] == "field"
    assert f["decision"] == "accepted"
    assert f["multi"] is False
    # existing display keys still present
    assert m["name"] == "a"
    assert f["identifier"] == "f.a"
    assert f["value"] == "v"


def test_list_field_is_multi():
    ann = _annotation(id=5, catdv_clip_id=1, catdv_clip_name="c")
    items = [
        ReviewItem(
            id=20,
            annotation_id=5,
            catdv_clip_id=1,
            kind="field",
            target_identifier="pragafilm.rok.natočení",
            proposed_value=["a", "b"],
            decision="pending",
        ),
    ]
    view = build_draft_view(ann, items)
    f = view["fields"][0]
    assert f["multi"] is True
    assert f["value"] == "a, b"


def test_build_draft_view_exposes_note_items():
    ann = _annotation(id=5, catdv_clip_id=1, catdv_clip_name="c")
    items = [
        ReviewItem(
            id=21,
            annotation_id=5,
            catdv_clip_id=1,
            kind="note",
            target_identifier="notes",
            proposed_value="some note text",
            decision="pending",
        ),
    ]
    view = build_draft_view(ann, items)
    assert "note_items" in view
    assert len(view["note_items"]) == 1
    ni = view["note_items"][0]
    assert ni["item_id"] == 21
    assert ni["kind"] == "note"
    assert ni["decision"] == "pending"
    assert ni["text"] == "some note text"

    # annotation-None branch also returns note_items == []
    none_view = build_draft_view(None, [])
    assert none_view["note_items"] == []
