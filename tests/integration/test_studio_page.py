"""Studio page renders and includes the expected scaffolding."""

import importlib

import pytest
from fastapi.testclient import TestClient


def _setenv(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.delenv("CATDV_PASSWORD", raising=False)


def _make_app(monkeypatch, tmp_path):
    _setenv(monkeypatch, tmp_path)
    from backend.app import main as main_mod

    importlib.reload(main_mod)
    return main_mod.app


@pytest.fixture
def client(monkeypatch, tmp_path):
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as c:
        yield c


def test_studio_page_renders(client):
    r = client.get("/studio")
    assert r.status_code == 200
    html = r.text
    # Page-level scaffolding
    assert "studio-page" in html
    assert "studio-hdr" in html
    assert "studio-body" in html


def test_studio_rail_button_present(client):
    # /prompts is archive-free and always 200 in the test environment
    r = client.get("/prompts")
    assert r.status_code == 200
    assert 'href="/studio"' in r.text


def test_studio_page_with_prompt_id_renders(client):
    # Even with an unknown prompt_id, the page must render
    r = client.get("/studio?prompt_id=999")
    assert r.status_code == 200


def _make_prompt_two_versions(client, *, name: str):
    """Create a prompt with v1 promoted to production and v2 branched as draft."""
    r = client.post("/api/prompts", json={
        "name": name, "media_kind": "any", "model": "gemini-2.5-pro",
        "target_map": {}, "output_schema": {}, "body": "v1",
    })
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    v1 = client.get(f"/api/prompts/{pid}").json()["latest_version_id"]
    # Promote v1 → production (so a fresh draft can be branched).
    pr = client.post(f"/api/prompts/{pid}/versions/{v1}:promote")
    assert pr.status_code == 200, pr.text
    # Branch v2 (draft, inherits v1's body — body content is irrelevant for these tests).
    r = client.post(f"/api/prompts/{pid}/versions", json={"from_version_id": v1})
    assert r.status_code == 201, r.text
    v2 = r.json()["id"]
    return pid, v1, v2


def test_studio_page_respects_version_id_param(client):
    pid, v1, v2 = _make_prompt_two_versions(client, name="vp")

    # Without param: default = draft = v2.
    r = client.get(f"/studio?prompt_id={pid}")
    assert r.status_code == 200
    assert f'activeVersionId: {v2}' in r.text

    # With param: pick v1 explicitly.
    r = client.get(f"/studio?prompt_id={pid}&version_id={v1}")
    assert r.status_code == 200
    assert f'activeVersionId: {v1}' in r.text


def test_studio_page_respects_compare_version_id_param(client):
    pid, v1, v2 = _make_prompt_two_versions(client, name="vp2")
    r = client.get(f"/studio?prompt_id={pid}&version_id={v2}&compare_version_id={v1}")
    assert r.status_code == 200
    assert f'compareVersionId: {v1}' in r.text


def test_studio_page_ignores_compare_equal_to_cur(client):
    pid, v1, _ = _make_prompt_two_versions(client, name="vp3")
    # Comparing a version with itself is a no-op.
    r = client.get(f"/studio?prompt_id={pid}&version_id={v1}&compare_version_id={v1}")
    assert r.status_code == 200
    assert "compareVersionId: null" in r.text


def test_studio_page_renders_source_tabs(client):
    r = client.get("/studio")
    assert r.status_code == 200
    html = r.text
    # Both tabs present; Archive is hidden only when no archive is connected.
    assert 'data-nav-source="uploaded"' in html
    # The dropzone is per-set now — it appears inside an expanded uploaded set
    # (loaded via HTMX), not on the bare page. The uploaded view shows the set
    # list shell instead.
    assert "studio-sets" in html


def test_source_param_preserves_tab(client, monkeypatch):
    # When a prompt switch carries the current tab (?source=…), the server
    # honors it instead of snapping back to the availability default.
    from backend.app.routes.pages import studio as studio_mod
    monkeypatch.setattr(studio_mod, "_archive_available", lambda req: True)
    # No source → default is archive (archive available).
    assert "studioNav('archive')" in client.get("/studio").text
    # Explicit source=uploaded is honored.
    assert "studioNav('uploaded')" in client.get("/studio?source=uploaded").text


def test_source_param_selects_matching_set_list(client, monkeypatch):
    from backend.app.routes.pages import studio as studio_mod
    monkeypatch.setattr(studio_mod, "_archive_available", lambda req: True)
    client.post("/api/studio/sets?source=archive", json={"name": "arc-set"})
    client.post("/api/studio/sets?source=uploaded", json={"name": "up-set"})
    html = client.get("/studio?source=uploaded").text
    assert "up-set" in html
    assert "arc-set" not in html


def test_open_set_id_expands_that_set(client):
    sid = client.post(
        "/api/studio/sets?source=uploaded", json={"name": "up"}
    ).json()["id"]
    html = client.get(f"/studio?source=uploaded&open_set_id={sid}").text
    # studioSets is seeded with the open set id so it auto-expands on load.
    assert f"studioSets({sid})" in html


def test_prompt_switch_link_carries_tab_and_open_set():
    from pathlib import Path
    js = Path("backend/app/static/studio.js").read_text()
    assert "u.searchParams.set('source'" in js
    assert "u.searchParams.set('open_set_id'" in js


def test_studio_nav_bulk_bar_is_clear_only(client):
    # The bulk bar holds only Clear now — running is driven by the single
    # header Run button (which targets the selection when clips are checked),
    # so there is no duplicate "Run on N clips" button in the navigator.
    html = client.get("/studio").text
    assert "studio-bulk-bar" in html
    assert "clearSelection()" in html
    assert "runOnSelectedClips()" not in html


def test_studio_sets_partial_partitions_by_source(client):
    # Create one archive set, then ask the uploaded partial — must be empty.
    client.post("/api/studio/sets", json={"name": "a"})
    r = client.get("/studio/_sets?source=uploaded")
    assert r.status_code == 200
    assert "a" not in r.text or "studio-set-card" not in r.text


def test_studio_page_exposes_uploaded_total(client):
    client.post("/api/studio/uploads", files={"file": ("a.mp4", b"x", "video/mp4")})
    html = client.get("/studio").text
    assert "data-studio-nav-body" in html
