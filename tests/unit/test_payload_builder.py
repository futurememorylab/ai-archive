import pytest

from backend.app.models.annotation import ReviewItem
from backend.app.models.template import TargetMap
from backend.app.services.payload_builder import build_put_payload


SAMPLE_CLIP = {
    "ID": 42,
    "name": "x",
    "markers": [
        {"name": "existing-a", "in": {"frm": 0, "secs": 0.0}, "out": {"frm": 25, "secs": 1.0}},
    ],
    "fields": {
        "pragafilm.dekáda.natočení": "20.léta",
        "pragafilm.popis.materialu": "Existing notes.",
        "pragafilm.barva": "false",
    },
    "notes": "old notes",
}


def _tm(d):
    return TargetMap.model_validate(d)


def test_no_accepted_items_returns_empty_payload():
    payload = build_put_payload(
        current=SAMPLE_CLIP, accepted_items=[], target_map=_tm({}),
    )
    assert payload == {}


def test_accepted_marker_appends_to_existing():
    new_marker = {"name": "scene-b", "in": {"frm": 100, "secs": 4.0},
                  "out": {"frm": 200, "secs": 8.0}}
    item = ReviewItem(annotation_id=1, catdv_clip_id=42, kind="marker",
                     proposed_value=new_marker, decision="accepted")
    payload = build_put_payload(
        current=SAMPLE_CLIP, accepted_items=[item],
        target_map=_tm({"scenes": {"kind": "markers"}}),
    )
    assert payload["markers"][0]["name"] == "existing-a"
    assert payload["markers"][1]["name"] == "scene-b"


def test_dedupes_marker_on_overlapping_in_frm():
    new_marker = {"name": "duplicate", "in": {"frm": 0, "secs": 0.0},
                  "out": {"frm": 25, "secs": 1.0}}
    item = ReviewItem(annotation_id=1, catdv_clip_id=42, kind="marker",
                     proposed_value=new_marker, decision="accepted")
    payload = build_put_payload(
        current=SAMPLE_CLIP, accepted_items=[item],
        target_map=_tm({"scenes": {"kind": "markers"}}),
    )
    assert len(payload["markers"]) == 1
    assert payload["markers"][0]["name"] == "existing-a"


def test_edited_value_used_over_proposed():
    new_marker = {"name": "scene-x", "in": {"frm": 100, "secs": 4.0}}
    edited = {"name": "scene-x (edited)", "in": {"frm": 110, "secs": 4.4}}
    item = ReviewItem(annotation_id=1, catdv_clip_id=42, kind="marker",
                     proposed_value=new_marker, edited_value=edited, decision="accepted")
    payload = build_put_payload(
        current=SAMPLE_CLIP, accepted_items=[item],
        target_map=_tm({"scenes": {"kind": "markers"}}),
    )
    assert payload["markers"][-1]["name"] == "scene-x (edited)"
    assert payload["markers"][-1]["in"]["frm"] == 110


def test_field_set_replaces_value():
    item = ReviewItem(annotation_id=1, catdv_clip_id=42, kind="field",
                     target_identifier="pragafilm.dekáda.natočení",
                     proposed_value="30.léta", decision="accepted")
    payload = build_put_payload(
        current=SAMPLE_CLIP, accepted_items=[item],
        target_map=_tm({"decade": {"kind": "field", "identifier": "pragafilm.dekáda.natočení"}}),
    )
    assert payload["fields"]["pragafilm.dekáda.natočení"] == "30.léta"
    assert "pragafilm.barva" not in payload.get("fields", {})


def test_field_unwraps_value_evidence_pattern():
    item = ReviewItem(annotation_id=1, catdv_clip_id=42, kind="field",
                     target_identifier="pragafilm.dekáda.natočení",
                     proposed_value={"value": "30.léta", "evidence_secs": [4.0]},
                     decision="accepted")
    payload = build_put_payload(
        current=SAMPLE_CLIP, accepted_items=[item],
        target_map=_tm({"decade": {"kind": "field", "identifier": "pragafilm.dekáda.natočení"}}),
    )
    assert payload["fields"]["pragafilm.dekáda.natočení"] == "30.léta"


def test_note_append_mode_joins_with_separator():
    item = ReviewItem(annotation_id=1, catdv_clip_id=42, kind="note",
                     target_identifier="pragafilm.popis.materialu",
                     proposed_value="New AI annotation.", decision="accepted")
    payload = build_put_payload(
        current=SAMPLE_CLIP, accepted_items=[item],
        target_map=_tm({
            "summary": {"kind": "note", "target": "pragafilm.popis.materialu", "mode": "append"}
        }),
    )
    assert payload["fields"]["pragafilm.popis.materialu"] == \
        "Existing notes.\n\n---\n\nNew AI annotation."


def test_note_replace_mode_overwrites():
    item = ReviewItem(annotation_id=1, catdv_clip_id=42, kind="note",
                     target_identifier="pragafilm.popis.materialu",
                     proposed_value="Fresh.", decision="accepted")
    payload = build_put_payload(
        current=SAMPLE_CLIP, accepted_items=[item],
        target_map=_tm({
            "summary": {"kind": "note", "target": "pragafilm.popis.materialu", "mode": "replace"}
        }),
    )
    assert payload["fields"]["pragafilm.popis.materialu"] == "Fresh."


def test_rejected_items_are_ignored():
    rejected = ReviewItem(annotation_id=1, catdv_clip_id=42, kind="field",
                         target_identifier="pragafilm.dekáda.natočení",
                         proposed_value="30.léta", decision="rejected")
    payload = build_put_payload(
        current=SAMPLE_CLIP, accepted_items=[rejected],
        target_map=_tm({"decade": {"kind": "field", "identifier": "pragafilm.dekáda.natočení"}}),
    )
    assert payload == {}


def test_payload_omits_unchanged_arrays():
    field_item = ReviewItem(annotation_id=1, catdv_clip_id=42, kind="field",
                           target_identifier="pragafilm.dekáda.natočení",
                           proposed_value="30.léta", decision="accepted")
    payload = build_put_payload(
        current=SAMPLE_CLIP, accepted_items=[field_item],
        target_map=_tm({"decade": {"kind": "field", "identifier": "pragafilm.dekáda.natočení"}}),
    )
    assert "markers" not in payload
