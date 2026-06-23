"""Admin Models tab — merged Gemini catalog + per-model pricing.

The single "Gemini models" tab's spine is the model catalog (the editable
enum); each model is joined to its model_config rate card (which may be
absent). Adding a model adds it to the catalog; saving rates for a model with
no card CREATES the card.
"""

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


def test_lists_full_catalog_including_unpriced(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as client:
        # Add a synthetic catalog entry that has no rate card — all seed models
        # are now priced, so we inject one via the admin endpoint.
        client.post(
            "/admin/models",
            data={"model": "gemini-unpriced-test-model"},
            headers={"HX-Request": "true"},
        )
        r = client.get("/admin/models")
        assert r.status_code == 200
        # a priced catalog model
        assert "gemini-2.5-flash-lite" in r.text
        # an unpriced catalog model, flagged
        assert "gemini-unpriced-test-model" in r.text
        assert "no rate card" in r.text
        # resolution help text explaining low/medium/high semantics
        assert "still images only" in r.text


def test_edit_existing_rate_persists_and_updates_cache(monkeypatch, tmp_path):
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


def test_set_rates_creates_card_for_unpriced_model(monkeypatch, tmp_path):
    # Use a synthetic model id added via the admin endpoint — all seed catalog
    # models are now priced, so we need a fresh cardless entry to test this path.
    _SYNTHETIC = "gemini-unpriced-test-model"
    with _client(monkeypatch, tmp_path) as client:
        # Add the synthetic model to the catalog (no rate card yet).
        client.post(
            "/admin/models",
            data={"model": _SYNTHETIC},
            headers={"HX-Request": "true"},
        )
        before = client.get("/admin/models")
        assert _SYNTHETIC in before.text
        assert "no rate card" in before.text

        r = client.post(
            f"/admin/models/{_SYNTHETIC}/rates",
            data={
                "input_text_video_image_per_1m": 0.15,
                "input_audio_per_1m": 0.25,
                "input_cached_per_1m": 0.02,
                "output_per_1m": 0.55,
            },
            headers={"HX-Request": "true"},
        )
        assert r.status_code == 200
        # The returned partial no longer flags the model as cardless.
        # (Other unpriced models may still carry the warning, so check the row.)
        assert _SYNTHETIC in r.text

        after = client.get("/admin/models")
        assert _SYNTHETIC in after.text

    from backend.app.services.pricing import rate_cards

    cards = rate_cards()
    assert _SYNTHETIC in cards
    assert cards[_SYNTHETIC].input_text_video_image_per_1m == 0.15


def test_add_model_appends_to_catalog(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as client:
        r = client.post(
            "/admin/models",
            data={"model": "gemini-test-x"},
            headers={"HX-Request": "true"},
        )
        assert r.status_code == 200
        assert "gemini-test-x" in r.text
        body = client.get("/admin/models").text
        assert "gemini-test-x" in body
        # newly-added catalog model has no rate card yet
        assert "no rate card" in body


def test_delete_model_removes_from_catalog_and_pricing(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as client:
        # give it a rate card first
        client.post(
            "/admin/models/gemini-3.5-flash/rates",
            data={
                "input_text_video_image_per_1m": 0.15,
                "input_audio_per_1m": 0.25,
                "input_cached_per_1m": 0.02,
                "output_per_1m": 0.55,
            },
            headers={"HX-Request": "true"},
        )
        r = client.request(
            "DELETE",
            "/admin/models/gemini-3.5-flash",
            headers={"HX-Request": "true"},
        )
        assert r.status_code == 200
        assert "gemini-3.5-flash" not in r.text

        body = client.get("/admin/models").text
        assert "gemini-3.5-flash" not in body

    from backend.app.services.pricing import rate_cards

    assert "gemini-3.5-flash" not in rate_cards()


def test_set_default_moves_star(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as client:
        r = client.post(
            "/admin/models/gemini-2.5-flash/default",
            headers={"HX-Request": "true"},
        )
        assert r.status_code == 200
        assert r.text.count("★") == 1
        body = client.get("/admin/models")
        assert body.text.count("★") == 1


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
            "/admin/models/not-a-model/rates",
            data={
                "input_text_video_image_per_1m": 0.20,
                "input_audio_per_1m": 0.30,
                "input_cached_per_1m": 0.01,
                "output_per_1m": 0.40,
            },
            headers={"HX-Request": "true"},
        )
        assert r.status_code == 404


def test_set_resolution_persists(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as client:
        r = client.post(
            "/admin/models/gemini-2.5-flash-lite/resolution",
            data={"media_resolution": "high"},
            headers={"HX-Request": "true"},
        )
        assert r.status_code == 200
        # The returned partial renders the select with 'high' selected.
        assert '<option value="high" selected>high</option>' in r.text

        body = client.get("/admin/models").text
        assert '<option value="high" selected>high</option>' in body

    # Durability: a fresh connection to the on-disk DB sees the committed value.
    import aiosqlite

    async def _check():
        async with aiosqlite.connect(tmp_path / "app.db") as fresh:
            cur = await fresh.execute(
                "SELECT default_media_resolution FROM model_config WHERE model = ?",
                ("gemini-2.5-flash-lite",),
            )
            (persisted,) = await cur.fetchone()
        return persisted

    persisted = asyncio.run(_check())
    assert persisted == "high"


def test_resolution_rejects_bad_value(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as client:
        r = client.post(
            "/admin/models/gemini-2.5-flash-lite/resolution",
            data={"media_resolution": "ultra"},
            headers={"HX-Request": "true"},
        )
        assert r.status_code == 422


def test_resolution_unknown_model_404(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as client:
        r = client.post(
            "/admin/models/not-a-model/resolution",
            data={"media_resolution": "high"},
            headers={"HX-Request": "true"},
        )
        assert r.status_code == 404
