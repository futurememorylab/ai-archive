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


_PANELS = {
    "markers": [{
        "name": "Scene 1", "in_secs": 1.5, "out_secs": 5.6,
        "category": "x", "description": "d", "item_id": 7, "decision": "pending",
    }],
    "fields": [], "notes": None, "big_notes": None, "note_items": [],
    "fps": 25.0,
}


def _render_panels(review_mode=None, follow_player=None):
    ctx = {
        "panels": _PANELS, "scope": "published",
        "clip": {"fps": 25.0, "kind": "video"}, "show_history": False,
    }
    if review_mode is not None:
        ctx["review_mode"] = review_mode
    if follow_player is not None:
        ctx["follow_player"] = follow_player
    return templates.env.get_template("pages/_anno_panels.html").render(**ctx)


def test_published_marker_has_follow_hooks_when_follow_player():
    # The clip-detail published panel sets follow_player=true (see
    # _published_refreshable.html); that's what renders the follow hooks.
    html = _render_panels(follow_player=True)
    assert "data-anno-marker" in html
    assert 'data-in="1.5"' in html
    assert 'data-out="5.6"' in html
    assert "isMarkerActive({in_secs: 1.5, out_secs: 5.6" in html


def test_published_follow_hooks_independent_of_review_mode():
    # Regression (the bug): normal published viewing renders with
    # review_mode=False but MUST still highlight. The gate is follow_player,
    # NOT review_mode — those are different concepts.
    html = _render_panels(review_mode=False, follow_player=True)
    assert "data-anno-marker" in html
    assert "isMarkerActive" in html


def test_published_marker_follow_hooks_absent_in_studio():
    # Studio renders this partial directly (no _published_refreshable, so no
    # follow_player) and has no player scope; isMarkerActive there would
    # break Alpine.initTree.
    html = _render_panels(review_mode=False)
    assert "data-anno-marker" not in html
    assert "isMarkerActive" not in html


def test_published_refreshable_enables_follow_player():
    pr = (ROOT / "backend/app/templates/pages/_published_refreshable.html").read_text()
    assert "follow_player" in pr


def test_css_has_active_card_rule():
    assert ".marker.active" in APP_CSS
    assert ".ri-card.ri-marker.active" in APP_CSS


def test_draft_marker_has_follow_hooks():
    draft = (ROOT / "backend/app/templates/pages/_anno_draft.html").read_text()
    assert "data-anno-marker" in draft
    assert ':data-in="m.in_secs"' in draft
    assert "active: isMarkerActive(m)" in draft


def test_driver_wired_in_player():
    # Watch on current drives the scroll; scope watch re-centres on tab switch.
    assert 'this.$watch("current"' in PLAYER_JS
    assert 'this.$watch("scope"' in PLAYER_JS
    assert "followActiveAnno()" in PLAYER_JS


def test_driver_reads_visible_cards_only():
    # offsetParent filter excludes the hidden scope/tab (display:none).
    assert "[data-anno-marker]" in PLAYER_JS
    assert "offsetParent" in PLAYER_JS


def test_manual_scroll_stops_until_intentional_nav():
    # Manual scroll stops follow and STAYS stopped — no auto-resume timer.
    assert "followSuspended" in PLAYER_JS
    assert "_selfScrolling" in PLAYER_JS     # ignore our own programmatic scroll
    assert "4000" not in PLAYER_JS, "the timed auto-resume must be gone"
    # Resume only on intentional navigation: the next play, or a timeline move.
    assert "this.playing = true; this.followSuspended = false" in PLAYER_JS
    assert "this.followSuspended = false" in PLAYER_JS   # also in seek()
