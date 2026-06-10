# tests/unit/test_toast_htmx_bridge.py
"""toast.js must bridge HTMX HX-Trigger 'toast' events into the store so
server responses (e.g. failed Connect) surface as toasts."""

from pathlib import Path

SRC = Path("backend/app/static/toast.js").read_text()


def test_listens_for_htmx_toast_event():
    assert "toast" in SRC
    # the bridge listens on the documented HTMX custom-event name
    assert "addEventListener('toast'" in SRC or 'addEventListener("toast"' in SRC


def test_bridge_pushes_into_store():
    assert "Alpine.store('toast').push" in SRC
