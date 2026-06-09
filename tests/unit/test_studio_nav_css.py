# tests/unit/test_studio_nav_css.py
"""Studio navigator restyle — required selectors exist and old folder
selectors are gone."""

from pathlib import Path

CSS = Path("backend/app/static/app.css").read_text()


def test_set_selectors_replace_folder_selectors():
    assert ".studio-folder" not in CSS  # renamed to .studio-set
    assert ".studio-folders" not in CSS
    assert ".studio-set-row" in CSS
    assert ".studio-sets-list" in CSS


def test_nav_and_card_selectors_present():
    for sel in (
        ".studio-nav-tab",
        ".studio-nav-tab.active",
        ".studio-uploaded-stub",
        ".studio-clip-card .clip-check",
        ".studio-clip-card .thumb .yr",
        ".studio-clip-card .thumb .tc",
    ):
        assert sel in CSS, sel


def test_dropzone_and_placeholder_styles_present():
    from pathlib import Path
    css = Path("backend/app/static/app.css").read_text()
    assert ".studio-dropzone" in css
    assert ".thumb-missing" in css
    assert ".set-rename" in css
