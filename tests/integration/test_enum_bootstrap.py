import importlib

from fastapi.testclient import TestClient


def _client(monkeypatch, tmp_path) -> TestClient:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from backend.app import main as main_mod
    importlib.reload(main_mod)
    return TestClient(main_mod.app)


def test_layout_injects_app_enums_with_toast_levels(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as client:
        html = client.get("/prompts").text
        assert "window.APP_ENUMS" in html
        assert '"toast_level"' in html
        assert "info" in html and "success" in html and "error" in html


def test_enums_api_serves_fixed_and_editable(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as client:
        fixed = client.get("/api/enums/toast_level").json()
        assert [v["value"] for v in fixed] == ["info", "success", "error"]
        editable = client.get("/api/enums/gemini_generation_model").json()
        assert any(v["value"] == "gemini-2.5-flash-lite" and v["default"] for v in editable)
