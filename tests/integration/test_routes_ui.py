"""UI partial routes — sanity-check that Jinja templates render."""

import importlib
from pathlib import Path

from fastapi.testclient import TestClient


def _setenv(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("CATDV_BASE_URL", "http://localhost:0")
    monkeypatch.setenv("CATDV_USERNAME", "")
    monkeypatch.setenv("CATDV_PASSWORD", "p")
    monkeypatch.setenv("CATDV_CATALOG_ID", "881507")
    monkeypatch.setenv("GCP_PROJECT_ID", "p")
    monkeypatch.setenv("GCS_BUCKET_NAME", "b")
    monkeypatch.setenv("PROXY_SOURCE", "rest")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))


def _make_app(monkeypatch, tmp_path):
    _setenv(monkeypatch, tmp_path)
    from backend.app import main as main_mod

    importlib.reload(main_mod)
    return main_mod.app


def test_connection_pill_renders(monkeypatch, tmp_path: Path):
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        r = client.get("/ui/connection-pill")
    assert r.status_code == 200
    assert "connection-pill" in r.text
    assert "Sync now" in r.text


def test_workspace_switcher_renders(monkeypatch, tmp_path: Path):
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        r = client.get("/ui/workspace-switcher")
    assert r.status_code == 200
    assert "workspace-switcher" in r.text
    assert "All clips" in r.text


def test_sync_drawer_renders_empty(monkeypatch, tmp_path: Path):
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        r = client.get("/ui/sync-drawer")
    assert r.status_code == 200
    assert "sync-drawer" in r.text
    assert "No pending writes" in r.text


def test_clip_badge_renders_zero(monkeypatch, tmp_path: Path):
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        r = client.get("/ui/clip-badge/catdv/1")
    assert r.status_code == 200
    # zero pending → no badge spans rendered
    assert "clip-badge" in r.text


def test_pages_have_breadcrumb_and_single_title(monkeypatch, tmp_path: Path):
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        for path in ("/prompts", "/cache"):
            r = client.get(path)
            assert r.status_code == 200
            assert 'class="crumb"' in r.text  # top-bar context present
        cache = client.get("/cache").text
    # title not duplicated: "Cache" lives in the crumb leaf, not a body <h1>
    assert "<h1>Cache</h1>" not in cache
    assert '<span class="strong">Cache</span>' in cache


def test_cache_metric_cap_labelled_and_no_raw_bytes(monkeypatch, tmp_path: Path):
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        r = client.get("/cache")
    assert r.status_code == 200
    # #4: the local-cache cap value is labelled as a cap (renders "of 50.0 GB cap").
    assert "GB cap" in r.text
    # #5: the AI-store m-foot no longer prints the redundant raw-byte line.
    # That line rendered as "<b>N</b> objects ·\n  <span class="muted-2">N B</span>",
    # so the "objects ·" separator and the raw-byte span are both gone.
    assert "objects ·" not in r.text
    assert 'class="muted-2">0 B' not in r.text
