"""The studio page renders the three layout toggles in its header and
the layout :class bindings on the body/right panes."""

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


def _make_prompt(client):
    r = client.post("/api/prompts", json={
        "name": "ps", "media_kind": "any", "model": "gemini-2.5-pro",
        "target_map": {}, "output_schema": {}, "body": "x",
    })
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_studio_page_renders_layout_toggles(client):
    pid = _make_prompt(client)
    page = client.get(f"/studio?prompt_id={pid}")
    assert page.status_code == 200
    assert 'class="studio-layout-toggles"' in page.text
    for which in ("list", "player", "layout"):
        assert f'data-studio-toggle="{which}"' in page.text
    # Layout bindings present on the panes.
    assert "'no-list': !showList" in page.text
    assert "'layout-right': layout === 'right'" in page.text
