"""Studio page renders and includes the expected scaffolding."""

import importlib

import pytest
from fastapi.testclient import TestClient


def _setenv(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.delenv("CATDV_PASSWORD", raising=False)


def _make_app(monkeypatch, tmp_path):
    _setenv(monkeypatch, tmp_path)
    from backend.app import main as main_mod

    importlib.reload(main_mod)
    return main_mod.app


@pytest.fixture
def client(monkeypatch, tmp_path):
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as c:
        yield c


def test_studio_page_renders(client):
    r = client.get("/studio")
    assert r.status_code == 200
    html = r.text
    # Page-level scaffolding
    assert "studio-page" in html
    assert "studio-hdr" in html
    assert "studio-body" in html


def test_studio_rail_button_present(client):
    # /prompts is archive-free and always 200 in the test environment
    r = client.get("/prompts")
    assert r.status_code == 200
    assert 'href="/studio"' in r.text


def test_studio_page_with_prompt_id_renders(client):
    # Even with an unknown prompt_id, the page must render
    r = client.get("/studio?prompt_id=999")
    assert r.status_code == 200
