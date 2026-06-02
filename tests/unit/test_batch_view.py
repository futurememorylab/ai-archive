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


def test_status_applied_when_all_reviewed():
    v = batch_view(_row(running_jobs=0, completed=10, awaiting_clips=0))
    assert v["status_state"] == "ok"
    assert v["status_label"] == "Applied"


def test_multi_prompt_label():
    v = batch_view(_row(prompt_count=2))
    assert v["prompt"] == "Scénické značky CZ + 1 more"


def test_missing_prompt_label():
    v = batch_view(_row(prompt_name=None))
    assert v["prompt"] == "(prompt unavailable)"


def test_review_href_targets_batch_jobs():
    v = batch_view(_row(job_ids=[42, 43]))
    assert v["review_href"] == "/?batch=42,43&anno=for_review"


def test_zero_ran_no_divide_by_zero():
    v = batch_view(_row(ran=0, completed=0, failed=0, awaiting_clips=0))
    assert v["pct_done"] == 0
    assert v["pct_reviewed"] == 0
