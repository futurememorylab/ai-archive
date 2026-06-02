"""The studio prompt/output pane must scroll when the prompt or output
is taller than the pane.

.studio-compare is the 1fr grid row of .studio-right and has a definite
height. But it is itself a grid with no explicit row track, so its child
.studio-compare-row lands in an implicit `auto` row and grows to content
height — the prompt card follows, and .pc-body (flex:1; overflow:auto)
never overflows, so it never scrolls. Bounding the row with
minmax(0, 1fr) clamps the card to the pane height and lets .pc-body
scroll. This gate guards the bounded row track."""

import re
from pathlib import Path

CSS = Path("backend/app/static/app.css")


def _rule_body(css: str, selector: str) -> str | None:
    m = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", css)
    return m.group(1) if m else None


def test_studio_compare_has_bounded_row_track():
    css = CSS.read_text()
    body = _rule_body(css, ".studio-compare")
    assert body is not None, ".studio-compare rule missing"
    assert "grid-template-rows" in body, (
        ".studio-compare must declare an explicit bounded row track so the "
        "prompt card is height-bounded and .pc-body can scroll"
    )
    normalized = body.replace(" ", "")
    assert "minmax(0,1fr)" in normalized, (
        ".studio-compare row track must be minmax(0, 1fr) to bound the card "
        "height without imposing a content min-height"
    )


def test_cmp_slot_does_not_break_the_height_chain():
    """The compare card is wrapped in .cmp-slot. A plain block wrapper is not
    a flex item of .studio-compare-row, so the cmp card was never
    height-clamped to the row and its .pc-body never scrolled (the cur card,
    a direct flex child, did). `display: contents` removes the slot's box so
    the cmp card becomes a direct flex child — matching
    `.studio-compare-row .studio-prompt-card { flex: 1 1 0 }` — stretches to
    the row height, and scrolls. An empty slot still contributes no box, so
    the cur card stays full-width when not comparing."""
    css = CSS.read_text()
    body = _rule_body(css, ".cmp-slot")
    assert body is not None, ".cmp-slot rule missing"
    assert "display: contents" in body, (
        ".cmp-slot must be display:contents so the compare card is a direct "
        "flex child of the row and gets height-clamped (scrollable)"
    )
