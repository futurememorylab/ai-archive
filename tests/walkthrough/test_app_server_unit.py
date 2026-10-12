"""WalkthroughApp port selection — no boot, no ffmpeg, no browser."""

from __future__ import annotations

import socket

from tests.walkthrough.app_server import WalkthroughApp, free_port


def test_free_port_is_bindable():
    port = free_port()
    with socket.socket() as s:
        s.bind(("127.0.0.1", port))


def test_default_port_is_free_not_fixed(tmp_path):
    # A fixed default made parallel (pytest -n auto) workers collide on :8766.
    app = WalkthroughApp(data_dir=tmp_path)
    assert app.port != 8766
    assert app.base_url == f"http://127.0.0.1:{app.port}"


def test_explicit_port_is_kept(tmp_path):
    assert WalkthroughApp(data_dir=tmp_path, port=8766).port == 8766
