from backend.app.settings import Settings


def _base_env(monkeypatch):
    monkeypatch.setenv("CATDV_BASE_URL", "http://localhost:0")
    monkeypatch.setenv("CATDV_CATALOG_ID", "1")
    monkeypatch.setenv("GCP_PROJECT_ID", "p")
    monkeypatch.setenv("GCS_BUCKET_NAME", "b")


def test_dev_reload_defaults_false(monkeypatch):
    _base_env(monkeypatch)
    monkeypatch.delenv("DEV_RELOAD", raising=False)
    assert Settings(_env_file=None).dev_reload is False  # type: ignore[call-arg]


def test_dev_reload_reads_env(monkeypatch):
    _base_env(monkeypatch)
    monkeypatch.setenv("DEV_RELOAD", "1")
    assert Settings(_env_file=None).dev_reload is True  # type: ignore[call-arg]
