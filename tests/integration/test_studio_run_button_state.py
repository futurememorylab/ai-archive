"""The studio header binds the Run button to runOrCancel() and to the
new computed label. Static-only test (asserts the rendered template)
— behavioral verification happens via the JS mirror test."""

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
        "name": "rb", "media_kind": "any", "model": "gemini-2.5-pro",
        "target_map": {}, "output_schema": {}, "body": "x",
    })
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    env = client.get(f"/api/prompts/{pid}").json()
    return pid, env["latest_version_id"]


def test_run_button_uses_runOrCancel_and_label_getter(client):
    pid, _ = _make_prompt(client)
    r = client.get(f"/studio?prompt_id={pid}")
    assert r.status_code == 200
    html = r.text
    # Single computed label binding, not dual <template x-if> blocks.
    assert "runButtonLabel()" in html
    # Click target is the dispatcher, not the bare run method.
    assert "runOrCancel()" in html
    # Disabled binding picks up cancelling + doneFlashUntilMs.
    assert "cancelling" in html
    assert "doneFlashUntilMs" in html


def test_run_button_disabled_when_no_focused_clip(client):
    pid, _ = _make_prompt(client)
    r = client.get(f"/studio?prompt_id={pid}")
    assert r.status_code == 200
    assert ":disabled=" in r.text
    assert "focusedClipId" in r.text
