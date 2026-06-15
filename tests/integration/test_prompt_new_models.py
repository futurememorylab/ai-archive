import importlib

from fastapi.testclient import TestClient


def _client(monkeypatch, tmp_path) -> TestClient:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from backend.app import main as main_mod
    importlib.reload(main_mod)
    return TestClient(main_mod.app)


def test_new_prompt_dropdown_reflects_catalog_and_default(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as client:
        from backend.app import main as main_mod

        async def _edit():
            svc = main_mod.app.state.core_ctx.enum_service
            await svc.add_value("gemini_generation_model", "gemini-4.0-pro")
            await svc.set_enabled("gemini_generation_model", "gemini-3.5-flash", enabled=False)

        client.portal.call(_edit)

        html = client.get("/prompts/new").text
        assert "gemini-4.0-pro" in html  # runtime add visible
        assert "gemini-3.5-flash" not in html  # disabled excluded


def test_orphaned_model_still_shown_in_edit_form(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as client:
        r = client.post(
            "/prompts/_create",
            data={
                "name": "orphan-test",
                "description": "",
                "body": "b",
                "target_map": "{}",
                "output_schema": "{}",
                "model": "gemini-3.1-pro-preview",
                "media_kind": "any",
            },
            follow_redirects=False,
        )
        assert r.status_code == 303
        pid = r.headers["location"].rsplit("/", 1)[-1]

        async def _remove():
            svc = client.app.state.core_ctx.enum_service  # type: ignore[attr-defined]
            await svc.remove_value("gemini_generation_model", "gemini-3.1-pro-preview")

        client.portal.call(_remove)

        html = client.get(f"/prompts/{pid}").text
        assert "gemini-3.1-pro-preview" in html  # saved model still offered
