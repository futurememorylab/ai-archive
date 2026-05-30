"""layout.html must include the toast partial so every page has the
Alpine.store('toast') available. The store is the canonical entry
point for user-facing error UI; without the include, calls to
Alpine.store('toast').push(...) silently no-op."""

from pathlib import Path

LAYOUT = Path(__file__).resolve().parents[2] / "backend" / "app" / "templates" / "pages" / "layout.html"


def test_layout_html_includes_toast_script():
    """layout.html must reference toast.js so the store registers on load."""
    text = LAYOUT.read_text()
    assert "toast.js" in text, (
        "layout.html must include <script src=\"/static/toast.js\">; "
        "without it Alpine.store('toast') is undefined."
    )


def test_layout_html_includes_toast_root_element():
    """The toast partial renders into a designated root the store targets."""
    text = LAYOUT.read_text()
    assert 'id="toast-root"' in text, (
        "layout.html must contain <div id=\"toast-root\">; "
        "toast.js renders into this element."
    )
