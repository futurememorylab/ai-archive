# tests/integration/test_connection_chip_render.py
"""In manual mode the topbar chip is the live connection control: it shows
Connected/Disconnected/Unreachable, carries the Connect/Disconnect/Retry
actions, and self-refreshes via /ui/connection-chip."""

from types import SimpleNamespace

from backend.app.routes.pages.templates import templates


def _vpn(managed=True, desired="on", healthy=True, process_running=True, connecting=False):
    return SimpleNamespace(
        managed=managed,
        desired=desired,
        healthy=healthy,
        process_running=process_running,
        connecting=connecting,
    )


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


def test_stable_chip_does_not_poll():
    # No constant background poll when stable — that constant innerHTML swap was
    # the source of the pill/dropdown flicker.
    html = _render(mode="disconnected", vpn=_vpn(desired="off", healthy=False))
    assert 'hx-get="/ui/connection-chip"' not in html


def test_connecting_chip_polls_in_background():
    # While the VPN is coming up, a hidden poller refreshes the chip every 2s so
    # "Connecting…" resolves on its own; it disappears once the state is stable.
    html = _render(mode="disconnected", vpn=_vpn(desired="on", healthy=False, connecting=True))
    assert 'hx-get="/ui/connection-chip"' in html
    assert 'hx-trigger="every 2s"' in html


def test_recovering_chip_polls_to_catch_reconnect():
    # CatDV offline while the VPN is UP is auto-recoverable (manual mode keeps
    # probing every 30s); the chip polls so it reflects recovery without the
    # user navigating — the post-batch "stale Disconnected" fix. The poll matches
    # the monitor's 30s probe interval: the chip reads cached state, so polling
    # faster just re-fetched the same value and spammed the log.
    html = _render(mode="offline", vpn=_vpn(desired="on", healthy=True))
    assert 'hx-get="/ui/connection-chip"' in html
    assert 'hx-trigger="every 30s"' in html


def test_online_chip_does_not_poll():
    # Fully stable → no background poll (avoids the old constant-swap flicker).
    html = _render(mode="online", vpn=_vpn(desired="on", healthy=True))
    assert 'hx-get="/ui/connection-chip"' not in html


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


# ---- dropdown chrome ----


def test_dropdown_has_no_footer():
    # The CATALOG · READ-ONLY footer was removed from the dropdown.
    html = _render(mode="online", vpn=_vpn(healthy=True))
    assert "conn-foot" not in html
    assert "READ-ONLY" not in html


def test_vpn_disable_has_no_confirm():
    # Turning the VPN off no longer pops a confirmation dialog.
    html = _render(mode="online", vpn=_vpn(desired="on", healthy=True))
    assert "/api/vpn/disable" in html
    assert "hx-confirm" not in html


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
    assert "popover" in html  # container carries the popover class
    assert 'x-show="open"' in html  # panel binds to the parent popover scope


def test_inner_partial_has_no_shadow_xdata():
    # The popover scope lives ONLY on the stable container; the swapped inner
    # partial must NOT declare its own x-data (hosted mode), or it would shadow
    # the parent popover scope and break toggle()/open across polls.
    html = _render(mode="disconnected", vpn=_vpn(desired="off", healthy=False))
    assert html.count("x-data=") == 1  # only the container's x-data="popover()"


# ---- VPN connecting phase (amber transition, not red Unreachable) ----


def test_pill_connecting_when_vpn_coming_up():
    html = _render(mode="disconnected", vpn=_vpn(desired="on", healthy=False, connecting=True))
    assert "Connecting" in html
    assert "is-connecting" in html
    assert "Connecting VPN" in html  # weakest-link subtext
    assert "is-error" not in html  # NOT shown as red/unreachable


def test_vpn_row_connecting_shows_amber_not_unreachable():
    html = _render(mode="disconnected", vpn=_vpn(desired="on", healthy=False, connecting=True))
    assert "Connecting" in html
    assert "s-dot work" in html  # amber state dot
    assert "Unreachable" not in html  # not the red error row
    assert "/api/vpn/retry" not in html  # Retry is for the unreachable state


def test_vpn_row_unreachable_only_when_not_connecting():
    # connecting=False + unhealthy → the red Unreachable + Retry path still works.
    html = _render(mode="disconnected", vpn=_vpn(desired="on", healthy=False, connecting=False))
    assert "Unreachable" in html
    assert "/api/vpn/retry" in html
