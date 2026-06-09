from backend.app.routes.pages.templates import templates


def _render(c):
    tmpl = templates.env.get_template("pages/_studio_set_clip_card.html")
    return tmpl.render(c=c, set_id=7, focused_clip_id=None)


def test_archive_card_shows_id_tag():
    html = _render({"clip_id": 42, "name": "Archive", "duration_secs": 10.0,
                    "year": 1999, "fps": 25.0, "has_cur": False, "has_other": False,
                    "uploaded": False})
    assert "id:42" in html


def test_uploaded_card_suppresses_id_and_uses_img_poster():
    html = _render({"clip_id": 1_000_000_001, "name": "holiday.mp4",
                    "duration_secs": 12.0, "year": None, "fps": 25.0,
                    "has_cur": False, "has_other": False, "uploaded": True})
    assert "holiday.mp4" in html
    assert "id:1000000001" not in html
    assert "onerror" in html  # placeholder fallback on poster decode failure
    assert "/api/media/1000000001/thumb" in html
