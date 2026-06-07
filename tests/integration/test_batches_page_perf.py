"""Pin the /batches read path against N+1 regressions (ADR 0046).

list_batches(limit=50) + count_total_batches + failed_items_for_jobs +
cost_sums_by_job must issue a CONSTANT number of SQL statements regardless
of how many batches exist in the DB. Page is capped at 50 batches, so the
failed-items / cost IN lists stay inside one statement each.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from backend.app.repositories.jobs import JobsRepo
from backend.app.repositories.run_telemetry import RunTelemetryRepo
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
    repo = JobsRepo()
    tele = RunTelemetryRepo()
    async with assert_query_count(db, 6) as counter:
        rows = await repo.list_batches(db, limit=50)
        await repo.count_total_batches(db)
        job_ids = [jid for r in rows for jid in r["job_ids"]]
        await repo.failed_items_for_jobs(db, job_ids)
        await tele.cost_sums_by_job(db, job_ids)
    # list_batches (1) + count_total_batches (1) + failed_items_for_jobs (1)
    # + cost_sums_by_job (1)
    assert counter.count == 4, f"[n={n}] expected 4 statements, got {counter.count}"
