"""Admin console shell — read surface for editable enumerations (issue #13)."""

import importlib

from fastapi.testclient import TestClient


def _client(monkeypatch, tmp_path) -> TestClient:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from backend.app import main as main_mod

    importlib.reload(main_mod)
    return TestClient(main_mod.app)


def test_admin_lists_editable_enum_with_models(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as client:
        r = client.get("/admin")
        assert r.status_code == 200
        assert "Gemini generation models" in r.text
        assert "gemini-2.5-flash-lite" in r.text


def test_admin_table_partial(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as client:
        r = client.get("/admin/enums/gemini_generation_model")
        assert r.status_code == 200
        assert "gemini-2.5-flash-lite" in r.text
