import importlib

import pytest
from fastapi.testclient import TestClient


def _make_app(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.delenv("CATDV_PASSWORD", raising=False)
    from backend.app import main as main_mod
    importlib.reload(main_mod)
    return main_mod.app


@pytest.fixture
def client(monkeypatch, tmp_path):
    with TestClient(_make_app(monkeypatch, tmp_path)) as c:
        yield c


def test_uploaded_set_renders_filename(client):
    up = client.post(
        "/api/studio/uploads",
        files={"file": ("holiday.mp4", b"x", "video/mp4")},
    ).json()
    html = client.get(f"/studio/_set?set_id={up['set_id']}").text
    assert "holiday.mp4" in html
    assert "clip-" not in html          # no archive id-fallback name
    assert f"id:{up['clip_id']}" not in html  # uploaded cards suppress the id tag


def test_uploaded_sets_list_renders(client):
    client.post("/api/studio/uploads", files={"file": ("a.mp4", b"x", "video/mp4")})
    html = client.get("/studio/_sets?source=uploaded").text
    assert "Uploads" in html             # the default set name
