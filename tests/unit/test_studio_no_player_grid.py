"""When the player is toggled off, the prompt/compare area must still fill.

Regression guard: with the player off, both the player slot
(`.studio-body.no-player .studio-player-slot { display:none }`) and the player
resizer divider (`.studio-body.no-player .studio-resizer.is-player {
display:none }`) are removed from the grid. If the `.studio-right` grid keeps
a multi-track template like `0 0 1fr`, the lone remaining `.studio-compare`
item auto-places into the FIRST (zero-sized) track and collapses — taking the
prompts with it. The no-player grid must be a SINGLE `1fr` track (rows in
`under`, columns in `right`) so the one remaining item fills it.
"""

import re
from pathlib import Path

CSS = Path("backend/app/static/app.css")


def _rule_body(css: str, selector: str) -> str | None:
    m = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", css)
    return m.group(1) if m else None


def test_no_player_under_uses_single_row_track():
    css = CSS.read_text()
    body = _rule_body(css, ".studio-body.no-player .studio-right")
    assert body is not None, ".studio-body.no-player .studio-right rule missing"
    assert "grid-template-rows: 1fr" in body, (
        "no-player must collapse to a single 1fr row so the lone compare item "
        "(slot + divider are display:none) fills it"
    )
    assert "0 0 1fr" not in body, (
        "a 3-track template strands the lone compare item in the first "
        "0-height track — prompts collapse"
    )


def test_no_player_right_uses_single_column_track():
    css = CSS.read_text()
    body = _rule_body(css, ".studio-body.no-player .studio-right.layout-right")
    assert body is not None, "no-player layout-right rule missing"
    assert "grid-template-columns: 1fr" in body, (
        "no-player + right must collapse to a single 1fr column so compare fills"
    )
    assert "0 0 1fr" not in body
