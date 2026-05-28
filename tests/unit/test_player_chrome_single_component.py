"""There must be exactly one player + timeline chrome, shared by the clip
page and studio. Both include pages/_player.html (which owns the
<video>/<img>, transport, and the _player_overlay.html timeline); neither
hand-rolls its own timeline. Guards against a second, divergent renderer."""

from pathlib import Path

PAGES = Path("backend/app/templates/pages")


def test_clip_and_studio_share_player_html():
    clip = (PAGES / "clip_detail.html").read_text()
    studio = (PAGES / "_studio_player.html").read_text()
    assert 'include "pages/_player.html"' in clip
    assert 'include "pages/_player.html"' in studio


def test_only_player_html_includes_the_timeline_overlay():
    # _player_overlay.html (the timeline) is included by exactly one file:
    # the shared _player.html chrome. No page should include it directly.
    includers = [
        p.name
        for p in PAGES.glob("*.html")
        if 'include "pages/_player_overlay.html"' in p.read_text()
    ]
    assert includers == ["_player.html"], (
        f"timeline overlay should only be included by _player.html, got {includers}"
    )
