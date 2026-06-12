"""The in-app Shut-down button is hidden in the cloud deployment
(app_env == "prod") because Cloud Run owns the lifecycle; it is present in
plain dev and disabled under --reload."""

from types import SimpleNamespace

from backend.app.routes.pages.templates import templates


def _render(app_env: str, dev_reload: bool) -> str:
    settings = SimpleNamespace(
        app_env=app_env,
        dev_reload=dev_reload,
        catdv_catalog_id=881507,
        catdv_connect_mode="manual",
    )
    state = SimpleNamespace(
        core_ctx=SimpleNamespace(settings=settings),
        live_ctx=None,
    )
    request = SimpleNamespace(
        app=SimpleNamespace(state=state),
        url=SimpleNamespace(netloc="localhost:8765"),
    )
    return templates.env.get_template("pages/_topbar_pills.html").render(request=request)


def test_button_hidden_in_cloud():
    html = _render(app_env="prod", dev_reload=False)
    assert "shutdown-btn" not in html
    assert "/api/connection/shutdown" not in html


def test_button_present_in_dev():
    html = _render(app_env="dev", dev_reload=False)
    assert "shutdown-btn" in html
    assert "/api/connection/shutdown" in html


def test_button_disabled_in_reload():
    html = _render(app_env="dev", dev_reload=True)
    assert "shutdown-btn" in html
    assert "disabled" in html
