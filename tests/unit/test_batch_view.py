from backend.app.ui.view_models import batch_view


def _row(**over):
    base = {
        "batch_key": "rg-1", "primary_job_id": 42, "started_at": "2026-06-02T09:04:00",
        "job_ids": [42, 43], "prompt_count": 1, "running_jobs": 0,
        "prompt_name": "Scénické značky CZ", "version_num": 5, "model": "gemini-2.5-pro",
        "ran": 12, "failed": 1, "completed": 11, "in_flight": 0, "awaiting_clips": 5,
    }
    base.update(over)
    return base


def test_basic_counts_and_reviewed_clamp():
    v = batch_view(_row())
    assert v["id"] == 42
    assert v["job_ids"] == [42, 43]
    assert v["ran"] == 12
    assert v["completed"] == 11
    assert v["failed"] == 1
    assert v["reviewed"] == 6          # completed(11) - awaiting(5)
    assert v["pct_done"] == 100        # (completed+failed)/ran = 12/12
    assert v["pct_reviewed"] == 55     # round(6/11*100)


def test_status_running():
    v = batch_view(_row(running_jobs=1, completed=4, failed=1))
    assert v["running"] is True
    assert v["status_state"] == "accent"
    assert v["status_label"] == "Running 5/12"


def test_status_awaiting_review_when_none_reviewed():
    v = batch_view(_row(running_jobs=0, completed=10, awaiting_clips=10))
    assert v["status_state"] == ""
    assert v["status_label"] == "Awaiting review"


def test_status_n_to_review():
    v = batch_view(_row(running_jobs=0, completed=10, awaiting_clips=3))
    assert v["status_label"] == "3 to review"


def test_status_applied_when_all_reviewed_and_synced():
    # All reviewed AND all write-backs confirmed on CatDV (synced_at set) → Applied.
    v = batch_view(_row(running_jobs=0, completed=10, awaiting_clips=0, syncing_clips=0))
    assert v["status_state"] == "ok"
    assert v["status_label"] == "Applied"


def test_status_syncing_when_reviewed_but_writes_unconfirmed():
    # All reviewed (awaiting 0) but write-backs not yet confirmed on CatDV
    # (e.g. CatDV offline, ops parked) → "Syncing N", NOT a premature "Applied".
    v = batch_view(_row(running_jobs=0, completed=10, awaiting_clips=0, syncing_clips=3))
    assert v["status_state"] == "accent"
    assert v["status_label"] == "Syncing 3"


def test_awaiting_review_takes_precedence_over_syncing():
    # Some clips still need review AND some are applied-but-unsynced → review wins.
    v = batch_view(_row(running_jobs=0, completed=10, awaiting_clips=3, syncing_clips=2))
    assert v["status_label"] == "3 to review"


def test_status_applied_when_syncing_field_absent():
    # Back-compat: rows without syncing_clips (older callers) still resolve to Applied.
    v = batch_view(_row(running_jobs=0, completed=10, awaiting_clips=0))
    assert v["status_label"] == "Applied"


def test_status_sync_failed_must_not_read_applied():
    # A write-back that exhausted retries (failed) or hit a conflict must NOT
    # let the batch read green "Applied" — it surfaces as a problem state so the
    # batch row and the topbar sync chip can never disagree.
    v = batch_view(_row(running_jobs=0, completed=10, awaiting_clips=0,
                        syncing_clips=0, problem_clips=2))
    assert v["status_state"] == "bad"
    assert v["status_label"] == "2 failed to sync"


def test_sync_problem_takes_precedence_over_syncing():
    # One clip's write failed while another is still in-flight → the failure
    # wins, so it isn't masked until the in-flight ops happen to settle.
    v = batch_view(_row(running_jobs=0, completed=10, awaiting_clips=0,
                        syncing_clips=1, problem_clips=1))
    assert v["status_state"] == "bad"
    assert v["status_label"] == "1 failed to sync"


def test_multi_prompt_label():
    v = batch_view(_row(prompt_count=2))
    assert v["prompt"] == "Scénické značky CZ + 1 more"


def test_missing_prompt_label():
    v = batch_view(_row(prompt_name=None))
    assert v["prompt"] == "(prompt unavailable)"


def test_review_href_falls_back_to_batch_filter_when_no_pending_clip():
    v = batch_view(_row(job_ids=[42, 43]))
    assert v["review_href"] == "/?batch=42,43&anno=for_review"


def test_review_href_targets_first_unreviewed_clip():
    v = batch_view(_row(first_pending_clip_id=882290))
    assert v["review_href"] == "/clips/882290?review=1"


def test_files_href_lists_all_batch_clips_no_status_filter():
    # Row click → all the batch's files (every job), no anno filter.
    v = batch_view(_row(job_ids=[42, 43]))
    assert v["files_href"] == "/?batch=42,43"


def test_zero_ran_no_divide_by_zero():
    v = batch_view(_row(ran=0, completed=0, failed=0, awaiting_clips=0))
    assert v["pct_done"] == 0
    assert v["pct_reviewed"] == 0
