"""Admin Models tab — list + edit per-model Gemini rates (pricing-to-DB PR2)."""

import importlib

import pytest
from fastapi.testclient import TestClient


def _client(monkeypatch, tmp_path) -> TestClient:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from backend.app import main as main_mod

    importlib.reload(main_mod)
    return TestClient(main_mod.app)


@pytest.fixture(autouse=True)
def _reset_cards():
    from backend.app.services import pricing

    yield
    pricing.set_rate_cards(pricing.SEED_RATE_CARDS)


def test_models_tab_lists_seeded_models(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as client:
        r = client.get("/admin/models")
        assert r.status_code == 200
        assert "gemini-2.5-flash-lite" in r.text


def test_edit_rates_persists_and_updates_cache(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as client:
        r = client.post(
            "/admin/models/gemini-2.5-flash-lite/rates",
            data={
                "input_text_video_image_per_1m": 0.20,
                "input_audio_per_1m": 0.30,
                "input_cached_per_1m": 0.01,
                "output_per_1m": 0.40,
            },
            headers={"HX-Request": "true"},
        )
        assert r.status_code == 200

    from backend.app.services.pricing import rate_cards

    assert rate_cards()["gemini-2.5-flash-lite"].input_text_video_image_per_1m == 0.20
