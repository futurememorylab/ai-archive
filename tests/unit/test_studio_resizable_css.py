"""CSS guards for the studio resizable split-panes.

The resizers are hand-rolled (ADR 0001 — no Node). A divider element sits
between two panes; dragging it writes a CSS custom property that drives the
container's grid/flex track sizes:

- .studio-right is a 3-track grid [player | divider | compare]; the player
  track uses --studio-player-h (under, row-resize) / --studio-player-w
  (right, col-resize).
- The cur card flex-basis is var(--studio-cmp-cur) only when a .cmp-card is
  present (gated with :has()); the cmp divider is hidden when not comparing
  (:not(:has(.cmp-card))).
- .studio-resizer declares a resize cursor so the affordance is discoverable.
"""

import re
from pathlib import Path

CSS = Path("backend/app/static/app.css")


def test_studio_right_tracks_use_player_size_vars():
    css = CSS.read_text()
    assert "--studio-player-h" in css, (
        ".studio-right under-layout track must use --studio-player-h"
    )
    assert "--studio-player-w" in css, (
        ".studio-right right-layout track must use --studio-player-w"
    )


def test_cmp_cur_basis_gated_by_has_cmp_card():
    css = CSS.read_text()
    normalized = re.sub(r"\s+", " ", css)
    # A :has(.cmp-card) rule sets the cur card width to var(--studio-cmp-cur...)
    assert ":has(.cmp-card)" in normalized
    assert "--studio-cmp-cur" in normalized, (
        "the cur card flex-basis must be var(--studio-cmp-cur) when comparing"
    )
    # The two appear together in a cur-card rule gated by :has(.cmp-card).
    m = re.search(
        r":has\(\.cmp-card\)[^{}]*\{[^}]*--studio-cmp-cur[^}]*\}", normalized
    )
    assert m, (
        "expected a :has(.cmp-card) cur-card rule referencing --studio-cmp-cur"
    )


def test_cmp_divider_hidden_when_not_comparing():
    css = CSS.read_text()
    normalized = re.sub(r"\s+", " ", css)
    assert ":not(:has(.cmp-card))" in normalized, (
        "the cmp divider must be hidden via :not(:has(.cmp-card))"
    )


def test_studio_resizer_declares_resize_cursor():
    css = CSS.read_text()
    assert "col-resize" in css and "row-resize" in css, (
        ".studio-resizer must declare resize cursors (col-resize/row-resize)"
    )


def test_body_studio_resizing_disables_select():
    css = CSS.read_text()
    normalized = re.sub(r"\s+", " ", css)
    assert "body.studio-resizing" in normalized or ".studio-resizing" in normalized, (
        "a body.studio-resizing rule must exist to suppress text selection "
        "during a drag"
    )
