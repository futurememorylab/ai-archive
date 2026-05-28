"""The shared player chrome (_player.html) is included by both the clip
detail page and the studio player. Its transport buttons must carry CSS
classes that are actually styled in app.css.

PR#17 created _player.html emitting a bespoke `.tbtn` class, but the
button CSS had earlier been migrated onto the canonical `.btn`
(commit "migrate bespoke button classes onto canonical .btn"). `.tbtn`
has no rule anywhere, so the transport buttons rendered as unstyled
white default buttons. This gate fails on any orphan button class in
the shared player chrome.
"""

import re
from pathlib import Path

PLAYER = Path("backend/app/templates/pages/_player.html")
CSS = Path("backend/app/static/app.css")


def test_player_transport_button_classes_are_styled():
    html = PLAYER.read_text()
    css = CSS.read_text()

    classes: set[str] = set()
    for m in re.finditer(r'<button[^>]*class="([^"]+)"', html):
        classes.update(m.group(1).split())

    assert classes, "expected to find <button class=...> in _player.html"

    for cls in sorted(classes):
        assert re.search(rf"\.{re.escape(cls)}\b", css), (
            f"_player.html uses button class .{cls!s} that has no CSS rule "
            f"in app.css — it renders as an unstyled (white) default button. "
            f"Use a canonical, styled class (e.g. .btn / .btn.play)."
        )
