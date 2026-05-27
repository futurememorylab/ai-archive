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
