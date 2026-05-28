"""The studio prompt editor textarea must fill the full pane height
rather than collapse to its 220px min-height.

.pc-editor uses height:100%, which only resolves if its ancestor wrapper
divs have a definite height. Those wrappers (the cmp/diff wrapper and the
mode-switch prompt pane) are auto-height by default, so height:100% had
nothing to resolve against. Stable classes + height:100% on the wrappers
fix it. The output pane stays auto-height so it keeps scrolling via
.pc-body (guarded by test_studio_compare_scroll_css)."""

import re
from pathlib import Path

CARD = Path("backend/app/templates/pages/_studio_prompt_card.html")
CSS = Path("backend/app/static/app.css")


def _scoped_rule(css: str, sel: str) -> str | None:
    m = re.search(r"\.studio-prompt-card\s+" + re.escape(sel) + r"\s*\{([^}]*)\}", css)
    return m.group(1) if m else None


def test_pane_wrappers_have_stable_classes():
    t = CARD.read_text()
    assert 'class="pc-panes"' in t, "cmp/diff wrapper needs the pc-panes class"
    assert 'class="pc-pane-prompt"' in t, "prompt pane needs the pc-pane-prompt class"


def test_pane_wrappers_fill_height():
    css = CSS.read_text()
    for sel in (".pc-panes", ".pc-pane-prompt"):
        body = _scoped_rule(css, sel)
        assert body is not None, f"missing .studio-prompt-card {sel} rule"
        assert "height: 100%" in body, (
            f"{sel} must be height:100% so .pc-editor's height:100% resolves"
        )
