"""Markup guards for the studio split-pane dividers.

Two resizers cover both layouts:
- player <-> prompt: a divider in .studio-right with data-studio-resizer="player".
  Always present in the studio shell.
- cur <-> cmp: a divider in the compare row with data-studio-resizer="cmp".
  Always rendered (CSS hides it via :not(:has(.cmp-card)) when not comparing).
"""

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


def _two_versions(client):
    r = client.post("/api/prompts", json={
        "name": "cmp", "media_kind": "any", "model": "gemini-2.5-pro",
        "target_map": {}, "output_schema": {}, "body": "v1",
    })
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    v1 = client.get(f"/api/prompts/{pid}").json()["latest_version_id"]
    pr = client.post(f"/api/prompts/{pid}/versions/{v1}:promote")
    assert pr.status_code == 200, pr.text
    r = client.post(f"/api/prompts/{pid}/versions", json={"from_version_id": v1})
    assert r.status_code == 201, r.text
    v2 = r.json()["id"]
    return pid, v1, v2


def test_player_resizer_present_in_studio_shell(client):
    pid, _, _ = _two_versions(client)
    r = client.get(f"/studio?prompt_id={pid}")
    assert r.status_code == 200
    assert 'data-studio-resizer="player"' in r.text


def test_cmp_resizer_present_when_not_comparing(client):
    """The cmp divider is always rendered; CSS hides it via :has()."""
    pid, _, _ = _two_versions(client)
    r = client.get(f"/studio?prompt_id={pid}")
    assert r.status_code == 200
    assert 'data-studio-resizer="cmp"' in r.text


def test_cmp_resizer_present_when_comparing(client):
    pid, v1, v2 = _two_versions(client)
    r = client.get(
        f"/studio?prompt_id={pid}&version_id={v2}&compare_version_id={v1}"
    )
    assert r.status_code == 200
    assert 'data-studio-resizer="cmp"' in r.text
