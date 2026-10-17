import pytest

from backend.app.repositories.jobs import JobsRepo
from backend.app.repositories.prompts import PromptsRepo
from tests._helpers.query_count import assert_query_count


async def _seed_version(
    db, *, name="Scénické značky CZ", model="gemini-2.5-pro"
) -> tuple[int, int]:
    prompts = PromptsRepo()
    pid, vid = await prompts.create_with_initial_version(
        db,
        name=name,
        description=None,
        body="p",
        target_map={"x": {"kind": "markers"}},
        output_schema={},
        model=model,
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
    await _pending_op(db, clip_id=101, status="failed")  # problem
    await _pending_op(db, clip_id=102, status="conflict")  # problem
    await _pending_op(db, clip_id=103, status="pending")  # syncing, not a problem
    await _pending_op(db, clip_id=104, status="applied")  # landed → neither
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
async def test_list_batches_running_jobs_and_in_flight(db):
    _, vid = await _seed_version(db)
    jobs = JobsRepo()
    # Two items: one will be in-flight (prompting), the other finished (review_ready).
    jid = await jobs.create_job(db, prompt_version_id=vid, clip_ids=[1, 2])
    await jobs.update_status(db, jid, "running")
    its = await jobs.list_items(db, jid)
    await jobs.update_item_status(db, its[0].id, "prompting")  # in-flight
    await jobs.update_item_status(db, its[1].id, "review_ready")  # finished

    rows = await jobs.list_batches(db, limit=50)
    assert len(rows) == 1
    r = rows[0]
    assert r["running_jobs"] == 1
    assert r["in_flight"] == 1


@pytest.mark.asyncio
async def test_clip_status_by_jobs_is_one_query(db):
    """Merged clip→status across a batch's jobs is ONE chunked query, not a
    list_items() per job — the polled batch-status fragment was an N+1 over the
    batch's jobs on every tick."""
    jobs = JobsRepo()
    _, vid = await _seed_version(db)
    j1 = await jobs.create_job(db, prompt_version_id=vid, clip_ids=[101, 102], run_group="rg")
    j2 = await jobs.create_job(db, prompt_version_id=vid, clip_ids=[201], run_group="rg")
    its1 = await jobs.list_items(db, j1)
    await jobs.update_item_status(db, its1[0].id, "applied")
    async with assert_query_count(db, max_n=1):
        m = await jobs.clip_status_by_jobs(db, [j1, j2])
    assert m == {101: "applied", 102: "pending", 201: "pending"}
    assert await jobs.clip_status_by_jobs(db, []) == {}


async def _review_item(db, *, ann_id, clip_id, kind, target, decision, applied):
    await db.execute(
        "INSERT INTO review_items "
        "(annotation_id, studio_run_id, catdv_clip_id, kind, target_identifier, "
        " proposed_value, edited_value, decision, applied_at) "
        "VALUES (?, NULL, ?, ?, ?, '{}', NULL, ?, ?)",
        (ann_id, clip_id, kind, target, decision, "2026-06-02T01:00:00" if applied else None),
    )
    await db.commit()


async def _annotation(db, *, job_id, clip_id, prompt_version_id) -> int:
    cur = await db.execute(
        "INSERT INTO annotations "
        "(catdv_clip_id, catdv_clip_name, prompt_version_id, job_id, model, "
        " prompt_used, raw_response, structured_output, clip_snapshot, created_at) "
        "VALUES (?, ?, ?, ?, 'm', 'p', '{}', '{}', '{}', '2026-06-02T00:00:00')",
        (clip_id, f"Clip_{clip_id}", prompt_version_id, job_id),
    )
    await db.commit()
    return cur.lastrowid


@pytest.mark.asyncio
async def test_list_batches_applied_draft_with_suggestions_is_not_awaiting(db):
    # Regression (#123 writeback redesign): applying a draft leaves its
    # suggestions (field/note items targeting a field the app does not own)
    # with applied_at NULL by design. They must not keep the batch in
    # "Awaiting review" once the owned items are applied.
    _, vid = await _seed_version(db)
    jobs = JobsRepo()
    jid = await jobs.create_job(db, prompt_version_id=vid, clip_ids=[101])
    its = await jobs.list_items(db, jid)
    await jobs.update_item_status(db, its[0].id, "review_ready")
    ann = await _annotation(db, job_id=jid, clip_id=101, prompt_version_id=vid)
    await _review_item(
        db, ann_id=ann, clip_id=101, kind="marker", target=None, decision="accepted", applied=True
    )
    await _review_item(
        db,
        ann_id=ann,
        clip_id=101,
        kind="field",
        target="decade",
        decision="accepted",
        applied=False,
    )
    await _review_item(
        db,
        ann_id=ann,
        clip_id=101,
        kind="note",
        target="notes",
        decision="pending",
        applied=False,
    )

    rows = await jobs.list_batches(db, limit=50)
    assert rows[0]["awaiting_clips"] == 0
    assert rows[0]["first_pending_clip_id"] is None


@pytest.mark.asyncio
async def test_list_batches_unapplied_owned_field_is_still_awaiting(db):
    # The suggestion exclusion must not swallow a real pending write to an
    # app-owned field.
    from backend.app.archive.model import AI_OWNED_FIELDS

    _, vid = await _seed_version(db)
    jobs = JobsRepo()
    jid = await jobs.create_job(db, prompt_version_id=vid, clip_ids=[101])
    ann = await _annotation(db, job_id=jid, clip_id=101, prompt_version_id=vid)
    await _review_item(
        db,
        ann_id=ann,
        clip_id=101,
        kind="field",
        target=AI_OWNED_FIELDS[0],
        decision="pending",
        applied=False,
    )
    rows = await jobs.list_batches(db, limit=50)
    assert rows[0]["awaiting_clips"] == 1


# --- Batch names (docs/specs/2026-10-17-batches-redesign-design.md) ---------


@pytest.mark.asyncio
async def test_batch_name_is_stored_and_read_from_the_primary_job(db):
    _, vid = await _seed_version(db)
    jobs = JobsRepo()
    j1 = await jobs.create_job(
        db, prompt_version_id=vid, clip_ids=[101], run_group="rg-1", batch_name="Tatry"
    )
    j2 = await jobs.create_job(
        db, prompt_version_id=vid, clip_ids=[102], run_group="rg-1", batch_name="Tatry"
    )
    rows = await jobs.list_batches(db, limit=50)
    assert rows[0]["batch_name"] == "Tatry"
    assert {j.id: j.batch_name for j in await jobs.list_jobs(db)} == {j1: "Tatry", j2: "Tatry"}


@pytest.mark.asyncio
async def test_rename_batch_updates_every_job_of_the_batch(db):
    _, vid = await _seed_version(db)
    jobs = JobsRepo()
    j1 = await jobs.create_job(
        db, prompt_version_id=vid, clip_ids=[1], run_group="rg", batch_name="a"
    )
    j2 = await jobs.create_job(
        db, prompt_version_id=vid, clip_ids=[2], run_group="rg", batch_name="a"
    )
    other = await jobs.create_job(db, prompt_version_id=vid, clip_ids=[3], batch_name="keep")

    await jobs.rename_batch(db, [j1, j2], "Tatry 1934")

    names = await jobs.batch_names_for_jobs(db, [j1, j2, other])
    assert names == {j1: "Tatry 1934", j2: "Tatry 1934", other: "keep"}


@pytest.mark.asyncio
async def test_batch_names_for_jobs_maps_every_job_to_its_primary_jobs_name(db):
    _, vid = await _seed_version(db)
    jobs = JobsRepo()
    j1 = await jobs.create_job(
        db, prompt_version_id=vid, clip_ids=[1], run_group="rg", batch_name="first"
    )
    # A second per-kind job created without a name still reads the batch's name.
    j2 = await jobs.create_job(db, prompt_version_id=vid, clip_ids=[2], run_group="rg")
    async with assert_query_count(db, 1):
        names = await jobs.batch_names_for_jobs(db, [j2])
    assert names == {j2: "first"}
    assert await jobs.batch_names_for_jobs(db, []) == {}
    assert j1 < j2


@pytest.mark.asyncio
async def test_batch_previews_first_five_distinct_clips_and_count(db):
    _, vid = await _seed_version(db)
    jobs = JobsRepo()
    await jobs.create_job(db, prompt_version_id=vid, clip_ids=[11, 12, 13, 14], run_group="big")
    # Second per-kind job of the same batch; clip 12 again counts once.
    await jobs.create_job(db, prompt_version_id=vid, clip_ids=[15, 16, 12], run_group="big")
    solo = await jobs.create_job(db, prompt_version_id=vid, clip_ids=[21])

    async with assert_query_count(db, 1):
        prev = await jobs.batch_previews(db, ["big", f"job:{solo}"], first=5)

    assert prev["big"] == {"clip_ids": [11, 12, 13, 14, 15], "clips": 6}
    assert prev[f"job:{solo}"] == {"clip_ids": [21], "clips": 1}
    assert await jobs.batch_previews(db, []) == {}


@pytest.mark.asyncio
async def test_batch_clip_rows_reports_each_clips_state(db):
    _, vid = await _seed_version(db)
    jobs = JobsRepo()
    jid = await jobs.create_job(db, prompt_version_id=vid, clip_ids=[1, 2, 3, 4, 5, 6])
    its = await jobs.list_items(db, jid)
    await jobs.update_item_status(db, its[0].id, "review_ready")  # 1: to review
    await jobs.update_item_status(db, its[1].id, "error", error="Gemini timed out")  # 2
    await jobs.update_item_status(db, its[2].id, "prompting")  # 3: running
    # 4 stays pending (waiting)
    await jobs.update_item_status(db, its[4].id, "review_ready")  # 5: saving
    await jobs.update_item_status(db, its[5].id, "review_ready")  # 6: reviewed
    await _annotation_with_review(db, job_id=jid, clip_id=1, applied=False, prompt_version_id=vid)
    await _annotation_with_review(db, job_id=jid, clip_id=5, applied=True, prompt_version_id=vid)
    await _annotation_with_review(db, job_id=jid, clip_id=6, applied=True, prompt_version_id=vid)
    await _pending_op(db, clip_id=5, status="pending")
    await db.execute(
        "INSERT INTO clip_cache (provider_id, provider_clip_id, name, catalog_id, "
        "duration_secs, fps, canonical_json, fetched_at) "
        "VALUES ('catdv', '1', 'Výlet do Tater', '7', 1, 25, '{}', '2026-01-01')"
    )
    await db.commit()

    async with assert_query_count(db, 1):
        rows = await jobs.batch_clip_rows(db, [jid])

    by_id = {r["clip_id"]: r for r in rows}
    assert by_id[1]["name"] == "Výlet do Tater"
    # Not in clip_cache: the name the clip's annotation recorded, else None.
    assert by_id[6]["name"] == "Clip_6"
    assert by_id[2]["name"] is None
    assert by_id[1]["awaiting"] and not by_id[6]["awaiting"]
    assert by_id[2]["item_status"] == "error"
    assert by_id[2]["error"] == "Gemini timed out"
    assert by_id[3]["item_status"] == "prompting"
    assert by_id[4]["item_status"] == "pending"
    assert by_id[5]["sync"] == "saving"
    assert by_id[6]["sync"] is None


async def _job_ids(db) -> list[int]:
    cur = await db.execute("SELECT id FROM jobs ORDER BY id")
    return [r[0] for r in await cur.fetchall()]


@pytest.mark.asyncio
async def test_delete_batch_removes_jobs_but_keeps_annotations(db):
    """Delete batch drops the batch record (every job sharing its run_group and
    their items); annotations the batch produced stay, detached from it, so
    drafts and accepted results remain on their clips."""
    _, vid = await _seed_version(db)
    jobs = JobsRepo()
    a = await jobs.create_job(db, prompt_version_id=vid, clip_ids=[1, 2], run_group="rg-del")
    b = await jobs.create_job(db, prompt_version_id=vid, clip_ids=[3], run_group="rg-del")
    keep = await jobs.create_job(db, prompt_version_id=vid, clip_ids=[4], run_group="rg-keep")
    for jid in (a, b, keep):
        await jobs.update_status(db, jid, "completed")
        for it in await jobs.list_items(db, jid):
            assert it.id is not None
            await jobs.update_item_status(db, it.id, "review_ready")
    await _annotation_with_review(db, job_id=a, clip_id=1, applied=False, prompt_version_id=vid)

    # A stale subset of the batch's job ids still deletes the whole batch.
    assert await jobs.delete_batch(db, [a]) == 2

    assert await _job_ids(db) == [keep]
    cur = await db.execute("SELECT COUNT(*) FROM job_items WHERE job_id IN (?, ?)", (a, b))
    assert (await cur.fetchone())[0] == 0
    cur = await db.execute("SELECT job_id FROM annotations WHERE catdv_clip_id = 1")
    assert [r[0] for r in await cur.fetchall()] == [None]
    cur = await db.execute("SELECT COUNT(*) FROM review_items WHERE catdv_clip_id = 1")
    assert (await cur.fetchone())[0] == 1


@pytest.mark.asyncio
async def test_delete_batch_refuses_a_running_batch(db):
    _, vid = await _seed_version(db)
    jobs = JobsRepo()
    jid = await jobs.create_job(db, prompt_version_id=vid, clip_ids=[1, 2], run_group="rg-run")
    first = (await jobs.list_items(db, jid))[0]
    assert first.id is not None
    await jobs.update_item_status(db, first.id, "review_ready")
    # The second clip is still pending: the batch is running.

    with pytest.raises(ValueError):
        await jobs.delete_batch(db, [jid])
    assert await _job_ids(db) == [jid]
