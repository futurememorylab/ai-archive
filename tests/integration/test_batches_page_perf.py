"""Pin the /batches read path against N+1 regressions (ADR 0046).

The whole page load (`routes.batches._load_batches_ctx`: list_batches +
batch_previews + the three telemetry sums) must issue a CONSTANT number of
SQL statements regardless of how many batches exist in the DB. The page is
capped at 50 batches, so every IN list stays inside one statement.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from backend.app.repositories.jobs import JobsRepo
from backend.app.repositories.run_telemetry import RunTelemetryRepo
from backend.app.routes.batches import _load_batches_ctx
from tests._helpers.query_count import assert_query_count


async def _seed_batches(db, n: int) -> None:
    now = datetime.now(UTC).isoformat()
    cur = await db.execute(
        "INSERT INTO prompts (name, description, archived, created_at, updated_at) "
        "VALUES ('p', NULL, 0, ?, ?)",
        (now, now),
    )
    pid = cur.lastrowid
    cur = await db.execute(
        "INSERT INTO prompt_versions "
        "(prompt_id, version_num, state, body, target_map, output_schema, model, "
        " created_at, updated_at) "
        "VALUES (?, 1, 'production', 'b', '{}', '{}', 'm', ?, ?)",
        (pid, now, now),
    )
    vid = cur.lastrowid
    for i in range(1, n + 1):
        cur = await db.execute(
            "INSERT INTO jobs (prompt_version_id, status, created_at, total_clips, run_group) "
            "VALUES (?, 'completed', ?, 3, ?)",
            (vid, now, f"rg-{i}"),
        )
        jid = cur.lastrowid
        for c in range(3):
            st = "error" if c == 0 else "review_ready"
            await db.execute(
                "INSERT INTO job_items (job_id, catdv_clip_id, status, error_message) "
                "VALUES (?, ?, ?, ?)",
                (jid, i * 10 + c, st, "boom" if st == "error" else None),
            )
    await db.commit()


@pytest.mark.asyncio
@pytest.mark.parametrize("n", [60, 200, 1000])
async def test_batches_read_path_is_constant_query_count(db, n):
    await _seed_batches(db, n)
    ctx = SimpleNamespace(db=db, jobs_repo=JobsRepo(), run_telemetry_repo=RunTelemetryRepo())
    async with assert_query_count(db, 5) as counter:
        page = await _load_batches_ctx(ctx, 50)
    # list_batches (1) + batch_previews (1) + cost sums / counts / estimates (3)
    assert counter.count == 5, f"[n={n}] expected 5 statements, got {counter.count}"
    assert len(page["batches"]) == 50
    assert all(len(b["thumbs"]) == 3 for b in page["batches"])
