"""nav-feedback must opt the connection chip out of the global progress bar.

The chip swaps its own innerHTML, which removes the triggering Connect/
Disconnect/Retry button; htmx then fires htmx:afterRequest on the detached
node so it never reaches the body listener, and the paired done() is missed
→ the top progress bar leaks ("stuck loading"). Guard the opt-out."""

from pathlib import Path

SRC = Path("backend/app/static/nav-feedback.js").read_text()


def test_connection_chip_is_excluded_from_progress_bar():
    assert "#connection-chip" in SRC
    assert "skipProgressBar" in SRC
