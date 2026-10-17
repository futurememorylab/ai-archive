"""0033 rebuilds run_telemetry to accept kind='live' without losing rows or
the indexes the estimator and Usage tab rely on."""

from pathlib import Path

import pytest

from backend.app.db import open_db
from backend.app.migrations_runner import apply_migrations

MIGRATIONS = Path(__file__).resolve().parents[2] / "backend" / "migrations"


@pytest.mark.asyncio
async def test_rebuild_keeps_rows_and_indexes(tmp_path):
    staged = tmp_path / "migrations"
    staged.mkdir()
    for p in sorted(MIGRATIONS.glob("*")):
        if p.name < "0033":
            (staged / p.name).write_text(p.read_text())

    async with open_db(tmp_path / "t.db") as db:
        await apply_migrations(db, staged)
        await db.execute(
            "INSERT INTO run_telemetry(occurred_at, install_id, kind, model, status, cost_usd, "
            "attrs, send_attempts) VALUES ('2026-10-01', 'i', 'annotation', 'm', 'ok', 0.5, "
            "'{\"a\": 1}', 3)"
        )
        await db.commit()

        await apply_migrations(db, MIGRATIONS)

        cur = await db.execute("SELECT kind, cost_usd, attrs, send_attempts FROM run_telemetry")
        assert await cur.fetchall() == [("annotation", 0.5, '{"a": 1}', 3)]
        await db.execute(
            "INSERT INTO run_telemetry(occurred_at, install_id, kind, model, status) "
            "VALUES ('2026-10-02', 'i', 'live', 'm', 'ok')"
        )
        cur = await db.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='run_telemetry' "
            "ORDER BY name"
        )
        assert [r[0] for r in await cur.fetchall()] == [
            "idx_run_telemetry_estimator",
            "idx_run_telemetry_estimator_res",
            "idx_run_telemetry_occurred_at",
            "idx_run_telemetry_unsent",
        ]
