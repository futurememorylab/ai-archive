"""Always-present topbar spend pill: GET /ui/usage-pill renders the
current-month spend and colours the .pill by status (ok → plain / warn →
.pill.warn / over → .pill.bad). CoreCtx / DB-only, so it renders with
live_ctx=None (offline-safe) and never crashes the topbar.

Harness mirrors test_admin_usage.py: an on-disk reload-and-seed in-process
app driven through the TestClient.
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
    return now.replace(day=day, hour=12, minute=0, second=0, microsecond=0).isoformat()


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


# ── spend always shown ───────────────────────────────────────────────────


def test_usage_pill_renders_spend_no_budget(monkeypatch, tmp_path):
    """Offline + no budget → the pill shows this-month spend with a plain
    .pill class and a '$X this month' title (no budget figure)."""
    with _client(monkeypatch, tmp_path) as client:
        client.app.state.live_ctx = None
        asyncio.run(_seed_telemetry(tmp_path / "app.db", cost_usd=4.00))
        asyncio.run(_seed_telemetry(tmp_path / "app.db", cost_usd=6.00))

        r = client.get("/ui/usage-pill")
        assert r.status_code == 200
        assert "$10.00" in r.text  # 4 + 6
        # No budget → status 'none' → plain pill, no warn/bad modifier.
        assert 'class="pill"' in r.text
        assert "pill warn" not in r.text
        assert "pill bad" not in r.text
        assert "this month" in r.text


def test_usage_pill_zero_spend_renders(monkeypatch, tmp_path):
    """No telemetry at all → spend $0.00 still renders (the pill is ALWAYS
    present), never a crash."""
    with _client(monkeypatch, tmp_path) as client:
        client.app.state.live_ctx = None
        r = client.get("/ui/usage-pill")
        assert r.status_code == 200
        assert "$0.00" in r.text
        assert 'class="pill"' in r.text


# ── status → class mapping ───────────────────────────────────────────────


def _set_budget(client, usd):
    r = client.post("/admin/usage/budget", data={"budget_usd": str(usd)})
    assert r.status_code == 200


def test_usage_pill_ok_under_80pct(monkeypatch, tmp_path):
    """Spend at 50% of budget → status 'ok' → plain .pill (no warn/bad)."""
    with _client(monkeypatch, tmp_path) as client:
        client.app.state.live_ctx = None
        asyncio.run(_seed_telemetry(tmp_path / "app.db", cost_usd=5.00))
        _set_budget(client, 10)  # 5 / 10 = 0.5 → ok

        r = client.get("/ui/usage-pill")
        assert r.status_code == 200
        assert "$5.00" in r.text
        assert 'class="pill"' in r.text
        assert "pill warn" not in r.text
        assert "pill bad" not in r.text
        # Budget present → title carries the "of $Y this month (NN%)" form.
        assert "of $10.00 this month" in r.text
        assert "(50%)" in r.text


def test_usage_pill_warn_at_85pct(monkeypatch, tmp_path):
    """Spend at 85% of budget (0.8–1.0) → status 'warn' → .pill.warn."""
    with _client(monkeypatch, tmp_path) as client:
        client.app.state.live_ctx = None
        asyncio.run(_seed_telemetry(tmp_path / "app.db", cost_usd=8.50))
        _set_budget(client, 10)  # 8.5 / 10 = 0.85 → warn

        r = client.get("/ui/usage-pill")
        assert r.status_code == 200
        assert "$8.50" in r.text
        assert 'class="pill warn"' in r.text
        assert "pill bad" not in r.text


def test_usage_pill_over_at_120pct(monkeypatch, tmp_path):
    """Spend at 120% of budget (>1.0) → status 'over' → .pill.bad."""
    with _client(monkeypatch, tmp_path) as client:
        client.app.state.live_ctx = None
        asyncio.run(_seed_telemetry(tmp_path / "app.db", cost_usd=12.00))
        _set_budget(client, 10)  # 12 / 10 = 1.2 → over

        r = client.get("/ui/usage-pill")
        assert r.status_code == 200
        assert "$12.00" in r.text
        assert 'class="pill bad"' in r.text
        assert "pill warn" not in r.text
        assert "(120%)" in r.text


# ── present on a full page render (rides topbar_counts) ──────────────────


def test_usage_pill_present_on_full_page(monkeypatch, tmp_path):
    """The pill container renders on EVERY page (the pillset is in layout.html),
    and on first paint the inner spend is rendered inline from topbar_counts —
    no flicker waiting for the poll."""
    with _client(monkeypatch, tmp_path) as client:
        client.app.state.live_ctx = None
        asyncio.run(_seed_telemetry(tmp_path / "app.db", cost_usd=3.00))

        r = client.get("/admin")
        assert r.status_code == 200
        # Stable poll container present.
        assert 'id="usage-pill"' in r.text
        assert 'hx-get="/ui/usage-pill"' in r.text
        # Inline first-paint spend from topbar_counts (not a blank container).
        assert "$3.00" in r.text
