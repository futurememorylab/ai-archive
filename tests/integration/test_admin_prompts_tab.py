"""Admin Prompts tab — lists prompt versions with per-resolution calibration
results and a placeholder Calibrate button.

Seeding: uses aiosqlite directly against the app's on-disk DB (same
pattern as test_admin_models_tab.py durability tests) to create a
prompt + version via PromptsRepo, so the GET /admin/prompts render has
something to list.
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


async def _seed_prompt(db_path) -> None:
    """Insert one prompt + version into the app's on-disk DB."""
    import aiosqlite

    from backend.app.repositories.prompts import PromptsRepo

    async with aiosqlite.connect(db_path) as conn:
        repo = PromptsRepo()
        await repo.create_with_initial_version(
            conn,
            name="MyPrompt",
            description="test prompt",
            body="Identify scenes.",
            target_map={"scenes": {"kind": "markers"}},
            output_schema={"type": "object"},
            model="gemini-2.5-flash-lite",
            media_resolution="low",
        )


def test_prompts_tab_lists_versions(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as client:
        # Seed a prompt via the app DB after the app has booted (so migrations
        # have already run and the schema is ready).
        asyncio.run(_seed_prompt(tmp_path / "app.db"))

        r = client.get("/admin/prompts")
        assert r.status_code == 200
        assert "MyPrompt" in r.text
        assert "Calibrate" in r.text


def test_prompts_tab_renders_with_seeded_defaults(monkeypatch, tmp_path):
    """App seeds default prompts on boot — page still renders 200."""
    with _client(monkeypatch, tmp_path) as client:
        r = client.get("/admin/prompts")
        assert r.status_code == 200
        # Default prompts are seeded; the table is present, not the empty-state.
        assert "admin-prompts" in r.text


def test_prompts_tab_shows_version_number(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as client:
        asyncio.run(_seed_prompt(tmp_path / "app.db"))
        r = client.get("/admin/prompts")
        assert r.status_code == 200
        assert "v1" in r.text  # version_num rendered
