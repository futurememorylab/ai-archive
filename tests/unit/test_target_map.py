import pytest

from backend.app.models.annotation import ReviewItem
from backend.app.models.template import TargetMap
from backend.app.services.target_map import expand


def _tm(d):
    return TargetMap.model_validate(d)


def test_expand_markers_produces_one_review_item_per_scene():
    structured = {
        "scenes": [
            {"name": "scene-a", "in": {"secs": 0.0}, "out": {"secs": 5.0}},
            {"name": "scene-b", "in": {"secs": 5.0}, "out": {"secs": 10.0}},
        ]
    }
    tm = _tm({"scenes": {"kind": "markers"}})
    items = expand(structured, tm, annotation_id=1, catdv_clip_id=42)
    assert len(items) == 2
    assert all(it.kind == "marker" for it in items)
    assert items[0].proposed_value["name"] == "scene-a"


def test_expand_field_value():
    tm = _tm({"decade": {"kind": "field", "identifier": "pragafilm.dekáda.natočení"}})
    items = expand({"decade": "30.léta"}, tm, annotation_id=1, catdv_clip_id=42)
    assert len(items) == 1
    assert items[0].kind == "field"
    assert items[0].target_identifier == "pragafilm.dekáda.natočení"
    assert items[0].proposed_value == "30.léta"


def test_expand_note():
    tm = _tm({"summary": {"kind": "note", "target": "pragafilm.popis.materialu", "mode": "append"}})
    items = expand({"summary": "Rodinný portrét"}, tm, annotation_id=1, catdv_clip_id=42)
    assert len(items) == 1
    assert items[0].kind == "note"
    assert items[0].target_identifier == "pragafilm.popis.materialu"
    assert items[0].proposed_value == "Rodinný portrét"


def test_expand_skips_missing_schema_keys():
    tm = _tm(
        {
            "scenes": {"kind": "markers"},
            "decade": {"kind": "field", "identifier": "pragafilm.dekáda.natočení"},
        }
    )
    items = expand({"scenes": []}, tm, annotation_id=1, catdv_clip_id=42)
    assert items == []


def test_expand_handles_array_field():
    tm = _tm({"years": {"kind": "field", "identifier": "pragafilm.rok.natočení"}})
    items = expand({"years": ["1933", "1934"]}, tm, annotation_id=1, catdv_clip_id=42)
    assert items[0].proposed_value == ["1933", "1934"]


def test_expand_unwraps_value_evidence_pattern():
    """When schema returns {value, evidence_secs}, store as-is for UI to render."""
    tm = _tm({"decade": {"kind": "field", "identifier": "pragafilm.dekáda.natočení"}})
    items = expand(
        {"decade": {"value": "30.léta", "evidence_secs": [4.0, 12.0]}},
        tm,
        annotation_id=1,
        catdv_clip_id=42,
    )
    assert items[0].proposed_value == {"value": "30.léta", "evidence_secs": [4.0, 12.0]}
