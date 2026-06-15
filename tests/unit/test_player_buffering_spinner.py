"""The shared player chrome shows a buffering spinner while the <video> is
fetching or decoding media — the first play (preload="none" defers the proxy
fetch), a seek to an un-buffered point/marker, or a mid-playback network stall.

These are static guards (no JS runtime): the overlay element must be bound to
the `buffering` state, player.js must flip that state on the native media
events, and the spinner must be styled. Guards against silent regression.

Issue #54: the spinner must NOT be driven by `loadstart`. Under preload="none"
`loadstart` fires during the resource-selection algorithm — i.e. as soon as the
clip is selected/rendered, before any play — and the browser then fires
`suspend` (never `canplay`/`playing`), so a loadstart-driven spinner spins
forever on a merely-selected clip. Buffering is instead armed when the user
requests play.
"""

from pathlib import Path

STATIC = Path("backend/app/static")
PAGES = Path("backend/app/templates/pages")


def test_player_html_renders_buffering_spinner():
    html = (PAGES / "_player.html").read_text()
    assert "player-spinner" in html, "buffering spinner element missing from player chrome"
    assert 'x-show="buffering"' in html, "spinner must be gated on the `buffering` state"


def test_player_js_tracks_buffering_on_media_events():
    js = (STATIC / "player.js").read_text()
    assert "buffering" in js, "player.js must own a `buffering` state"
    # Mid-playback stalls / seeks arm the spinner; OFF once it can present/continue.
    for ev in ("waiting", "seeking", "playing", "seeked", "canplay"):
        assert f'"{ev}"' in js, f"player.js must handle the `{ev}` media event for the spinner"


def test_player_js_does_not_arm_spinner_on_loadstart():
    """Regression guard for #54: loadstart fires on mere clip selection under
    preload="none" and has no matching off-event, so it must not arm the
    spinner — otherwise it spins forever on a selected-but-unplayed clip."""
    js = (STATIC / "player.js").read_text()
    assert 'addEventListener("loadstart"' not in js, (
        "loadstart must not drive the buffering spinner (#54): it fires during "
        "resource selection, not on play, and never clears under preload=none"
    )


def test_player_js_arms_spinner_on_play_request():
    """The spinner is armed when the user requests play (and the proxy fetch
    may need a round-trip), matching #54's 'only when play is requested and the
    player is caching content'."""
    js = (STATIC / "player.js").read_text()
    assert "buffering = true" in js, (
        "play request must arm the buffering spinner so it shows while the "
        "first-play proxy fetch is in flight"
    )


def test_player_spinner_is_styled():
    css = (STATIC / "app.css").read_text()
    assert ".player-spinner" in css, "spinner overlay needs a style rule"
