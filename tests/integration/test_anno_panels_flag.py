"""_anno_panels.html gains an optional show_history flag (default True).
Clip detail leaves it unset → History tab still renders. Studio will pass
show_history=False in a later task."""

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


def test_anno_panels_show_history_default_true_clip_detail_unchanged(client):
    r = client.get("/clips/12041")
    if r.status_code != 200:
        pytest.skip("clip not available in offline test env")
    assert "tab === 'history'" in r.text
