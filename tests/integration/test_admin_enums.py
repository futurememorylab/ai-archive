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
        assert "Gemini generation models" in r.text  # enum tab present
        # /admin now defaults to the Access & Permissions section; enum VALUES
        # live behind the enum tab (covered by test_admin_table_partial).
        assert "Access" in r.text and "Permissions" in r.text


def test_admin_table_partial(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as client:
        r = client.get("/admin/enums/gemini_generation_model")
        assert r.status_code == 200
        assert "gemini-2.5-flash-lite" in r.text


def test_add_returns_partial_and_appears(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as client:
        r = client.post(
            "/admin/enums/gemini_generation_model/values",
            data={"value": "gemini-4.0-pro"},
            headers={"HX-Request": "true"},
        )
        assert r.status_code == 200
        assert "gemini-4.0-pro" in r.text  # partial, not full page
        assert "<html" not in r.text.lower()


def test_remove_last_enabled_refused(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as client:
        async def _setup_and_get_default():
            svc = client.app.state.core_ctx.enum_service  # type: ignore[attr-defined]
            await svc.reconcile_seeds()
            return await svc.generation_default()

        default = client.portal.call(_setup_and_get_default)
        r = client.request(
            "DELETE",
            f"/admin/enums/gemini_generation_model/values/{default}",
            headers={"HX-Request": "true"},
        )
        assert r.status_code == 400
        assert "default" in r.text.lower()


def test_set_default_moves_marker(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as client:
        r = client.post(
            "/admin/enums/gemini_generation_model/values/gemini-2.5-flash/default",
            headers={"HX-Request": "true"},
        )
        assert r.status_code == 200
        # exactly one ★ in the returned partial
        assert r.text.count("★") == 1
