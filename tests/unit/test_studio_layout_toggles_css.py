"""Guards the CSS rules that drive the studio layout toggles. The
toggles are pure CSS modifier classes on .studio-body / .studio-right;
if these selectors go missing the toggles silently no-op."""

from pathlib import Path

CSS = Path("backend/app/static/app.css")


def test_no_list_rule_exists():
    css = CSS.read_text()
    assert ".studio-body.no-list" in css, "missing .no-list grid rule"


def test_layout_right_rule_exists():
    css = CSS.read_text()
    assert ".studio-right.layout-right" in css, "missing .layout-right grid rule"


def test_layout_toggles_group_styled():
    css = CSS.read_text()
    assert ".studio-layout-toggles" in css, "missing toggle-group rule"


def test_dead_player_minimise_css_removed():
    css = CSS.read_text()
    assert ".studio-player-min" not in css, (
        "the player minimise button is removed; its CSS should go too"
    )
    assert ".studio-show-player" not in css, (
        "the header restore button is removed; its CSS should go too"
    )
