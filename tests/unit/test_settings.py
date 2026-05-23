def test_settings_loads_from_env(monkeypatch):
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("CATDV_BASE_URL", "http://example.test:8080")
    monkeypatch.setenv("CATDV_USERNAME", "user1")
    monkeypatch.setenv("CATDV_PASSWORD", "pw")
    monkeypatch.setenv("CATDV_CATALOG_ID", "881507")
    monkeypatch.setenv("GCP_PROJECT_ID", "p")
    monkeypatch.setenv("GCS_BUCKET_NAME", "b")
    monkeypatch.setenv("PROXY_SOURCE", "rest")
    monkeypatch.setenv("DATA_DIR", "/tmp/cdv")

    from backend.app.settings import Settings

    s = Settings(_env_file=None)
    assert s.app_env == "dev"
    assert s.catdv_base_url == "http://example.test:8080"
    assert s.catdv_catalog_id == 881507
    assert s.proxy_source == "rest"
    assert s.gemini_model == "gemini-2.5-pro"  # default


def test_settings_accepts_filesystem_without_fs_root(monkeypatch):
    monkeypatch.setenv("CATDV_BASE_URL", "http://x")
    monkeypatch.setenv("CATDV_CATALOG_ID", "1")
    monkeypatch.setenv("GCP_PROJECT_ID", "p")
    monkeypatch.setenv("GCS_BUCKET_NAME", "b")
    monkeypatch.setenv("PROXY_SOURCE", "filesystem")
    monkeypatch.delenv("PROXY_FS_ROOT", raising=False)

    from backend.app.settings import Settings

    s = Settings(_env_file=None)
    assert s.proxy_source == "filesystem"
