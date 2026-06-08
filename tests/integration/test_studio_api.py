"""Integration tests for /api/studio routes."""

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


def test_create_list_rename_delete_set(client):
    r = client.post("/api/studio/sets", json={"name": "edge_cases"})
    assert r.status_code == 201
    sid = r.json()["id"]

    r = client.get("/api/studio/sets")
    assert r.status_code == 200
    sets = r.json()
    assert len(sets) == 1
    assert sets[0]["name"] == "edge_cases"
    assert sets[0]["clip_count"] == 0

    r = client.patch(f"/api/studio/sets/{sid}", json={"name": "rare"})
    assert r.status_code == 200
    r = client.get("/api/studio/sets")
    assert r.json()[0]["name"] == "rare"

    r = client.delete(f"/api/studio/sets/{sid}")
    assert r.status_code == 204
    r = client.get("/api/studio/sets")
    assert r.json() == []


def test_duplicate_set_name_rejected(client):
    client.post("/api/studio/sets", json={"name": "x"})
    r = client.post("/api/studio/sets", json={"name": "x"})
    assert r.status_code == 409


def test_uploaded_source_is_separate_list(client):
    client.post("/api/studio/sets", json={"name": "a"})  # default source=archive
    r = client.get("/api/studio/sets?source=uploaded")
    assert r.status_code == 200
    assert r.json() == []
    r = client.get("/api/studio/sets?source=archive")
    assert [s["name"] for s in r.json()] == ["a"]


def test_add_list_remove_clips(client):
    r = client.post("/api/studio/sets", json={"name": "f"})
    sid = r.json()["id"]

    r = client.post(f"/api/studio/sets/{sid}/clips", json={"clip_ids": [12041, 12042]})
    assert r.status_code == 200
    assert r.json()["added"] == 2

    r = client.get(f"/api/studio/sets/{sid}/clips")
    assert r.status_code == 200
    clips = r.json()
    assert sorted(c["clip_id"] for c in clips) == [12041, 12042]

    r = client.delete(f"/api/studio/sets/{sid}/clips/12041")
    assert r.status_code == 204

    r = client.get(f"/api/studio/sets/{sid}/clips")
    assert [c["clip_id"] for c in r.json()] == [12042]


def test_create_run_persists_pending_studio_run_and_job(client):
    # 1. Create a prompt
    r = client.post(
        "/api/prompts",
        json={
            "name": "studio-e2e",
            "body": "do x",
            "target_map": {},
            "output_schema": {},
            "model": "gemini-2.5-pro",
        },
    )
    assert r.status_code == 201
    pid = r.json()["id"]
    r = client.get(f"/api/prompts/{pid}")
    assert r.status_code == 200
    vid = r.json()["latest_version_id"]

    # 2. Create a studio run with explicit model override
    r = client.post(
        "/api/studio/runs",
        json={"prompt_version_id": vid, "clip_id": 42, "model": "gemini-2.5-flash"},
    )
    assert r.status_code == 201
    body = r.json()
    assert "run_id" in body and "job_id" in body
    run_id = body["run_id"]

    # 3. Run exists, status=pending, model recorded
    r = client.get(f"/api/studio/runs/{run_id}")
    assert r.status_code == 200
    run = r.json()
    assert run["status"] == "pending"
    assert run["model"] == "gemini-2.5-flash"

    # 4. Latest lookup returns the same run
    r = client.get(
        "/api/studio/runs",
        params={"prompt_version_id": vid, "clip_id": 42, "latest": 1},
    )
    assert r.status_code == 200
    assert r.json()["id"] == run_id


def test_studio_e2e_happy_path(client):
    """End-to-end integration test: prompt, set, clip, run, studio page."""
    # 1. Create a prompt
    r = client.post(
        "/api/prompts",
        json={
            "name": "e2e-test-prompt",
            "body": "do x",
            "target_map": {},
            "output_schema": {},
            "model": "gemini-2.5-pro",
        },
    )
    assert r.status_code == 201
    pid = r.json()["id"]
    r = client.get(f"/api/prompts/{pid}")
    assert r.status_code == 200
    vid = r.json()["latest_version_id"]

    # 2. Create a set
    r = client.post("/api/studio/sets", json={"name": "e2e-set"})
    assert r.status_code == 201
    fid = r.json()["id"]

    # 3. Add a clip to the set
    r = client.post(f"/api/studio/sets/{fid}/clips", json={"clip_ids": [42]})
    assert r.status_code == 200
    assert r.json()["added"] == 1

    # 4. List sets, find this set, assert clip_count == 1
    r = client.get("/api/studio/sets")
    assert r.status_code == 200
    f = next(x for x in r.json() if x["id"] == fid)
    assert f["clip_count"] == 1
    assert f["name"] == "e2e-set"

    # 5. POST a studio run with explicit model override
    r = client.post(
        "/api/studio/runs",
        json={"prompt_version_id": vid, "clip_id": 42, "model": "gemini-2.5-flash"},
    )
    assert r.status_code == 201
    run_id = r.json()["run_id"]

    # 6. Get the run, assert status='pending', model='gemini-2.5-flash'
    r = client.get(f"/api/studio/runs/{run_id}")
    assert r.status_code == 200
    run = r.json()
    assert run["status"] == "pending"
    assert run["model"] == "gemini-2.5-flash"

    # 7. Latest lookup returns it
    r = client.get(
        "/api/studio/runs",
        params={"prompt_version_id": vid, "clip_id": 42, "latest": 1},
    )
    assert r.status_code == 200
    assert r.json()["id"] == run_id

    # 8. GET /studio?prompt_id={pid} and assert studio page renders 200
    r = client.get(f"/studio?prompt_id={pid}")
    assert r.status_code == 200
    # The navigator now has source tabs; offline (no archive) the index
    # renders the Uploaded stub, so the archive set list is served via the
    # canonical /studio/_sets partial — assert the set name shows there.
    r = client.get("/studio/_sets?source=archive")
    assert r.status_code == 200
    assert "e2e-set" in r.text
