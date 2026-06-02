from backend.app.ui.view_models import draft_review_arrays


def _draft():
    return {
        "has_draft": True,
        "markers": [
            {"item_id": 1, "decision": "pending", "name": "A", "category": "est",
             "description": "d", "in_secs": 2.0, "out_secs": 5.0, "color": None, "kind": "marker"},
            {
                "item_id": 2, "decision": "accepted", "name": "B", "category": None,
                "description": None, "in_secs": 8.0, "out_secs": None, "color": None,
                "kind": "marker",
            },
            {
                "item_id": 3, "decision": "rejected", "name": "C", "category": None,
                "description": None, "in_secs": 9.0, "out_secs": None, "color": None,
                "kind": "marker",
            },
        ],
        "fields": [
            {
                "item_id": 11, "decision": "pending", "identifier": "x.y",
                "value": "v", "multi": False, "kind": "field",
            },
        ],
        "note_items": [
            {"item_id": 21, "decision": "accepted", "identifier": None, "text": "note",
             "kind": "note"},
            {"item_id": 22, "decision": "rejected", "identifier": None, "text": "gone",
             "kind": "note"},
        ],
    }


def test_markers_carry_status_and_exclude_rejected():
    a = draft_review_arrays(_draft())
    assert [m["item_id"] for m in a["markers"]] == [1, 2]          # 3 (rejected) dropped
    assert a["markers"][0]["status"] == "proposed"                # pending -> proposed
    assert a["markers"][1]["status"] == "accepted"
    assert a["markers"][0]["in_secs"] == 2.0 and a["markers"][0]["out_secs"] == 5.0
    assert a["markers"][0]["name"] == "A" and a["markers"][0]["category"] == "est"


def test_fields_and_notes_status_and_exclude_rejected():
    a = draft_review_arrays(_draft())
    assert [f["item_id"] for f in a["fields"]] == [11]
    assert a["fields"][0]["status"] == "proposed"
    assert a["fields"][0]["identifier"] == "x.y" and a["fields"][0]["value"] == "v"
    assert [n["item_id"] for n in a["notes"]] == [21]             # 22 (rejected) dropped
    assert a["notes"][0]["status"] == "accepted" and a["notes"][0]["text"] == "note"


def test_no_draft_returns_empty_arrays():
    assert draft_review_arrays({"has_draft": False}) == {"markers": [], "fields": [], "notes": []}
