"""The CatDV logout DELETE shares Cloud Run's 10s SIGTERM grace with the
onetun kill (2s) and Litestream's final WAL sync. Keep it tight (2s)."""

from backend.app.services import catdv_client


def test_logout_timeout_is_two_seconds():
    assert catdv_client.LOGOUT_TIMEOUT_S == 2.0
