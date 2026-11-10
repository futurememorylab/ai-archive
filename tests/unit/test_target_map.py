from backend.app.models.prompt import TargetMap
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


def test_expand_markers_drops_in_secs_past_duration():
    """Gemini hallucinates timestamps past the clip end on long video.
    `clip_duration_secs` lets us drop those before they reach the UI."""
    structured = {
        "scenes": [
            {"name": "in-bounds", "in": {"secs": 10.0}, "out": {"secs": 20.0}},
            {"name": "starts-at-end", "in": {"secs": 100.0}, "out": {"secs": 110.0}},
            {"name": "way-past", "in": {"secs": 200.0}, "out": {"secs": 250.0}},
        ]
    }
    tm = _tm({"scenes": {"kind": "markers"}})
    items = expand(structured, tm, annotation_id=1, catdv_clip_id=42, clip_duration_secs=100.0)
    assert [it.proposed_value["name"] for it in items] == ["in-bounds"]


def test_expand_markers_clamps_out_secs_to_duration():
    structured = {
        "scenes": [
            {"name": "spills-over", "in": {"secs": 90.0}, "out": {"secs": 120.0}},
        ]
    }
    tm = _tm({"scenes": {"kind": "markers"}})
    items = expand(structured, tm, annotation_id=1, catdv_clip_id=42, clip_duration_secs=100.0)
    assert len(items) == 1
    assert items[0].proposed_value["out"]["secs"] == 100.0
    # Original `in` preserved
    assert items[0].proposed_value["in"]["secs"] == 90.0


def test_expand_markers_no_clamping_when_duration_not_supplied():
    structured = {"scenes": [{"name": "anything", "in": {"secs": 999.0}, "out": {"secs": 1000.0}}]}
    tm = _tm({"scenes": {"kind": "markers"}})
    items = expand(structured, tm, annotation_id=1, catdv_clip_id=42)
    assert len(items) == 1
    assert items[0].proposed_value["out"]["secs"] == 1000.0


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


# --- #173: scene timing sanity (Šikl feedback) ---------------------------


def _scenes(items):
    pvs = [it.proposed_value for it in items]
    return [(pv["name"], pv["in"]["secs"], pv["out"]["secs"]) for pv in pvs]


def test_expand_markers_repairs_out_before_in_to_next_scene_start():
    """BROŽ Vladimír 01, scene 4: 07:32 – 07:03 (out before in). The out
    is not trusted; the scene runs to where the next one starts."""
    structured = {
        "scenes": [
            {"name": "a", "in": {"secs": 400.0}, "out": {"secs": 452.0}},
            {"name": "b", "in": {"secs": 452.0}, "out": {"secs": 423.0}},
            {"name": "c", "in": {"secs": 470.0}, "out": {"secs": 500.0}},
        ]
    }
    tm = _tm({"scenes": {"kind": "markers"}})
    items = expand(structured, tm, annotation_id=1, catdv_clip_id=42, clip_duration_secs=500.0)
    assert _scenes(items) == [("a", 400.0, 452.0), ("b", 452.0, 470.0), ("c", 470.0, 500.0)]


def test_expand_markers_repairs_out_before_in_on_last_scene_to_clip_end():
    structured = {"scenes": [{"name": "last", "in": {"secs": 90.0}, "out": {"secs": 80.0}}]}
    tm = _tm({"scenes": {"kind": "markers"}})
    items = expand(structured, tm, annotation_id=1, catdv_clip_id=42, clip_duration_secs=100.0)
    assert _scenes(items) == [("last", 90.0, 100.0)]


def test_expand_markers_drops_backwards_out_when_nothing_to_repair_with():
    """Last scene, duration unknown: keep the in-point, drop the bad out."""
    structured = {"scenes": [{"name": "x", "in": {"secs": 90.0}, "out": {"secs": 80.0}}]}
    tm = _tm({"scenes": {"kind": "markers"}})
    items = expand(structured, tm, annotation_id=1, catdv_clip_id=42)
    assert len(items) == 1
    assert items[0].proposed_value["in"]["secs"] == 90.0
    assert "out" not in items[0].proposed_value


