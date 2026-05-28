"""Folder list new-folder input + buttons use canonical primitives.
PR3 visual audit removes the inline style= and the undefined .mini
button modifier."""

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


def test_folder_list_uses_canonical_primitives(client):
    r = client.get("/studio")
    assert r.status_code == 200
    # New-folder wrapper uses a class, not inline style.
    assert "studio-folder-new" in r.text
    # The inline style="display:flex;..." string is gone.
    assert 'style="display:flex;gap:6px;padding:8px 12px' not in r.text
    # The .mini button modifier is gone (use .sm).
    assert "btn ghost mini" not in r.text
    assert "btn primary mini" not in r.text
    # The bare-input inline font-size override is gone.
    assert 'style="flex:1;font-size:12px' not in r.text
    # The empty-state inline padding is gone — class-driven.
    assert 'style="padding:12px"' not in r.text


def test_folder_list_input_uses_txt_class(client):
    r = client.get("/studio")
    assert r.status_code == 200
    # The new-folder input is .txt sm (canonical input class).
    assert 'class="txt sm"' in r.text
