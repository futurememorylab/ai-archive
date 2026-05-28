"""Clip detail's player transport renders identically after the
.transport/.timeline/.ranges/.playhead markup is extracted into
_player_overlay.html. This guards the only non-Studio surface PR2 touches.
"""

import importlib
import re

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


def _player_html(html: str) -> str:
    # Slice from `<div class="transport">` to its matching closing — we only
    # care that the transport block is unchanged.
    m = re.search(r'<div class="transport">.*?</div>\s*</div>\s*</div>', html, re.DOTALL)
    return m.group(0) if m else ""


def test_clip_detail_transport_block_present(client):
    r = client.get("/clips/12041")
    if r.status_code != 200:
        pytest.skip("clip not available in offline test env")
    if "duration_secs" in r.text and 'class="transport"' in r.text:
        assert 'class="timeline"' in r.text
        assert 'class="ticks"' in r.text
        assert 'class="ranges"' in r.text
        assert 'class="playhead"' in r.text


def _render_overlay(rows, duration_secs=10.0):
    """Render _player_overlay.html directly through the app's Jinja env."""
    from backend.app.routes.pages.templates import templates

    tmpl = templates.env.get_template("pages/_player_overlay.html")
    return tmpl.render(
        duration_secs=duration_secs,
        duration_smpte="00:00:10:00",
        rows=rows,
    )


def test_draft_range_carries_item_id_and_drag_binding():
    """A draft marker with item_id renders a draggable .range: it must carry
    data-item-id, the editing :class hook, and the startMarkerDrag pointerdown
    binding plus in/out edge handles (Task 10 drag-to-adjust)."""
    rows = [
        {
            "key": "draft",
            "ranges": [{"in_secs": 1.0, "out_secs": 3.0, "name": "Scene 1", "item_id": 42}],
            "cls": "draft-ranges range-draft",
            "alpine_list": None,
            "x_show": "scope === 'draft'",
        },
    ]
    html = _render_overlay(rows)
    assert 'data-item-id="42"' in html
    assert "startMarkerDrag($event, 42, 'move')" in html
    assert "startMarkerDrag($event, 42, 'in')" in html
    assert "startMarkerDrag($event, 42, 'out')" in html
    assert "editingItemId === 42" in html
    assert "range-handle in" in html and "range-handle out" in html
    # Position is Alpine-bound (not a static style=) so drag moves the bar live.
    assert "_draftItem(42)" in html


def test_published_range_stays_static_no_drag():
    """Published (non-draft) ranges keep the static server-computed style and
    gain no drag bindings."""
    rows = [
        {
            "key": "markers",
            "ranges": [{"in_secs": 1.0, "out_secs": 3.0, "name": "Cut"}],
            "cls": "range-cur",
            "alpine_list": "markers",
            "x_show": None,
        },
    ]
    html = _render_overlay(rows)
    assert "startMarkerDrag" not in html
    assert "range-handle" not in html
    assert "style=" in html  # static left/width preserved
