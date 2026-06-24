"""Admin "Usage" tab: GET renders spend + budget, POST sets / clears the
budget and re-renders, bad / negative budgets → 422, and the calibrate
estimate carries a soft-cap warning (never blocking — the Launch button
stays enabled).

Harness mirrors test_admin_calibrate.py: an on-disk reload-and-seed
in-process app. The usage surfaces are CoreCtx / DB-only, so they render
with live_ctx=None (offline-safe).
"""

import asyncio
import importlib
from datetime import UTC, datetime

from fastapi.testclient import TestClient


def _client(monkeypatch, tmp_path) -> TestClient:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from backend.app import main as main_mod

    importlib.reload(main_mod)
    return TestClient(main_mod.app)


def _now_month_iso(day: int = 5) -> str:
    """An occurred_at inside the current calendar month (UTC), so the route's
    ``datetime.now(UTC)`` window includes it."""
    now = datetime.now(UTC)
    return now.replace(
        day=day, hour=12, minute=0, second=0, microsecond=0
    ).isoformat()


async def _seed_telemetry(db_path, *, cost_usd, model="gemini-flash", occurred_at=None):
    import aiosqlite

    from backend.app.models.telemetry import RunTelemetryRecord
    from backend.app.repositories.run_telemetry import RunTelemetryRepo

    rec = RunTelemetryRecord(
        occurred_at=occurred_at or _now_month_iso(),
        install_id="inst-1",
        kind="studio",
        model=model,
        status="ok",
        media_kind="video+audio",
        media_duration_secs=10.0,
        prompt_hash="h" * 64,
        tokens_in=3000,
        tokens_out=100,
        cost_usd=cost_usd,
    )
    async with aiosqlite.connect(db_path) as conn:
        await RunTelemetryRepo().insert(conn, rec)
        await conn.commit()


async def _seed_prompt(db_path) -> int:
    import aiosqlite

    from backend.app.repositories.prompts import PromptsRepo

    async with aiosqlite.connect(db_path) as conn:
        _pid, vid = await PromptsRepo().create_with_initial_version(
            conn,
            name="CalPrompt",
            description="test prompt",
            body="Identify scenes.",
            target_map={"scenes": {"kind": "markers"}},
            output_schema={"type": "object"},
            model="gemini-2.5-flash-lite",
            media_resolution="low",
        )
        return vid


