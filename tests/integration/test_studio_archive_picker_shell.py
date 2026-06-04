"""The studio archive-picker route renders only the modal shell; the result
rows come client-side from the shared /batches/picker endpoint (spec:
docs/specs/2026-06-04-studio-archive-picker-reuse-design.md). Fixture shape
mirrors tests/integration/test_studio_folders_htmx_partials.py."""

import importlib

import pytest
from fastapi.testclient import TestClient


def _setenv(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.delenv("CATDV_PASSWORD", raising=False)


@pytest.fixture
def client(monkeypatch, tmp_path):
    _setenv(monkeypatch, tmp_path)
    from backend.app import main as main_mod
    importlib.reload(main_mod)
    with TestClient(main_mod.app) as c:
        yield c


def test_picker_shell_renders_offline(client):
    r = client.get("/studio/_archive_picker?folder_id=7")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert "archivePicker(7)" in r.text  # Alpine component wired
    # Full shared-picker chrome (spec v2): filters, list, pager, basket.
    assert "nb-filters" in r.text
    assert "nb-list" in r.text
    assert "nb-pager" in r.text
    assert "nb-basket" in r.text


def test_picker_shell_has_no_bare_rows_or_htmx_search(client):
    r = client.get("/studio/_archive_picker?folder_id=7")
    assert "picker-row" not in r.text  # bare renderer deleted
    assert "hx-get" not in r.text      # search is Alpine-driven now
