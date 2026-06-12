"""CATALOG + READ-ONLY moved into the connection dropdown footer; the standalone
topbar env-pills for them are gone. The DEV env pill stays."""


def test_topbar_pills_has_no_standalone_catalog_or_readonly():
    # The partial reads request.app.state at render time, so assert on source:
    # the standalone CATALOG/READ-ONLY env-pills must be removed (they now live
    # in the connection dropdown footer).
    src = open("backend/app/templates/pages/_topbar_pills.html").read()
    assert "CATALOG {{ _settings.catdv_catalog_id }}" not in src
    assert ">READ-ONLY<" not in src
    # The DEV env pill must remain.
    assert "DEV ·" in src