async def _seed_clip(db_path, clip_id: int, handle: str) -> None:
    import aiosqlite

    from backend.app.archive.model import CanonicalClip, MediaRef
    from backend.app.repositories.clip_cache import ClipCacheRepo

    async with aiosqlite.connect(db_path) as conn:
        clip = CanonicalClip(
            key=("catdv", str(clip_id)),
            name=f"clip {clip_id}",
            duration_secs=12.5,
            fps=25.0,
            markers=(),
            fields={},
            notes={},
            media=MediaRef(
                mime_type=None,
                size_bytes=None,
                cached_path=None,
                upstream_handle=handle,
            ),
            provider_data={},
            fetched_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        await ClipCacheRepo().upsert(conn, clip=clip, catalog_id="test-catalog")
        await conn.commit()


# ── GET /admin/usage ─────────────────────────────────────────────────────


def test_usage_tab_renders_spend_no_budget(monkeypatch, tmp_path):
    """Offline (live_ctx=None) + no budget set → renders this-month spend and a
    'no budget set' note, no crash."""
    with _client(monkeypatch, tmp_path) as client:
        client.app.state.live_ctx = None
        asyncio.run(_seed_telemetry(tmp_path / "app.db", cost_usd=4.00))
        asyncio.run(_seed_telemetry(tmp_path / "app.db", cost_usd=6.00, model="gemini-pro"))

        r = client.get("/admin/usage")
        assert r.status_code == 200
        assert "This month:" in r.text
        assert "$10.00" in r.text  # 4 + 6
        assert "no budget set" in r.text
        # by-model breakdown present
        assert "gemini-pro" in r.text


def test_usage_tab_partial_pricing_note(monkeypatch, tmp_path):
    """An un-priced (cost_usd NULL) row counts toward total but not priced, so
    the '(N of M priced)' note appears."""
    with _client(monkeypatch, tmp_path) as client:
        client.app.state.live_ctx = None
        asyncio.run(_seed_telemetry(tmp_path / "app.db", cost_usd=4.00))
        asyncio.run(_seed_telemetry(tmp_path / "app.db", cost_usd=None))

        r = client.get("/admin/usage")
        assert r.status_code == 200
        assert "1 of 2 runs priced" in r.text


def test_usage_tab_renders_budget_and_status(monkeypatch, tmp_path):
    """With a budget set below spend, the over-budget pill + budget figure render."""
    with _client(monkeypatch, tmp_path) as client:
        client.app.state.live_ctx = None
        asyncio.run(_seed_telemetry(tmp_path / "app.db", cost_usd=10.00))
        r = client.post("/admin/usage/budget", data={"budget_usd": "5"})
        assert r.status_code == 200
        # over budget → bad pill + bar
        assert "over budget" in r.text
        assert "usage-bar" in r.text
        assert "$5.00" in r.text  # the budget figure


# ── POST /admin/usage/budget ─────────────────────────────────────────────


def test_set_and_clear_budget(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as client:
        client.app.state.live_ctx = None
        asyncio.run(_seed_telemetry(tmp_path / "app.db", cost_usd=2.00))

        # Set a budget.
        r = client.post("/admin/usage/budget", data={"budget_usd": "100"})
        assert r.status_code == 200
        assert "$100.00" in r.text
        assert "within budget" in r.text  # 2 / 100 = ok

        # GET reflects the persisted budget.
        r2 = client.get("/admin/usage")
        assert "$100.00" in r2.text

        # Clear it (empty string).
        r3 = client.post("/admin/usage/budget", data={"budget_usd": ""})
        assert r3.status_code == 200
        assert "no budget set" in r3.text

        # Clear with 0 also works.
        client.post("/admin/usage/budget", data={"budget_usd": "100"})
        r4 = client.post("/admin/usage/budget", data={"budget_usd": "0"})
        assert r4.status_code == 200
        assert "no budget set" in r4.text


def test_budget_non_numeric_422(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as client:
        client.app.state.live_ctx = None
        r = client.post("/admin/usage/budget", data={"budget_usd": "abc"})
        assert r.status_code == 422


def test_budget_negative_422(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as client:
        client.app.state.live_ctx = None
        r = client.post("/admin/usage/budget", data={"budget_usd": "-5"})
        assert r.status_code == 422


# ── Soft-cap warning on the calibrate estimate (advisory, never blocks) ──


def test_calibrate_estimate_would_exceed_budget(monkeypatch, tmp_path):
    """With a low budget and a projected sweep cost over the remaining budget,
    the estimate response carries would_exceed_budget: true. The cap is SOFT —
    the status code is 200 and the Launch button binding (:disabled=!selCount())
    is untouched, so it stays enabled."""
    with _client(monkeypatch, tmp_path) as client:
        client.app.state.live_ctx = None
        vid = asyncio.run(_seed_prompt(tmp_path / "app.db"))
        asyncio.run(_seed_clip(tmp_path / "app.db", 11, "a.jpg"))

        # A tiny budget guarantees the projected sweep cost exceeds it (the
        # single-image sweep projects to a fraction of a cent).
        r0 = client.post("/admin/usage/budget", data={"budget_usd": "0.0001"})
        assert r0.status_code == 200

        r = client.post(
            f"/admin/prompts/{vid}/calibrate/estimate",
            json={"clip_ids": [11]},
        )
        assert r.status_code == 200  # NEVER blocks
        body = r.json()
        assert body["projected_cost_usd"] is not None
        assert body["projected_cost_usd"] > 0.0001  # genuinely over budget
        assert body["budget_usd"] == 0.0001
        assert "month_spend_usd" in body
        assert body["would_exceed_budget"] is True

        # The cap is advisory: the Launch button's :disabled binding is
        # exactly !selCount() (unchanged) — never gated on the budget.
        from pathlib import Path

        admin_html = Path(
            "backend/app/templates/pages/_admin_prompts_table.html"
        ).read_text()
        assert ':disabled="!selCount()"' in admin_html
        assert "would_exceed_budget" not in admin_html  # no :disabled gate added here


def test_calibrate_estimate_no_budget_no_exceed(monkeypatch, tmp_path):
    """No budget set → would_exceed_budget is false (nothing to exceed)."""
    with _client(monkeypatch, tmp_path) as client:
        client.app.state.live_ctx = None
        vid = asyncio.run(_seed_prompt(tmp_path / "app.db"))
        asyncio.run(_seed_clip(tmp_path / "app.db", 11, "a.jpg"))

        r = client.post(
            f"/admin/prompts/{vid}/calibrate/estimate",
            json={"clip_ids": [11]},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["budget_usd"] is None
        assert body["would_exceed_budget"] is False
