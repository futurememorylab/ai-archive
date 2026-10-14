"""_anno_panels.html is shared by the clip-page draft review and the
studio run output. On the clip page it shows the review accept/reject
("keep") checkbox + inline edit/drag machinery (which binds to the
player scope's editingItemId / _draftItem / riReadout). Studio has none
of that scope and no apply step, so it passes review_mode=False to get a
read-only view: markers/fields/notes display only, markers stay
click-to-seek, and crucially NO references to player-only methods (so
Alpine.initTree can wire the injected output without throwing)."""

from backend.app.routes.pages.templates import templates

PANELS = {
    "markers": [
        {
            "name": "Scene 1",
            "in_secs": 0.0,
            "out_secs": 5.6,
            "category": "x",
            "description": "d",
            "item_id": 1,
            "decision": "pending",
        }
    ],
    "fields": [
        {
            "identifier": "a.b",
            "value": "v",
            "multi": False,
            "item_id": 2,
            "decision": "pending",
        }
    ],
    "notes": None,
    "big_notes": None,
    "note_items": [{"text": "a note", "item_id": 3, "decision": "pending"}],
    "fps": 25.0,
}


def _render(review_mode=None):
    ctx = {
        "panels": PANELS,
        "scope": "published",
        "clip": {"fps": 25.0, "kind": "video"},
        "show_history": False,
    }
    if review_mode is not None:
        ctx["review_mode"] = review_mode
    return templates.env.get_template("pages/_anno_panels.html").render(**ctx)


def test_default_review_mode_keeps_checkbox():
    # Clip page passes nothing -> review_mode defaults True -> keep stays.
    html = _render()
    assert "ri-accept" in html
    assert "> keep" in html


def test_studio_read_only_drops_keep_and_edit():
    html = _render(review_mode=False)
    assert "ri-accept" not in html, "keep checkbox must be gone in studio"
    assert "> keep" not in html
    assert "✎ Edit" not in html, "edit button must be gone in studio"


def test_studio_read_only_has_no_player_only_method_refs():
    # These bind to the clip page's player scope; if present, Alpine.initTree
    # of the studio output would throw and dead-click everything.
    html = _render(review_mode=False)
    assert "_draftItem" not in html
    assert "riReadout" not in html
    assert "startMarkerDrag" not in html


def test_studio_read_only_markers_still_seek():
    html = _render(review_mode=False)
    assert "seek(0.0)" in html, "marker must remain click-to-seek in studio"


# --- #123 provenance UI ---------------------------------------------------

from backend.app.ui.view_models import draft_review_arrays  # noqa: E402


def _render_panels(panels, *, review_mode=True):
    return templates.env.get_template("pages/_anno_panels.html").render(
        panels=panels,
        scope="published",
        clip={"fps": 25.0, "kind": "video"},
        show_history=False,
        review_mode=review_mode,
    )


def test_published_marker_shows_origin_pill():
    panels = {
        "markers": [
            {
                "name": "Two men talk",
                "in_secs": 4.0,
                "out_secs": 6.0,
                "category": "Interview",
                "description": None,
                "origin": "ai",
            },
            {
                "name": "Žena",
                "in_secs": 8.0,
                "out_secs": None,
                "category": "Exteriér",
                "description": None,
                "origin": "human",
            },
        ],
        "fields": [],
        "notes": None,
        "big_notes": None,
        "note_items": [],
        "fps": 25.0,
    }
    html = _render_panels(panels, review_mode=False)
    assert "AI · published" in html
    assert "Human" in html


def test_draft_marker_shows_ai_draft_pill():
    panels = {
        "markers": [
            {
                "name": "Scene",
                "in_secs": 1.0,
                "out_secs": 2.0,
                "category": "X",
                "description": None,
                "origin": "ai_draft",
                "item_id": 5,
                "decision": "pending",
            },
        ],
        "fields": [],
        "notes": None,
        "big_notes": None,
        "note_items": [],
        "fps": 25.0,
    }
    html = _render_panels(panels)
    assert "AI · draft" in html


def test_suggestion_field_has_copy_button_and_no_keep():
    # A non-owned field flows through draft_review_arrays as a suggestion.
    draft = {
        "has_draft": True,
        "markers": [],
        "fields": [
            {
                "item_id": 7,
                "decision": "pending",
                "identifier": "pragafilm.dekáda.natočení",
                "value": "30.léta",
                "multi": False,
                "kind": "field",
                "applied_at": None,
            },
        ],
        "note_items": [],
    }
    arrays = draft_review_arrays(draft)
    panels = {
        "markers": [],
        "fields": arrays["fields"],
        "notes": None,
        "big_notes": None,
        "note_items": [],
        "fps": 25.0,
    }
    html = _render_panels(panels)
    assert "AI · suggestion" in html
    assert "Suggestion — not written to CatDV." in html
    assert 'data-copy="30.léta"' in html
    # the suggestion row offers no keep checkbox
    assert 'data-item-id="7"' in html
    assert "ri-suggestion" in html


def test_published_field_carries_origin_pill():
    panels = {
        "markers": [],
        "fields": [
            {"identifier": "pragafilm.popis.ai", "value": "shrnutí", "origin": "ai"},
            {"identifier": "pragafilm.popis.materialu", "value": "human", "origin": "human"},
        ],
        "notes": None,
        "big_notes": None,
        "note_items": [],
        "fps": 25.0,
    }
    html = _render_panels(panels, review_mode=False)
    assert "AI · published" in html  # the popis.ai field
    assert "Human" in html
