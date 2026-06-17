import pytest

from backend.app.repositories.jobs import JobsRepo
from backend.app.repositories.prompts import PromptsRepo


async def _seed_version(
    db, *, name="Scénické značky CZ", model="gemini-2.5-pro"
) -> tuple[int, int]:
    prompts = PromptsRepo()
    pid, vid = await prompts.create_with_initial_version(
        db, name=name, description=None, body="p",
        target_map={"x": {"kind": "markers"}}, output_schema={}, model=model,
    )
    return pid, vid


async def _annotation_with_review(db, *, job_id, clip_id, applied, prompt_version_id):
    """Insert an annotation for (job, clip) and one review_item; applied=True
    sets applied_at so the clip counts as reviewed, else it's awaiting."""
    cur = await db.execute(
        "INSERT INTO annotations "
        "(catdv_clip_id, catdv_clip_name, prompt_version_id, job_id, model, "
        " prompt_used, raw_response, structured_output, clip_snapshot, created_at) "
        "VALUES (?, ?, ?, ?, 'm', 'p', '{}', '{}', '{}', '2026-06-02T00:00:00')",
        (clip_id, f"Clip_{clip_id}", prompt_version_id, job_id),
    )
    ann_id = cur.lastrowid
    await db.execute(
        "INSERT INTO review_items "
        "(annotation_id, studio_run_id, catdv_clip_id, kind, target_identifier, "
        " proposed_value, edited_value, decision, applied_at) "
        "VALUES (?, NULL, ?, 'marker', NULL, '{}', NULL, 'pending', ?)",
        (ann_id, clip_id, "2026-06-02T01:00:00" if applied else None),
    )
    await db.commit()


async def _pending_op(db, *, clip_id, status):
    """Insert a write-back queue row for a clip (the source the topbar sync chip
    and the batch 'Syncing' count both read)."""
    await db.execute(
        "INSERT INTO pending_operations "
        "(provider_id, provider_clip_id, op_kind, op_json, status, attempts, enqueued_at) "
        "VALUES ('catdv', ?, 'SetField', '{}', ?, 0, '2026-06-02T03:00:00')",
        (str(clip_id), status),
    )
    await db.commit()


@pytest.mark.asyncio
async def test_list_batches_groups_run_group_into_one_row(db):
    _, vid = await _seed_version(db)
    jobs = JobsRepo()
    # two per-kind jobs sharing a run_group = one batch
    j1 = await jobs.create_job(db, prompt_version_id=vid, clip_ids=[101, 102], run_group="rg-1")
    j2 = await jobs.create_job(db, prompt_version_id=vid, clip_ids=[201], run_group="rg-1")
    # mark progress: 101 done, 102 error, 201 done
    its1 = await jobs.list_items(db, j1)
    await jobs.update_item_status(db, its1[0].id, "review_ready")
    await jobs.update_item_status(db, its1[1].id, "error", error="boom")
    its2 = await jobs.list_items(db, j2)
    await jobs.update_item_status(db, its2[0].id, "review_ready")

    rows = await jobs.list_batches(db, limit=50)
    assert len(rows) == 1
    r = rows[0]
    assert r["batch_key"] == "rg-1"
    assert sorted(r["job_ids"]) == sorted([j1, j2])
    assert r["primary_job_id"] == min(j1, j2)
    assert r["ran"] == 3
    assert r["failed"] == 1
    assert r["completed"] == 2  # two review_ready
    assert r["prompt_name"] == "Scénické značky CZ"
    assert r["version_num"] == 1
    assert r["model"] == "gemini-2.5-pro"
    assert r["prompt_count"] == 1


@pytest.mark.asyncio
async def test_list_batches_singleton_job_without_run_group(db):
    _, vid = await _seed_version(db)
    jobs = JobsRepo()
    jid = await jobs.create_job(db, prompt_version_id=vid, clip_ids=[1])
    rows = await jobs.list_batches(db, limit=50)
    assert len(rows) == 1
    assert rows[0]["batch_key"] == f"job:{jid}"
    assert rows[0]["job_ids"] == [jid]


@pytest.mark.asyncio
async def test_list_batches_awaiting_clips_counts_unapplied_reviews(db):
    _, vid = await _seed_version(db)
    jobs = JobsRepo()
    jid = await jobs.create_job(db, prompt_version_id=vid, clip_ids=[101, 102])
    its = await jobs.list_items(db, jid)
    await jobs.update_item_status(db, its[0].id, "review_ready")
    await jobs.update_item_status(db, its[1].id, "review_ready")
    await _annotation_with_review(  # awaiting
        db, job_id=jid, clip_id=101, applied=False, prompt_version_id=vid
    )
    await _annotation_with_review(  # reviewed
        db, job_id=jid, clip_id=102, applied=True, prompt_version_id=vid
    )

    rows = await jobs.list_batches(db, limit=50)
    assert rows[0]["awaiting_clips"] == 1
    # "Review →" lands on the first un-reviewed clip of the batch.
    assert rows[0]["first_pending_clip_id"] == 101


