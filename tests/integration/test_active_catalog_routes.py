"""List surfaces follow the active catalog cookie (#124)."""

import asyncio
import importlib

import pytest
from fastapi.testclient import TestClient

from backend.app.archive.model import CatalogInfo, ClipPage, ClipQuery
from backend.app.services.active_catalog import COOKIE
from tests._helpers.live_ctx import install_live_ctx


class _RecordingArchive:
    id = "catdv"

    def __init__(self):
        self.catalogs: list[str] = []

    async def list_clips(self, catalog: str, query: ClipQuery) -> ClipPage:
        self.catalogs.append(catalog)
        return ClipPage(items=(), total=0, offset=query.offset, limit=query.limit)

    async def list_catalogs(self):
        return [CatalogInfo("7", "Default cat"), CatalogInfo("913722", "vyber pro AI")]


@pytest.fixture
def client_and_archive(monkeypatch, tmp_path):
    for k, v in {
        "APP_ENV": "dev",
        "CATDV_BASE_URL": "http://localhost:0",
        "CATDV_USERNAME": "",
        "CATDV_PASSWORD": "p",
        "CATDV_CATALOG_ID": "7",
        "GCP_PROJECT_ID": "p",
        "GCS_BUCKET_NAME": "b",
        "PROXY_SOURCE": "rest",
        "DATA_DIR": str(tmp_path),
    }.items():
        monkeypatch.setenv(k, v)
    from backend.app import main as main_mod

    importlib.reload(main_mod)
    with TestClient(main_mod.app) as client:
        core = main_mod.app.state.core_ctx
        asyncio.run(
            core.catalogs_repo.replace_all(
                core.db,
                provider_id="catdv",
                catalogs=[CatalogInfo("913722", "vyber pro AI")],
                fetched_at="2999-01-01T00:00:00+00:00",
            )
        )
        archive = _RecordingArchive()
        install_live_ctx(main_mod.app, archive=archive)
        yield client, archive


def test_clips_page_uses_cookie_catalog(client_and_archive):
    client, archive = client_and_archive
    client.cookies.set(COOKIE, "913722")
    r = client.get("/")
    assert r.status_code == 200, r.text
    assert archive.catalogs[-1] == "913722"
    assert "vyber pro AI · #913722" in r.text


def test_clips_page_defaults_without_cookie(client_and_archive):
    client, archive = client_and_archive
    r = client.get("/")
    assert r.status_code == 200, r.text
    assert archive.catalogs[-1] == "7"
    assert "Catalog #7 · #7" in r.text
    assert "AI katalog" not in r.text


def test_api_catdv_clips_uses_cookie(client_and_archive):
    client, archive = client_and_archive
    client.cookies.set(COOKIE, "913722")
    assert client.get("/api/catdv/clips").status_code == 200
    assert archive.catalogs[-1] == "913722"


def test_batch_picker_rows_use_cookie(client_and_archive):
    client, archive = client_and_archive
    client.cookies.set(COOKIE, "913722")
    r = client.get("/batches/picker")
    assert r.status_code == 200, r.text
    assert archive.catalogs[-1] == "913722"
