"""Scene-link bridge wiring (static-asset structural checks)."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LINK_JS = (ROOT / "backend" / "app" / "static" / "studioSceneLink.js").read_text()
STORE_JS = (ROOT / "backend" / "app" / "static" / "studioStore.js").read_text()
STUDIO_HTML = (ROOT / "backend" / "app" / "templates" / "pages" / "studio.html").read_text()


def test_store_has_selected_scene_key():
    assert "selectedSceneKey" in STORE_JS


def test_bridge_toggles_is_linked_by_data_scene_key():
    assert "data-scene-key" in LINK_JS
    assert "is-linked" in LINK_JS
    assert "selectedSceneKey" in LINK_JS


def test_studio_page_includes_bridge_script():
    assert "studioSceneLink.js" in STUDIO_HTML
