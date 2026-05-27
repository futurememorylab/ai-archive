"""_anno_panels.html gains an optional show_history flag (default True).
Clip detail leaves it unset → History tab still renders. Studio will pass
show_history=False in a later task."""

import importlib

import pytest
from fastapi.testclient import TestClient


def _setenv(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.delenv("CATDV_PASSWORD", raising=False)


@pytest.fixture
def client(monkeypatch, tmp_path):
    _setenv(monkeypatch, tmp_path)
    from backend.app import main as main_mod
    importlib.reload(main_mod)
    with TestClient(main_mod.app) as c:
        yield c


def test_anno_panels_show_history_default_true_clip_detail_unchanged(client):
    r = client.get("/clips/12041")
    if r.status_code != 200:
        pytest.skip("clip not available in offline test env")
    assert "tab === 'history'" in r.text


def _render_review_panels(**panels_overrides):
    """Render _anno_panels.html with a review marker item using the app's
    configured Jinja env (so the `smpte` global resolves)."""
    from backend.app.routes.pages.templates import templates

    marker = {
        "item_id": 42,
        "name": "Wide establishing shot",
        "category": "shot",
        "description": "An establishing wide.",
        "in_secs": 1.0,
        "out_secs": 3.0,
        "decision": "pending",
    }
    panels = {
        "markers": [marker],
        "fields": [],
        "notes": None,
        "big_notes": None,
        "fps": 25.0,
        "note_items": None,
    }
    panels.update(panels_overrides)
    tmpl = templates.env.get_template("pages/_anno_panels.html")
    return tmpl.render(panels=panels, scope="draft", clip=None, show_history=True)


def test_review_items_render_readonly_with_edit_gate():
    html = _render_review_panels()
    assert "editingItemId" in html      # edit toggle wired to player-root state
    assert "ri-editor" in html          # the gated editor container exists
    assert 'class="ri-accept' in html   # keep-checkbox preserved (accent-styled)


def test_review_marker_preserves_persistence_hooks():
    html = _render_review_panels()
    # JS persistence hooks review.js relies on must survive the restructure.
    assert 'class="ri-row ri-marker"' in html   # _decideMarker closest('.ri-marker')
    assert 'data-k="name"' in html
    assert 'data-k="in"' in html and 'data-k="out"' in html
    assert "ri-mfield" in html
    # read-only row shows display text + SMPTE timecode
    assert 'class="ri-text"' in html
    assert 'class="ri-tc' in html
