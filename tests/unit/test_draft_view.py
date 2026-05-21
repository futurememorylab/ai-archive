from backend.app.services.draft_view import build_draft_view


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
