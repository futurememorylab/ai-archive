"""SSR smoke tests for /prompts."""
import importlib
from pathlib import Path

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


def _new_body(**over):
    base = {
        "name": "Test Prompt",
        "description": "test",
        "body": "p",
        "target_map": {"x": {"kind": "markers"}},
        "output_schema": {"type": "object"},
        "model": "m",
    }
    base.update(over)
    return base


def test_prompts_page_renders_empty(monkeypatch, tmp_path: Path):
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        r = client.get("/prompts")
        assert r.status_code == 200
        assert "Prompts" in r.text
        # Empty state when no seed loaded — the SSR test bypasses lifespan seeding.
        assert "No prompts yet" in r.text or "page-body" in r.text


def test_prompts_page_lists_seeded_prompt(monkeypatch, tmp_path: Path):
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        # Manually seed one prompt via the API to avoid relying on the seed loader.
        pid = client.post("/api/prompts", json=_new_body()).json()["id"]
        r = client.get("/prompts")
        assert r.status_code == 200
        assert "Test Prompt" in r.text
        r = client.get(f"/prompts/{pid}")
        assert r.status_code == 200
        assert "Test Prompt" in r.text


def test_rail_includes_prompts_link(monkeypatch, tmp_path: Path):
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        r = client.get("/prompts")
        assert r.status_code == 200
        assert 'href="/prompts"' in r.text


def test_prompts_detail_renders_editor_textareas(monkeypatch, tmp_path: Path):
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        body = {
            "name": "Editor Test", "description": "",
            "body": "Edit me", "target_map": {"x": {"kind": "markers"}},
            "output_schema": {"type": "object"}, "model": "gemini-2.5-pro",
        }
        pid = client.post("/api/prompts", json=body).json()["id"]
        r = client.get(f"/prompts/{pid}")
        assert r.status_code == 200
        # Editor textareas present:
        assert "x-model=\"draft.body\"" in r.text or "x-model='draft.body'" in r.text
        assert "x-model=\"draft.target_map_text\"" in r.text or "x-model='draft.target_map_text'" in r.text
        assert "x-model=\"draft.output_schema_text\"" in r.text or "x-model='draft.output_schema_text'" in r.text
        # Alpine factory bootstrap:
        assert "promptEditor({" in r.text
        # The new draft prompt isn't read-only (state=draft → canEdit=true):
        assert "Edit me" in r.text
