"""Server-side half of the HTMX↔Alpine re-init contract (T3-B2).

`window.htmxAlpine.reinit(el)` re-scans a swapped subtree so Alpine
directives + HTMX attributes come alive. There is no JS test runner, so
this test asserts the server returns the container markup the helper is
expected to re-init: the `/studio/_prompt_card` partial must carry the
`.studio-prompt-card` root with the data-attrs the afterSwap handler
reads back into the store.
"""

import importlib

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.delenv("CATDV_PASSWORD", raising=False)
    from backend.app import main as main_mod
    importlib.reload(main_mod)
    with TestClient(main_mod.app) as c:
        yield c


def _make_prompt(client):
    r = client.post("/api/prompts", json={
        "name": "pc", "media_kind": "any", "model": "gemini-2.5-pro",
        "target_map": {}, "output_schema": {}, "body": "v1",
    })
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    v1 = client.get(f"/api/prompts/{pid}").json()["latest_version_id"]
    return pid, v1


def test_prompt_card_partial_returns_reinitable_container(client):
    _, v1 = _make_prompt(client)
    r = client.get(f"/studio/_prompt_card?side=cur&prompt_version_id={v1}")
    assert r.status_code == 200
    # The container class the helper / afterSwap handler keys off, plus the
    # data-attrs that get reconciled back into the store after re-init.
    assert 'class="studio-prompt-card' in r.text
    assert 'data-side="cur"' in r.text
    assert f'data-version-id="{v1}"' in r.text
