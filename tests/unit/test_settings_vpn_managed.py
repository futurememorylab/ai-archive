import pytest
from backend.app.settings import Settings

_BASE = dict(
    catdv_base_url="http://127.0.0.1:18080",
    catdv_catalog_id=1,
    gcp_project_id="p",
    gcs_bucket_name="b",
)


def test_vpn_unmanaged_when_wg_absent():
    s = Settings(**_BASE, _env_file=None)
    assert s.vpn_managed is False
    # 1000 = verified-safe default under the Cloud Run -> gateway path MTU (ADR 0076)
    assert s.onetun_mtu == 1000


def test_vpn_managed_when_all_wg_present():
    s = Settings(
        **_BASE,
        wg_private_key="priv",
        wg_endpoint="gw.example:51820",
        wg_peer_pubkey="pub",
        wg_source_ip="192.168.3.5",
        _env_file=None,
    )
    assert s.vpn_managed is True
    assert s.wg_private_key.get_secret_value() == "priv"


def test_vpn_unmanaged_when_partial_wg():
    s = Settings(**_BASE, wg_private_key="priv", wg_endpoint="gw.example:51820", _env_file=None)
    assert s.vpn_managed is False