@pytest.mark.asyncio
async def test_list_batches_syncing_clips_counts_active_pending_writebacks(db):
    # "Syncing" must read the SAME source as the topbar sync chip
    # (pending_operations), so the two can never contradict each other.
    _, vid = await _seed_version(db)
    jobs = JobsRepo()
    jid = await jobs.create_job(db, prompt_version_id=vid, clip_ids=[101, 102, 103])
    for it in await jobs.list_items(db, jid):
        await jobs.update_item_status(db, it.id, "review_ready")
    await _pending_op(db, clip_id=101, status="pending")  # in the queue → syncing
    await _pending_op(db, clip_id=102, status="applied")  # landed → NOT syncing
    # 103 has no queue row at all → not syncing
    rows = await jobs.list_batches(db, limit=50)
    assert rows[0]["syncing_clips"] == 1  # only 101


@pytest.mark.asyncio
async def test_list_batches_problem_clips_counts_failed_and_conflict_writebacks(db):
    # A write-back that exhausted retries (failed) or hit a conflict must surface
    # on the batch as a problem — NOT silently let it read green "Applied". Same
    # source as the topbar sync chip (pending_operations), so they agree. A
    # problem clip is NOT also counted as syncing.
    _, vid = await _seed_version(db)
    jobs = JobsRepo()
    jid = await jobs.create_job(db, prompt_version_id=vid, clip_ids=[101, 102, 103, 104])
    for it in await jobs.list_items(db, jid):
        await jobs.update_item_status(db, it.id, "review_ready")
    await _pending_op(db, clip_id=101, status="failed")    # problem
    await _pending_op(db, clip_id=102, status="conflict")  # problem
    await _pending_op(db, clip_id=103, status="pending")   # syncing, not a problem
    await _pending_op(db, clip_id=104, status="applied")   # landed → neither
    rows = await jobs.list_batches(db, limit=50)
    assert rows[0]["problem_clips"] == 2  # 101 + 102
    assert rows[0]["syncing_clips"] == 1  # only 103


@pytest.mark.asyncio
async def test_list_batches_excludes_studio_jobs(db):
    _, vid = await _seed_version(db)
    jobs = JobsRepo()
    await jobs.create_job(db, prompt_version_id=vid, clip_ids=[1], kind="studio")
    assert await jobs.list_batches(db, limit=50) == []


@pytest.mark.asyncio
async def test_count_total_batches(db):
    _, vid = await _seed_version(db)
    jobs = JobsRepo()
    await jobs.create_job(db, prompt_version_id=vid, clip_ids=[1], run_group="rg-1")
    await jobs.create_job(db, prompt_version_id=vid, clip_ids=[2], run_group="rg-1")
    await jobs.create_job(db, prompt_version_id=vid, clip_ids=[3])  # singleton
    assert await jobs.count_total_batches(db) == 2


@pytest.mark.asyncio
async def test_count_total_batches_excludes_studio_jobs(db):
    _, vid = await _seed_version(db)
    jobs = JobsRepo()
    await jobs.create_job(db, prompt_version_id=vid, clip_ids=[1])                    # normal
    await jobs.create_job(db, prompt_version_id=vid, clip_ids=[2], kind="studio")     # excluded
    assert await jobs.count_total_batches(db) == 1


@pytest.mark.asyncio
async def test_list_batches_running_jobs_and_in_flight(db):
    _, vid = await _seed_version(db)
    jobs = JobsRepo()
    # Two items: one will be in-flight (prompting), the other finished (review_ready).
    jid = await jobs.create_job(db, prompt_version_id=vid, clip_ids=[1, 2])
    await jobs.update_status(db, jid, "running")
    its = await jobs.list_items(db, jid)
    await jobs.update_item_status(db, its[0].id, "prompting")      # in-flight
    await jobs.update_item_status(db, its[1].id, "review_ready")   # finished

    rows = await jobs.list_batches(db, limit=50)
    assert len(rows) == 1
    r = rows[0]
    assert r["running_jobs"] == 1
    assert r["in_flight"] == 1


@pytest.mark.asyncio
async def test_failed_items_for_jobs_resolves_clip_name(db):
    _, vid = await _seed_version(db)
    jobs = JobsRepo()
    jid = await jobs.create_job(db, prompt_version_id=vid, clip_ids=[882290, 999])
    its = await jobs.list_items(db, jid)
    await jobs.update_item_status(db, its[0].id, "error", error="ProxyNotFound: not on disk")
    await jobs.update_item_status(db, its[1].id, "review_ready")  # not failed
    # name only known for 882290 via clip_cache
    await db.execute(
        "INSERT INTO clip_cache "
        "(provider_id, provider_clip_id, name, catalog_id, duration_secs, fps, "
        " canonical_json, provider_etag, fetched_at) "
        "VALUES ('catdv', '882290', 'Návštěva delegace', '7', 1.0, 25.0, '{}', NULL, "
        " '2026-06-02T00:00:00')"
    )
    await db.commit()

    fails = await jobs.failed_items_for_jobs(db, [jid])
    assert len(fails) == 1
    f = fails[0]
    assert f["job_id"] == jid
    assert f["catdv_clip_id"] == 882290
    assert f["error_message"] == "ProxyNotFound: not on disk"
    assert f["clip_name"] == "Návštěva delegace"


@pytest.mark.asyncio
async def test_failed_items_for_jobs_empty_input(db):
    assert await JobsRepo().failed_items_for_jobs(db, []) == []
