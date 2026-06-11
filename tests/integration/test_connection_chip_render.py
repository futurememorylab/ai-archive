# tests/integration/test_connection_chip_render.py
"""In manual mode the topbar chip is the live connection control: it shows
Connected/Disconnected/Unreachable, carries the Connect/Disconnect/Retry
actions, and self-refreshes via /ui/connection-chip."""

from backend.app.routes.pages.templates import templates


def _render(mode):
    tmpl = templates.get_template("_connection_chip.html")
    return tmpl.render(mode=mode, connect_mode="manual", request=None)


def test_disconnected_offers_connect():
    html = _render("disconnected")
    assert "Disconnected" in html
    assert "/api/connection/connect" in html and "Connect" in html
    assert "/api/connection/retry" not in html  # Connect, not a probe


def test_unreachable_offers_retry():
    html = _render("offline")
    assert "Unreachable" in html
    assert "/api/connection/retry" in html and "Retry" in html


def test_connected_offers_disconnect():
    html = _render("online")
    assert "Connected" in html
    assert "/api/connection/disconnect" in html and "Disconnect" in html


def test_chip_self_polls():
    assert 'hx-get="/ui/connection-chip"' in _render("disconnected")
