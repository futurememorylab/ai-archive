"""End-to-end deep-link + compare materialization tests."""

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


def _two_versions(client):
    r = client.post("/api/prompts", json={
        "name": "cmp", "media_kind": "any", "model": "gemini-2.5-pro",
        "target_map": {}, "output_schema": {}, "body": "v1",
    })
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    v1 = client.get(f"/api/prompts/{pid}").json()["latest_version_id"]
    pr = client.post(f"/api/prompts/{pid}/versions/{v1}:promote")
    assert pr.status_code == 200, pr.text
    r = client.post(f"/api/prompts/{pid}/versions", json={"from_version_id": v1})
    assert r.status_code == 201, r.text
    v2 = r.json()["id"]
    return pid, v1, v2


def test_single_card_when_no_compare_param(client):
    pid, _, _ = _two_versions(client)
    r = client.get(f"/studio?prompt_id={pid}")
    assert r.status_code == 200
    assert r.text.count('data-side="cur"') == 1
    assert 'data-side="cmp"' not in r.text


def test_two_cards_when_compare_param_set(client):
    pid, v1, v2 = _two_versions(client)
    r = client.get(f"/studio?prompt_id={pid}&version_id={v2}&compare_version_id={v1}")
    assert r.status_code == 200
    assert 'data-side="cur"' in r.text
    assert 'data-side="cmp"' in r.text
    assert f'data-version-id="{v2}"' in r.text  # cur card
    assert f'data-version-id="{v1}"' in r.text  # cmp card


def test_both_cards_bind_tabs_to_root_mode(client):
    pid, v1, v2 = _two_versions(client)
    r = client.get(f"/studio?prompt_id={pid}&version_id={v2}&compare_version_id={v1}")
    html = r.text
    # Both cards' tab buttons bind to the page-level `mode`, exposed on
    # the card scope via a getter (Alpine's $root doesn't walk past
    # nested x-data — see studio.js studioPromptCard._page).
    cur_count = html.count("mode === 'prompt'")
    out_count = html.count("mode === 'output'")
    assert cur_count >= 2  # both cards have one each
    assert out_count >= 2
    # Per-card `mode` ref should not appear in the partial.
    assert 'this.mode' not in html


def test_cmp_card_emits_cmp_diff_alpine_root(client):
    pid, v1, v2 = _two_versions(client)
    r = client.get(f"/studio?prompt_id={pid}&version_id={v2}&compare_version_id={v1}")
    html = r.text
    # Cmp card has a diff slot wired to the cmpDiff Alpine component.
    assert 'data-cmp-diff' in html
    assert 'x-data="cmpDiff"' in html


def test_cmp_diff_reacts_to_store_signals_including_save(client):
    """The diff must recompute when the store's reactive signals change —
    including a save (savedTick) — so saving a draft updates an open diff.
    The old `$root.*` reads were non-reactive (inside cmpDiff's own x-data,
    $root is the diff div), so a save left the diff showing stale text."""
    pid, v1, v2 = _two_versions(client)
    r = client.get(f"/studio?prompt_id={pid}&version_id={v2}&compare_version_id={v1}")
    html = r.text
    assert "$store.studio.savedTick" in html
    assert "$store.studio.activeVersionId" in html
    # The dead non-reactive deps are gone.
    assert "$root.activeVersionId" not in html
    assert "$root.compareVersionId" not in html
