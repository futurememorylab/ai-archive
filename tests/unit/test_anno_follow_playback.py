"""Guards for the annotation follow-playback feature (clip-detail page).

Python-only repo (ADR 0001): no JS runner, so the pure scroll-math is
verified by the manual acceptance flows in the spec. These guards pin the
wiring contract — helper names, DOM data-attrs/active bindings, CSS rule,
and the Studio read-only exclusion — so it can't silently regress.
"""

from pathlib import Path

from backend.app.routes.pages.templates import templates

ROOT = Path(__file__).resolve().parents[2]
PLAYER_JS = (ROOT / "backend/app/static/player.js").read_text()
APP_CSS = (ROOT / "backend/app/static/app.css").read_text()


def test_pure_helpers_defined():
    # Module-scope, this-free helpers — the testable core of the feature.
    assert "function annoActiveAnchorIndex(" in PLAYER_JS
    assert "function annoComputeScroll(" in PLAYER_JS


def test_anchor_helper_returns_minus_one_sentinel():
    # Gap -> -1 (not null/undefined); followActiveAnno relies on `< 0`.
    assert "return -1;" in PLAYER_JS


def test_compute_scroll_uses_viewport_threshold_for_behavior():
    # Smooth for small corrections, instant ('auto') for jumps > 1 viewport.
    assert '"auto"' in PLAYER_JS and '"smooth"' in PLAYER_JS
    assert "viewportHeight" in PLAYER_JS
