from backend.app.services.output_compare import build_output_compare


def _marker(in_s, out_s, name, desc=None):
    return {"in_secs": float(in_s), "out_secs": float(out_s),
            "name": name, "description": desc, "category": None}


def _panels(markers=None, fields=None, notes=None):
    return {"markers": markers or [], "fields": fields or [], "notes": notes}


# Mock 1 data: v4 (cmp) vs v5 (cur).
CMP = _panels(markers=[
    _marker(0, 7, "Celkovy pohled na statek se vzrostlym stromem"),
    _marker(7, 28, "Zeny pracuji na dvore u lavice"),
    _marker(28, 42, "Zena stoji u zdi domu"),
])
CUR = _panels(markers=[
    _marker(0, 7, "Celkovy pohled na statek se vzrostlym stromem"),
    _marker(7, 17, "Zena v satku loupe brambory na drevene lavici"),
    _marker(17, 24, "Dite v bilem cepci sedi u stolu vedle zeny"),
    _marker(24, 35, "Zena ve vzorovanych satech stoji u zdi domu"),
    _marker(35, 42, "Detail naradi a nadob u zdi staveni"),
])


def test_mock1_aligns_to_five_scenes_with_expected_statuses():
    model = build_output_compare(CUR, CMP)
    assert model["scene_count"] == 5
    assert [r["status"] for r in model["scenes"]] == [
        "unchanged", "changed", "added", "changed", "added",
    ]


def test_added_scene_has_no_cmp_side():
    model = build_output_compare(CUR, CMP)
    added = [r for r in model["scenes"] if r["status"] == "added"]
    assert added and all(r["cmp"] is None and r["cur"] is not None for r in added)


def test_scene_keys_are_unique_and_stable():
    model = build_output_compare(CUR, CMP)
    keys = [r["key"] for r in model["scenes"]]
    assert keys == [f"scene-{i}" for i in range(5)]


def test_changed_scene_segs_have_ins_and_del():
    model = build_output_compare(CUR, CMP)
    changed = next(r for r in model["scenes"] if r["status"] == "changed")
    types = {s["type"] for s in changed["segs"]}
    assert "ins" in types and "del" in types


def test_scene_side_carries_tc_dur_and_name():
    model = build_output_compare(CUR, CMP)
    first = model["scenes"][0]
    assert first["cur"]["tc"] == "0:00"
    assert first["cur"]["dur_s"] == 7
    assert first["cur"]["name"].startswith("Celkovy")


def test_removed_only_when_cur_empty():
    model = build_output_compare(_panels(), CMP)
    assert model["scene_count"] == 3
    assert {r["status"] for r in model["scenes"]} == {"removed"}
    assert all(r["cur"] is None for r in model["scenes"])


def test_added_only_when_cmp_empty():
    model = build_output_compare(CUR, _panels())
    assert {r["status"] for r in model["scenes"]} == {"added"}


def test_all_unchanged_when_identical():
    model = build_output_compare(CMP, CMP)
    assert {r["status"] for r in model["scenes"]} == {"unchanged"}


def test_empty_inputs_produce_empty_model():
    model = build_output_compare(_panels(), _panels())
    assert model["scene_count"] == 0
    assert model["scenes"] == []
    assert model["fields"] == []
    assert model["notes"] is None


def test_fields_align_by_identifier():
    cur = _panels(fields=[
        {"identifier": "location", "value": "Harbor Beach"},
        {"identifier": "mood", "value": "serene"},
    ])
    cmp = _panels(fields=[
        {"identifier": "location", "value": "Beach"},
        {"identifier": "weather", "value": "sunny"},
    ])
    model = build_output_compare(cur, cmp)
    by = {f["identifier"]: f for f in model["fields"]}
    assert by["location"]["status"] == "changed"
    assert by["mood"]["status"] == "added"
    assert by["weather"]["status"] == "removed"
    assert by["location"]["has_cmp"] and by["location"]["has_cur"]
    assert by["weather"]["has_cmp"] and not by["weather"]["has_cur"]


def test_notes_diff_present_when_changed():
    cur = _panels(notes="hello brave world")
    cmp = _panels(notes="hello world")
    model = build_output_compare(cur, cmp)
    assert model["notes"]["changed"] is True
    assert any(s["type"] == "ins" for s in model["notes"]["segs"])


def test_notes_none_when_both_empty():
    assert build_output_compare(_panels(), _panels())["notes"] is None


def test_scene_dur_falls_back_when_out_missing():
    cur = _panels(markers=[{"in_secs": 5.0, "out_secs": None, "name": "X",
                            "description": None, "category": None}])
    model = build_output_compare(cur, _panels())
    side = model["scenes"][0]["cur"]
    assert side["out_secs"] is None
    assert side["dur_s"] == 1   # in+1 fallback
    assert side["tc"] == "0:05"


def test_time_changed_flag_set_when_times_differ_same_text():
    cur = _panels(markers=[_marker(7, 17, "Same name")])
    cmp = _panels(markers=[_marker(7, 28, "Same name")])
    row = build_output_compare(cur, cmp)["scenes"][0]
    assert row["status"] == "unchanged"   # text identical
    assert row["time_changed"] is True    # but the out-point moved


def test_time_changed_false_when_times_identical():
    cur = _panels(markers=[_marker(7, 17, "A")])
    cmp = _panels(markers=[_marker(7, 17, "A")])
    assert build_output_compare(cur, cmp)["scenes"][0]["time_changed"] is False


def test_time_changed_false_for_added_or_removed():
    model = build_output_compare(_panels(markers=[_marker(0, 5, "X")]), _panels())
    assert model["scenes"][0]["time_changed"] is False


def test_time_changed_ignores_subsecond_wobble_that_renders_identically():
    # Both render as "0:00 · 6s" (in rounds to 0:00, dur rounds to 6), so even
    # though the raw out-points differ (6.2 vs 6.4) the time is NOT flagged.
    cur = _panels(markers=[_marker(0.0, 6.4, "A")])
    cmp = _panels(markers=[_marker(0.0, 6.2, "A")])
    assert build_output_compare(cur, cmp)["scenes"][0]["time_changed"] is False
