"""The Description tab's summary shows the human and AI clip descriptions
side by side as a Human / AI toggle, rendering only the non-empty ones."""

from backend.app.routes.pages.templates import templates


def _render(descriptions):
    panels = {
        "markers": [],
        "fields": [],
        "notes": None,
        "big_notes": None,
        "descriptions": descriptions,
        "fps": 25.0,
    }
    return templates.env.get_template("pages/_anno_panels.html").render(
        panels=panels, clip={"fps": 25.0, "kind": "video"}, show_history=False
    )


def test_both_descriptions_render_as_toggle():
    html = _render(
        [
            {"key": "human", "label": "Human", "origin": "human", "text": "Lidský popis"},
            {"key": "ai", "label": "AI", "origin": "ai", "text": "AI popis"},
        ]
    )
    assert "Lidský popis" in html and "AI popis" in html
    assert "desc = 'human'" in html and "desc = 'ai'" in html
    assert "desc: 'human'" in html  # first non-empty one is selected


def test_single_description_has_no_toggle():
    html = _render([{"key": "ai", "label": "AI", "origin": "ai", "text": "AI popis"}])
    assert "AI popis" in html
    assert "desc = 'ai'" not in html


def test_no_descriptions_no_summary():
    assert "anno-summary" not in _render([])
