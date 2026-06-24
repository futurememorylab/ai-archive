"""JobsRepo.phase_counts — groups job_items into the topbar phase breakdown
(caching / annotating / queued / done / error). See ADR 0093."""

from pathlib import Path
from typing import get_args

import pytest

from backend.app.db import open_db
from backend.app.migrations_runner import apply_migrations
from backend.app.models.job import ItemStatus
from backend.app.repositories.jobs import _PHASE_STATUSES, JobsRepo


@pytest.fixture
async def db(tmp_path: Path):
    cm = open_db(tmp_path / "t.db")
    conn = await cm.__aenter__()
    await apply_migrations(conn, Path("backend/migrations"))
    await conn.execute(
        "INSERT INTO prompts(id, name, archived, created_at, updated_at) "
        "VALUES (1, 'p', 0, '2026-05-26T00:00:00+00:00', '2026-05-26T00:00:00+00:00')"
    )
    await conn.execute(
        "INSERT INTO prompt_versions(id, prompt_id, version_num, state, body, target_map, "
        "output_schema, model, created_at, updated_at) "
        "VALUES (10, 1, 1, 'draft', 'b', '{}', '{}', 'm', "
        "'2026-05-26T00:00:00+00:00', '2026-05-26T00:00:00+00:00')"
    )
    await conn.commit()
    yield conn
    await cm.__aexit__(None, None, None)


@pytest.mark.asyncio
async def test_phase_counts_groups_items_by_phase(db):
    repo = JobsRepo()
    jid = await repo.create_job(db, prompt_version_id=10, clip_ids=[1, 2, 3, 4, 5, 6])
    items = await repo.list_items(db, jid)  # 6 items, all 'pending'
    await repo.update_item_status(db, items[0].id, "resolving")  # caching
    await repo.update_item_status(db, items[1].id, "uploading")  # caching
    await repo.update_item_status(db, items[2].id, "prompting")  # annotating
    await repo.update_item_status(db, items[3].id, "review_ready")  # done
    await repo.update_item_status(db, items[4].id, "error", error="boom")  # error
    # items[5] stays pending → queued

    pc = await repo.phase_counts(db, jid)
    assert pc == {"caching": 2, "annotating": 1, "queued": 1, "done": 1, "error": 1}


@pytest.mark.asyncio
async def test_phase_counts_does_not_count_unknown_status_as_done(db):
    """A status outside the known phase buckets must NOT be silently counted as
    'done' — the old catch-all (`done = everything not in_flight`) did, so a new
    or intermediate status would wrongly report the work finished. Finding #6."""
    repo = JobsRepo()
    jid = await repo.create_job(db, prompt_version_id=10, clip_ids=[1, 2])
    items = await repo.list_items(db, jid)
    await repo.update_item_status(db, items[0].id, "applied")  # genuinely done
    await db.execute(
        "UPDATE job_items SET status = 'future_unknown' WHERE id = ?", (items[1].id,)
    )
    await db.commit()
    pc = await repo.phase_counts(db, jid)
    assert pc["done"] == 1  # only the 'applied' item, not the unknown one


def test_phase_statuses_partition_item_status():
    """Every ItemStatus value maps to exactly one phase bucket, so adding a
    status forces a bucket choice instead of silently falling into 'done'."""
    assigned = [s for statuses in _PHASE_STATUSES.values() for s in statuses]
    assert sorted(assigned) == sorted(get_args(ItemStatus))  # complete, no overlap
