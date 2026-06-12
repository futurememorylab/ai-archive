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


# ---- VPN row ----

def test_vpn_row_off_offers_enable_switch():
    html = _render(mode="disconnected", vpn=_vpn(desired="off", healthy=False))
    assert "VPN tunnel" in html
    assert "/api/vpn/enable" in html


def test_vpn_row_on_offers_disable_switch():
    html = _render(mode="online", vpn=_vpn(desired="on", healthy=True))
    assert "/api/vpn/disable" in html


def test_vpn_row_unreachable_offers_retry():
    html = _render(mode="disconnected", vpn=_vpn(desired="on", healthy=False))
    assert "/api/vpn/retry" in html
    assert "Retry" in html


def test_vpn_row_hidden_when_unmanaged():
    html = _render(mode="disconnected", vpn=None)
    assert "VPN tunnel" not in html
    assert "CatDV Annotator" in html


# ---- CatDV row ----

def test_catdv_row_connected_offers_disconnect():
    html = _render(mode="online", vpn=_vpn(healthy=True))
    assert "/api/connection/disconnect" in html


def test_catdv_row_disconnected_offers_connect():
    html = _render(mode="disconnected", vpn=_vpn(healthy=True))
    assert "/api/connection/connect" in html


def test_catdv_row_unreachable_offers_retry():
    html = _render(mode="offline", vpn=_vpn(healthy=True))
    assert "/api/connection/retry" in html
    assert "Retry" in html


def test_catdv_row_gated_when_vpn_off():
    html = _render(mode="disconnected", vpn=_vpn(desired="off", healthy=False))
    assert "Requires VPN" in html
    assert "can only connect once the VPN tunnel is up" in html
    assert "/api/connection/connect" not in html


def test_catdv_row_gated_when_vpn_unreachable():
    html = _render(mode="disconnected", vpn=_vpn(desired="on", healthy=False))
    assert "Requires VPN" in html


# ---- footer ----

def test_footer_shows_catalog_and_readonly():
    html = _render(mode="online", vpn=_vpn(healthy=True))
    assert "READ-ONLY" in html
    assert "live" in html


def test_footer_cached_when_offline():
    html = _render(mode="disconnected", vpn=_vpn(desired="off", healthy=False))
    assert "cached" in html


def test_catdv_row_forced_offline_shows_disabled_switch():
    # CATDV_OFFLINE=true → CatDV row is non-actionable (disabled span, no connect/disconnect).
    html = _render(mode="forced_offline", vpn=_vpn(healthy=True))
    assert "Offline (forced)" in html
    assert "conn-switch dis" in html
    assert "/api/connection/connect" not in html
    assert "/api/connection/disconnect" not in html


def test_container_is_popover_with_xdata():
    html = _render(mode="disconnected", vpn=_vpn(desired="off", healthy=False))
    assert 'x-data="popover()"' in html
    assert "popover" in html            # container carries the popover class
    assert 'x-show="open"' in html      # panel binds to the parent popover scope
