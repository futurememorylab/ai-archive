"""Topbar catalog picker + switch endpoint (#124). App boots offline
(live_ctx None) so everything here reads the DB-cached list."""

import asyncio
import importlib
import re

import pytest
from fastapi.testclient import TestClient

from backend.app.archive.model import CatalogInfo
from backend.app.services.active_catalog import COOKIE


@pytest.fixture
def client(monkeypatch, tmp_path):
    for k, v in {
        "APP_ENV": "dev",
        "CATDV_BASE_URL": "http://localhost:0",
        "CATDV_USERNAME": "",
        "CATDV_PASSWORD": "p",
        "CATDV_CATALOG_ID": "881507",
        "GCP_PROJECT_ID": "p",
        "GCS_BUCKET_NAME": "b",
        "PROXY_SOURCE": "rest",
        "DATA_DIR": str(tmp_path),
    }.items():
        monkeypatch.setenv(k, v)
    from backend.app import main as main_mod

    importlib.reload(main_mod)
    with TestClient(main_mod.app) as c:
        core = main_mod.app.state.core_ctx
        assert main_mod.app.state.live_ctx is None
        asyncio.run(
            core.catalogs_repo.replace_all(
                core.db,
                provider_id="catdv",
                catalogs=[
                    CatalogInfo("881507", "AI katalog"),
                    CatalogInfo("913722", "vyber pro AI", True),
                ],
                fetched_at="2000-01-01T00:00:00+00:00",
            )
        )
        yield c


def _option(html: str, cid: str) -> str:
    m = re.search(rf'<button[^>]*data-test="catalog-option-{cid}"[^>]*>.*?</button>', html, re.S)
    assert m, f"no option for {cid}"
    return m.group(0)


def test_picker_lists_cached_catalogs_offline(client):
    r = client.get("/ui/catalog-picker")
    assert r.status_code == 200, r.text
    assert r.headers["cache-control"] == "no-store"
    assert "is-current" in _option(r.text, "881507")
    assert "is-current" not in _option(r.text, "913722")
    assert "read-only" in _option(r.text, "913722")
    assert 'data-test="catalog-picker-trigger"' in r.text


def test_picker_marks_cookie_catalog_current(client):
    client.cookies.set(COOKIE, "913722")
    r = client.get("/ui/catalog-picker")
    assert "is-current" in _option(r.text, "913722")
    assert "is-current" not in _option(r.text, "881507")


def test_post_active_sets_cookie_and_redirects(client):
    r = client.post("/api/catalog/active", data={"catalog_id": "913722"}, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/"
    sc = r.headers["set-cookie"]
    assert f"{COOKIE}=913722" in sc
    assert "HttpOnly" in sc and "samesite=lax" in sc.lower() and "Max-Age=31536000" in sc


@pytest.mark.parametrize("bad", ["999", "abc", ""])
def test_post_invalid_catalog_400(client, bad):
    r = client.post("/api/catalog/active", data={"catalog_id": bad}, follow_redirects=False)
    assert r.status_code == 400
    assert "set-cookie" not in r.headers


def test_layout_has_picker_container(client):
    r = client.get("/prompts")
    assert r.status_code == 200, r.text
    assert 'id="catalog-picker"' in r.text
    assert 'hx-get="/ui/catalog-picker"' in r.text


def test_post_non_ascii_digit_400(client):
    r = client.post("/api/catalog/active", data={"catalog_id": "²"}, follow_redirects=False)
    assert r.status_code == 400
