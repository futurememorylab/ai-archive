# tests/integration/test_connection_chip_render.py
"""In manual mode the topbar chip is the live connection control: it shows
Connected/Disconnected/Unreachable, carries the Connect/Disconnect/Retry
actions, and self-refreshes via /ui/connection-chip."""

from types import SimpleNamespace

from backend.app.routes.pages.templates import templates


def _vpn(managed=True, desired="on", healthy=True, process_running=True):
    return SimpleNamespace(managed=managed, desired=desired,
                           healthy=healthy, process_running=process_running)


def _render(mode="disconnected", vpn=None, connect_mode="manual"):
    tmpl = templates.get_template("_connection_chip.html")
    return tmpl.render(mode=mode, vpn=vpn, connect_mode=connect_mode, request=None)


# ---- overall pill ----

def test_pill_online_when_vpn_healthy_and_catdv_online():
    html = _render(mode="online", vpn=_vpn(healthy=True))
    assert "Online" in html
    assert "All connected" in html
    assert "is-online" in html


def test_pill_online_local_dev_no_vpn():
    # No WireGuard locally (vpn=None) → VPN layer is absent; CatDV online → Online.
    html = _render(mode="online", vpn=None)
    assert "is-online" in html
    assert "Online" in html
    assert "All connected" in html


def test_pill_offline_vpn_off():
    html = _render(mode="disconnected", vpn=_vpn(desired="off", healthy=False))
    assert "VPN off" in html
    assert "is-offline" in html


def test_pill_error_vpn_unreachable():
    html = _render(mode="disconnected", vpn=_vpn(desired="on", healthy=False))
    assert "VPN unreachable" in html
    assert "is-error" in html


def test_pill_catdv_disconnected_when_vpn_up():
    html = _render(mode="disconnected", vpn=_vpn(healthy=True))
    assert "CatDV disconnected" in html


def test_pill_catdv_unreachable_when_vpn_up():
    html = _render(mode="offline", vpn=_vpn(healthy=True))
    assert "CatDV unreachable" in html


def test_pill_is_popover_trigger():
    html = _render(mode="disconnected", vpn=_vpn(desired="off", healthy=False))
    assert 'class="conn-pill' in html
    assert '@click="toggle()"' in html


def test_chip_self_polls():
    assert 'hx-get="/ui/connection-chip"' in _render()
