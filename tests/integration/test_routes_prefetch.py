import pytest
from fastapi.testclient import TestClient

from backend.app.main import app


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("CATDV_BASE_URL", "http://localhost/none")
    monkeypatch.setenv("CATDV_CATALOG_ID", "0")
    monkeypatch.setenv("GCP_PROJECT_ID", "x")
    monkeypatch.setenv("GCS_BUCKET_NAME", "x")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("APP_ENV", "dev")
    with TestClient(app) as c:
        yield c


def test_prefetch_enqueue_single(client):
    r = client.post(
        "/api/cache/prefetch",
        json={"clip_keys": [["catdv", "42"]]},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["enqueued"] == 1
    assert isinstance(body["ids"], list) and len(body["ids"]) == 1


def test_prefetch_enqueue_bulk_idempotent(client):
    r = client.post(
        "/api/cache/prefetch",
        json={"clip_keys": [["catdv", "1"], ["catdv", "2"], ["catdv", "1"]]},
    )
    assert r.status_code == 200
    body = r.json()
    # 3 enqueues but only 2 distinct active rows
    assert body["enqueued"] == 3
    assert len(set(body["ids"])) == 2


def test_queue_list_returns_rows(client):
    client.post("/api/cache/prefetch", json={"clip_keys": [["catdv", "7"]]})
    r = client.get("/api/cache/prefetch/queue")
    assert r.status_code == 200
    body = r.json()
    assert "active" in body and "recent" in body and "counts" in body
    active_keys = [(row["provider_id"], row["provider_clip_id"]) for row in body["active"]]
    assert ("catdv", "7") in active_keys


def test_cancel_queued_row(client):
    rid = client.post("/api/cache/prefetch", json={"clip_keys": [["catdv", "99"]]}).json()["ids"][0]
    r = client.post(f"/api/cache/prefetch/{rid}/cancel")
    assert r.status_code == 200
    assert r.json()["cancelled"] is True
