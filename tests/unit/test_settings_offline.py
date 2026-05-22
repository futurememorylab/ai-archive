import pytest

from backend.app.settings import Settings


def _required_env() -> dict[str, str]:
    return {
        "CATDV_BASE_URL": "http://example",
        "CATDV_CATALOG_ID": "1",
        "GCP_PROJECT_ID": "p",
        "GCS_BUCKET_NAME": "b",
    }


def test_catdv_offline_defaults_to_false(monkeypatch):
    for k, v in _required_env().items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("CATDV_OFFLINE", raising=False)
    s = Settings(_env_file=None)
    assert s.catdv_offline is False


@pytest.mark.parametrize(
    "val,expected",
    [("true", True), ("false", False), ("1", True), ("0", False)],
)
def test_catdv_offline_parses_truthy_strings(monkeypatch, val, expected):
    for k, v in _required_env().items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("CATDV_OFFLINE", val)
    s = Settings(_env_file=None)
    assert s.catdv_offline is expected
