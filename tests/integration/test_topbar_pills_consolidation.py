"""CATALOG + READ-ONLY moved into the connection dropdown footer; the standalone
topbar env-pills for them are gone. The DEV/PROD env label moved into the user
menu in the topbar consolidation (2026-06-17), so it's no longer a standalone
pill on the bar."""


def test_topbar_pills_has_no_standalone_catalog_or_readonly():
    # The partial reads request.app.state at render time, so assert on source.
    src = open("backend/app/templates/pages/_topbar_pills.html").read()
    assert "CATALOG {{ _settings.catdv_catalog_id }}" not in src
    assert ">READ-ONLY<" not in src
    # No standalone env-pill on the bar any more (catalog/readonly → connection
    # dropdown; DEV/PROD → user menu).
    assert "env-pill" not in src


def test_env_label_lives_in_user_menu():
    menu = open("backend/app/templates/pages/_user_menu.html").read()
    # The user menu renders the DEV/PROD env label (with the host).
    assert "DEV" in menu
    assert "PROD" in menu
