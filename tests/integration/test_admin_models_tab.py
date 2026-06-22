"""Admin Models tab — list + edit per-model Gemini rates (pricing-to-DB PR1)."""

import asyncio
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

    # Durability: a fresh connection to the on-disk DB must see the committed row.
    import aiosqlite

    async def _check():
        async with aiosqlite.connect(tmp_path / "app.db") as fresh:
            cur = await fresh.execute(
                "SELECT input_text_video_image_per_1m FROM model_config WHERE model = ?",
                ("gemini-2.5-flash-lite",),
            )
            (persisted,) = await cur.fetchone()
        return persisted

    persisted = asyncio.run(_check())
    assert persisted == 0.20


def test_negative_rate_rejected(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as client:
        r = client.post(
            "/admin/models/gemini-2.5-flash-lite/rates",
            data={
                "input_text_video_image_per_1m": -1.0,
                "input_audio_per_1m": 0.30,
                "input_cached_per_1m": 0.01,
                "output_per_1m": 0.40,
            },
            headers={"HX-Request": "true"},
        )
        assert r.status_code == 422


def test_unknown_model_404(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as client:
        r = client.post(
            "/admin/models/no-such-model/rates",
            data={
                "input_text_video_image_per_1m": 0.20,
                "input_audio_per_1m": 0.30,
                "input_cached_per_1m": 0.01,
                "output_per_1m": 0.40,
            },
            headers={"HX-Request": "true"},
        )
        assert r.status_code == 404
