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
    assert f"clip-{up['clip_id']}" not in html  # no archive id-fallback name
    assert f"id:{up['clip_id']}" not in html  # uploaded cards suppress the id tag


def test_uploaded_sets_list_renders(client):
    client.post("/api/studio/uploads", files={"file": ("a.mp4", b"x", "video/mp4")})
    html = client.get("/studio/_sets?source=uploaded").text
    assert "Uploads" in html             # the default set name


def test_uploaded_set_body_shows_dropzone_not_archive_button(client):
    up = client.post(
        "/api/studio/uploads",
        files={"file": ("holiday.mp4", b"x", "video/mp4")},
    ).json()
    html = client.get(f"/studio/_set?set_id={up['set_id']}").text
    # Uploaded sets get a per-set dropzone wired to this set id…
    assert "studio-dropzone" in html
    assert f"uploadClips({up['set_id']})" in html
    # …and never the archive picker button.
    assert "Add from archive" not in html
    assert "studio-add-from-archive" not in html


def test_archive_set_body_shows_archive_button_not_dropzone(client):
    sid = client.post("/api/studio/sets?source=archive", json={"name": "arc"}).json()[
        "id"
    ]
    html = client.get(f"/studio/_set?set_id={sid}").text
    assert "Add from archive" in html
    assert "studio-dropzone" not in html


def test_empty_uploaded_set_shows_dropzone_without_empty_placeholder(client):
    # An empty uploaded set is "empty" by the absence of cards; the dropzone is
    # its only content. The "Empty set." placeholder would be redundant noise.
    sid = client.post("/api/studio/sets?source=uploaded", json={"name": "up"}).json()[
        "id"
    ]
    html = client.get(f"/studio/_set?set_id={sid}").text
    assert "studio-dropzone" in html
    assert "Empty set." not in html


def test_empty_archive_set_still_shows_empty_placeholder(client):
    sid = client.post("/api/studio/sets?source=archive", json={"name": "arc2"}).json()[
        "id"
    ]
    html = client.get(f"/studio/_set?set_id={sid}").text
    assert "Empty set." in html
