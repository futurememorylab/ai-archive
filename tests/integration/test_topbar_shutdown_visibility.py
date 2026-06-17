"""The session-end control in the topbar user menu is environment-aware:
Log out only with real auth (cloud/IAP); Shut down only on a local instance
(disabled under --reload). They are mutually exclusive."""

from types import SimpleNamespace

from backend.app.routes.pages.templates import templates


def _render(*, app_env: str, auth_backend: str, dev_reload: bool) -> str:
    settings = SimpleNamespace(
        app_env=app_env,
        auth_backend=auth_backend,
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
        state=SimpleNamespace(
            current_user=SimpleNamespace(email="dev@localhost", is_authenticated=True)
        ),
        headers={},
    )
    return templates.env.get_template("pages/_topbar_pills.html").render(request=request)


def test_cloud_shows_logout_not_shutdown():
    html = _render(app_env="prod", auth_backend="iap", dev_reload=False)
    assert "CLEAR_LOGIN_COOKIE" in html  # Log out present
    assert "/api/connection/shutdown" not in html  # Shut down absent


def test_local_shows_shutdown_not_logout():
    html = _render(app_env="dev", auth_backend="dev", dev_reload=False)
    assert "/api/connection/shutdown" in html  # Shut down present
    assert "CLEAR_LOGIN_COOKIE" not in html  # Log out absent


def test_local_reload_disables_shutdown():
    html = _render(app_env="dev", auth_backend="dev", dev_reload=True)
    assert "user-menu-trigger" in html
    assert "disabled" in html
    assert "/api/connection/shutdown" not in html  # disabled item carries no hx-post


def test_no_standalone_logout_or_env_pill():
    html = _render(app_env="dev", auth_backend="dev", dev_reload=False)
    assert "topbar-logout" not in html  # logout folded into the menu
    assert "shutdown-btn" not in html  # old standalone button gone
