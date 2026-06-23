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


async def _seed_prompt(db_path, *, media_kind: str = "any") -> None:
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
            media_kind=media_kind,
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


def test_prompts_view_rows_carry_media_kind(monkeypatch, tmp_path):
    """_prompts_view must expose each prompt's media_kind so openCalibrate can
    auto-filter the picker to it (image/video)."""
    import asyncio as _asyncio

    from backend.app.routes.pages.admin import _prompts_view

    with _client(monkeypatch, tmp_path) as client:
        asyncio.run(_seed_prompt(tmp_path / "app.db", media_kind="image"))
        ctx = client.app.state.core_ctx
        view = _asyncio.run(_prompts_view(ctx))
        rows = [r for r in view["rows"] if r["prompt_name"] == "MyPrompt"]
        assert rows, "seeded prompt not in view"
        assert rows[0]["media_kind"] == "image"


async def _seed_telemetry(db_path, *, prompt_version_id: int) -> None:
    """Insert ok run_telemetry rows for a prompt version so the usage columns
    have something to aggregate."""
    import aiosqlite

    from backend.app.models.telemetry import RunTelemetryRecord
    from backend.app.repositories.run_telemetry import RunTelemetryRepo

    async with aiosqlite.connect(db_path) as conn:
        repo = RunTelemetryRepo()
        for _ in range(3):
            await repo.insert(
                conn,
                RunTelemetryRecord(
                    occurred_at="2026-06-07T12:00:00+00:00",
                    install_id="inst-1",
                    kind="studio",
                    model="gemini-2.5-flash-lite",
                    status="ok",
                    media_kind="video+audio",
                    media_duration_secs=60.0,
                    prompt_version_id=prompt_version_id,
                    cost_usd=0.20,
                    est_cost_usd_p50=0.15,
                ),
            )


def test_prompts_tab_shows_usage_columns(monkeypatch, tmp_path):
    """Seeded telemetry surfaces in the Annotated / Est. cost / Actual cost
    columns of the prompt-version row."""
    with _client(monkeypatch, tmp_path) as client:
        asyncio.run(_seed_prompt(tmp_path / "app.db"))
        # Find the seeded version's id, then attach telemetry to it.
        from backend.app.routes.pages.admin import _prompts_view

        ctx = client.app.state.core_ctx
        view = asyncio.run(_prompts_view(ctx))
        vid = next(r["version_id"] for r in view["rows"] if r["prompt_name"] == "MyPrompt")
        asyncio.run(_seed_telemetry(tmp_path / "app.db", prompt_version_id=vid))

        r = client.get("/admin/prompts")
        assert r.status_code == 200
        # 3 runs · 3m (180s) annotated footage.
        assert "3 runs · 3m" in r.text
        # Est. = 3 × 0.15 = $0.45; Actual = 3 × 0.20 = $0.60.
        assert "$0.45" in r.text
        assert "$0.60" in r.text


def test_prompts_tab_calibrate_button_passes_media_kind(monkeypatch, tmp_path):
    """The Calibrate button forwards the prompt's media_kind as the 3rd arg to
    openCalibrate, so the picker is filtered to that kind."""
    with _client(monkeypatch, tmp_path) as client:
        asyncio.run(_seed_prompt(tmp_path / "app.db", media_kind="image"))
        r = client.get("/admin/prompts")
        assert r.status_code == 200
        assert "'image')\">Calibrate" in r.text
