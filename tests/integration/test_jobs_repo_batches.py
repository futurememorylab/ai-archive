import pytest

from backend.app.repositories.jobs import JobsRepo
from backend.app.repositories.prompts import PromptsRepo


async def _seed_version(db, *, name="Scénické značky CZ", model="gemini-2.5-pro") -> tuple[int, int]:
    prompts = PromptsRepo()
    pid, vid = await prompts.create_with_initial_version(
        db, name=name, description=None, body="p",
        target_map={"x": {"kind": "markers"}}, output_schema={}, model=model,
    )
    return pid, vid


async def _annotation_with_review(db, *, job_id, clip_id, applied):
    """Insert an annotation for (job, clip) and one review_item; applied=True
    sets applied_at so the clip counts as reviewed, else it's awaiting."""
    cur = await db.execute(
        "INSERT INTO annotations "
        "(catdv_clip_id, catdv_clip_name, prompt_version_id, job_id, model, "
        " prompt_used, raw_response, structured_output, clip_snapshot, created_at) "
        "VALUES (?, ?, 1, ?, 'm', 'p', '{}', '{}', '{}', '2026-06-02T00:00:00')",
        (clip_id, f"Clip_{clip_id}", job_id),
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
    await _annotation_with_review(db, job_id=jid, clip_id=101, applied=False)  # awaiting
    await _annotation_with_review(db, job_id=jid, clip_id=102, applied=True)   # reviewed

    rows = await jobs.list_batches(db, limit=50)
    assert rows[0]["awaiting_clips"] == 1


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