def test_expand_markers_trims_overlap_with_next_scene():
    structured = {
        "scenes": [
            {"name": "a", "in": {"secs": 0.0}, "out": {"secs": 12.0}},
            {"name": "b", "in": {"secs": 10.0}, "out": {"secs": 20.0}},
        ]
    }
    tm = _tm({"scenes": {"kind": "markers"}})
    items = expand(structured, tm, annotation_id=1, catdv_clip_id=42, clip_duration_secs=20.0)
    assert _scenes(items) == [("a", 0.0, 10.0), ("b", 10.0, 20.0)]


def test_expand_markers_sorted_by_in_point():
    structured = {
        "scenes": [
            {"name": "b", "in": {"secs": 10.0}, "out": {"secs": 20.0}},
            {"name": "a", "in": {"secs": 0.0}, "out": {"secs": 10.0}},
        ]
    }
    tm = _tm({"scenes": {"kind": "markers"}})
    items = expand(structured, tm, annotation_id=1, catdv_clip_id=42, clip_duration_secs=20.0)
    assert _scenes(items) == [("a", 0.0, 10.0), ("b", 10.0, 20.0)]


def test_expand_markers_clamps_negative_in_to_zero():
    structured = {"scenes": [{"name": "a", "in": {"secs": -1.5}, "out": {"secs": 4.0}}]}
    tm = _tm({"scenes": {"kind": "markers"}})
    items = expand(structured, tm, annotation_id=1, catdv_clip_id=42, clip_duration_secs=20.0)
    assert _scenes(items) == [("a", 0.0, 4.0)]


def test_expand_markers_keeps_other_marker_fields_when_repairing():
    structured = {
        "scenes": [
            {
                "name": "a",
                "description": "d",
                "category": "Město",
                "in": {"secs": 5.0},
                "out": {"secs": 1.0},
            }
        ]
    }
    tm = _tm({"scenes": {"kind": "markers"}})
    items = expand(structured, tm, annotation_id=1, catdv_clip_id=42, clip_duration_secs=20.0)
    pv = items[0].proposed_value
    assert (pv["description"], pv["category"], pv["out"]["secs"]) == ("d", "Město", 20.0)


def test_expand_markers_leaves_point_markers_alone():
    """Markers without an out (point markers) are not given one."""
    structured = {
        "events": [
            {"name": "a", "in": {"secs": 3.0}},
            {"name": "b", "in": {"secs": 7.0}},
        ]
    }
    tm = _tm({"events": {"kind": "markers"}})
    items = expand(structured, tm, annotation_id=1, catdv_clip_id=42, clip_duration_secs=20.0)
    assert [("out" in it.proposed_value) for it in items] == [False, False]


def test_expand_markers_point_marker_does_not_cut_a_range():
    structured = {
        "scenes": [
            {"name": "range", "in": {"secs": 0.0}, "out": {"secs": 20.0}},
            {"name": "point", "in": {"secs": 5.0}},
        ]
    }
    tm = _tm({"scenes": {"kind": "markers"}})
    items = expand(structured, tm, annotation_id=1, catdv_clip_id=42, clip_duration_secs=30.0)
    assert items[0].proposed_value["out"]["secs"] == 20.0


def test_expand_markers_same_start_runs_to_next_distinct_start():
    structured = {
        "scenes": [
            {"name": "a", "in": {"secs": 10.0}, "out": {"secs": 25.0}},
            {"name": "b", "in": {"secs": 10.0}, "out": {"secs": 5.0}},
            {"name": "c", "in": {"secs": 20.0}, "out": {"secs": 30.0}},
        ]
    }
    tm = _tm({"scenes": {"kind": "markers"}})
    items = expand(structured, tm, annotation_id=1, catdv_clip_id=42, clip_duration_secs=30.0)
    assert _scenes(items) == [("a", 10.0, 20.0), ("b", 10.0, 20.0), ("c", 20.0, 30.0)]
