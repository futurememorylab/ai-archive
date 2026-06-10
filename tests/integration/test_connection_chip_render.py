# tests/integration/test_connection_chip_render.py
"""In manual mode the chip is read-only — no /api/connection/retry button
(retry only probes; it cannot log in). It shows Connected/Disconnected/
Unreachable labels."""

from backend.app.routes.pages.templates import templates


def _render(mode):
    tmpl = templates.get_template("_connection_chip.html")
    return tmpl.render(mode=mode, connect_mode="manual", request=None)


def test_disconnected_label_no_retry():
    html = _render("disconnected")
    assert "Disconnected" in html
    assert "/api/connection/retry" not in html


def test_unreachable_label():
    html = _render("offline")
    assert "Unreachable" in html


def test_connected_label():
    html = _render("online")
    assert "Connected" in html
