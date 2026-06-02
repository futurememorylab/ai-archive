"""The aligned table replaces the per-card Output panes only when comparing."""

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
    pid = r.json()["id"]
    v1 = client.get(f"/api/prompts/{pid}").json()["latest_version_id"]
    client.post(f"/api/prompts/{pid}/versions/{v1}:promote")
    v2 = client.post(f"/api/prompts/{pid}/versions",
                     json={"from_version_id": v1}).json()["id"]
    return pid, v1, v2


def test_compare_output_region_present_when_comparing(client):
    pid, v1, v2 = _two_versions(client)
    r = client.get(f"/studio?prompt_id={pid}&version_id={v2}&compare_version_id={v1}")
    html = r.text
    assert "studio-compare-output" in html
    assert "/studio/_compare?" in html
    assert "compareVersionId === null" in html


def test_single_version_keeps_per_card_output(client):
    pid, _, v2 = _two_versions(client)
    r = client.get(f"/studio?prompt_id={pid}&version_id={v2}")
    html = r.text
    assert "run-slot" in html
