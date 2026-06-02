"""The studio player must lay out as a flex column so the video viewer and
the transport bar share the (resizable) player track.

Regression guard: the resizable-panes work first set
`.studio-player .viewer { height: 100%; flex: none }` so the video would fill
the new fixed-height player track. But `.studio-player` is a plain block whose
children are `.viewer` + `.transport` (no `.player-wrap` wrapper in the studio
chrome). `height: 100%` made the viewer consume the entire track height,
pushing the transport controls into overflow below the player. The fix makes
`.studio-player` a flex column and lets `.viewer` flex (flex:1) above the
fixed-height transport.
"""

import re
from pathlib import Path

CSS = Path("backend/app/static/app.css")


def _rule_body(css: str, selector: str) -> str | None:
    # Line-anchored so `.studio-player` matches ONLY the bare rule, not
    # `.studio-player .viewer` or `.studio-player-slot .studio-player`.
    m = re.search(r"(?m)^" + re.escape(selector) + r"\s*\{([^}]*)\}", css)
    return m.group(1) if m else None


def test_studio_player_is_flex_column():
    css = CSS.read_text()
    body = _rule_body(css, ".studio-player")
    assert body is not None, ".studio-player rule missing"
    assert "display: flex" in body, ".studio-player must be a flex container"
    assert "flex-direction: column" in body, (
        ".studio-player must be a column so viewer + transport stack and the "
        "viewer can flex above the transport"
    )
    assert "height: 100%" in body, ".studio-player must fill the slot track"


def test_studio_player_viewer_flexes_not_full_height():
    css = CSS.read_text()
    body = _rule_body(css, ".studio-player .viewer")
    assert body is not None, ".studio-player .viewer rule missing"
    # height:100% made the viewer eat the whole track and overflow the
    # transport — it must flex instead.
    assert "height: 100%" not in body, (
        ".studio-player .viewer must NOT be height:100% (overflows the "
        "transport); it should flex to fill the space above it"
    )
    assert re.search(r"flex:\s*1", body), (
        ".studio-player .viewer should be flex:1 so it fills the space above "
        "the transport"
    )
