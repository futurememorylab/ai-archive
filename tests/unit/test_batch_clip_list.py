"""Per-clip status for a batch's Show clips list (batches redesign spec)."""

from backend.app.ui.view_models import batch_clip_list


def _r(clip_id, item_status="review_ready", *, awaiting=False, sync=None, error=None, name=None):
    return {
        "clip_id": clip_id,
        "name": name,
        "item_status": item_status,
        "error": error,
        "awaiting": awaiting,
        "sync": sync,
    }


def test_statuses_and_order_put_what_needs_you_first():
    rows = [
        _r(1),  # reviewed
        _r(2, "pending"),
        _r(3, "prompting"),
        _r(4, "error", error="Gemini timed out"),
        _r(5, awaiting=True, name="Výlet do Tater"),
        _r(6, sync="saving"),
        _r(7, sync="problem"),
        _r(8, "cancelled"),
    ]
    items, total = batch_clip_list(rows)
    assert total == 8
    assert [(i["id"], i["status"]["label"]) for i in items] == [
        (5, "To review"),
        (4, "Failed"),
        (7, "Couldn't save"),
        (3, "Running"),
        (2, "Waiting"),
        (6, "Saving to CatDV…"),
        (1, "Reviewed"),
        (8, "Stopped"),
    ]
    first = items[0]
    assert first["name"] == "Výlet do Tater"
    assert first["status"]["state"] == "info"
    assert first["row_href"] == "/clips/5"
    assert first["thumb_url"] == "/api/media/5/thumb"
    assert items[1]["name"] == "Clip 4"
    assert items[1]["error"] == "Gemini timed out"


def test_caps_the_list_but_reports_the_total():
    items, total = batch_clip_list([_r(i) for i in range(1, 121)], limit=50)
    assert len(items) == 50
    assert total == 120
