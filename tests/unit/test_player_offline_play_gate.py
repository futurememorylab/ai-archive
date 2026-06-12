"""When the operator is offline AND the clip's media isn't cached anywhere
(neither the local proxy nor the AI store / GCS), playback can't work — the
<video> would just fail to fetch under preload="none". So the shared player
chrome must:

  * disable the Play button (reusing the `.btn:disabled` styling),
  * show an in-player message hinting that reconnecting enables playback, and
  * have player.js refuse the play entry points (button / Spacebar / `L` /
    timeline-click autoplay) while gated.

The gate is driven by a `canPlay` flag in the Alpine player scope, injected by
clip_detail.html from the same `online-or-cached` condition that already gates
the Annotate/Live buttons. It defaults to true so the studio player and the
online/ cached cases are unaffected.

These are static source guards (no JS runtime), matching
test_player_buffering_spinner.py.
"""

from pathlib import Path

STATIC = Path("backend/app/static")
PAGES = Path("backend/app/templates/pages")


def test_play_button_is_disabled_when_cannot_play():
    html = (PAGES / "_player.html").read_text()
    assert ':disabled="!canPlay"' in html, (
        "play button must be disabled while playback is gated (canPlay false)"
    )


def test_player_renders_offline_message():
    html = (PAGES / "_player.html").read_text()
    assert "player-offline" in html, "in-player offline message element missing"
    assert 'x-show="!canPlay"' in html, "offline message must be gated on !canPlay"
    # Better wording than the literal "connect to play video" — actionable and
    # explains the offline-not-cached cause.
    assert "Reconnect to play" in html, "offline message wording missing"


def test_player_js_owns_canplay_and_guards_play():
    js = (STATIC / "player.js").read_text()
    assert "canPlay" in js, "player.js must own a `canPlay` flag (default true)"
    # togglePlay must short-circuit when gated.
    toggle = js[js.index("togglePlay()") : js.index("togglePlay()") + 200]
    assert "this.canPlay" in toggle, "togglePlay() must bail out when !canPlay"


def test_clip_detail_injects_canplay_from_online_or_cached():
    html = (PAGES / "clip_detail.html").read_text()
    assert "canPlay:" in html, "clip_detail must inject canPlay into the player scope"


def test_offline_message_is_styled():
    css = (STATIC / "app.css").read_text()
    assert ".player-offline" in css, "offline message overlay needs a style rule"
