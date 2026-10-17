from backend.app.ui.view_models import batch_view


def _row(**over):
    base = {
        "batch_key": "rg-1",
        "primary_job_id": 42,
        "started_at": "2026-06-02T09:04:00",
        "job_ids": [42, 43],
        "prompt_count": 1,
        "running_jobs": 0,
        "prompt_name": "Scénické značky CZ",
        "version_num": 5,
        "model": "gemini-2.5-pro",
        "ran": 12,
        "failed": 1,
        "completed": 11,
        "in_flight": 0,
        "awaiting_clips": 5,
    }
    base.update(over)
    return base


def test_basic_counts():
    v = batch_view(_row())
    assert v["id"] == 42
    assert v["job_ids"] == [42, 43]
    assert v["ran"] == 12
    assert v["completed"] == 11
    assert v["failed"] == 1
    assert v["awaiting"] == 5


def test_name_comes_from_the_batch_with_a_fallback():
    assert batch_view(_row(batch_name="Tatry 1934"))["name"] == "Tatry 1934"
    assert batch_view(_row(batch_name=None))["name"] == "Batch #42"


def _head(**over):
    v = batch_view(_row(**over))
    return v["section"], v["headline"], v["headline_state"]


def test_running_batch():
    assert _head(running_jobs=1, completed=4, failed=1) == (
        "running",
        "Running · 5 of 12 done",
        "accent",
    )


def test_clips_to_review_need_you():
    assert _head(completed=10, awaiting_clips=3) == ("attention", "3 clips to review", "info")
    assert _head(completed=1, awaiting_clips=1, ran=1, failed=0)[1] == "1 clip to review"


def test_review_takes_precedence_over_cancelled_and_saving():
    assert _head(cancelled=1, completed=3, awaiting_clips=1, syncing_clips=2)[1] == (
        "1 clip to review"
    )


def test_write_back_problem_needs_you_and_beats_saving():
    # A failed / conflicted write-back must never read green, and must win over
    # an in-flight one so it isn't masked (same source as the topbar sync chip).
    assert _head(completed=10, awaiting_clips=0, failed=0, syncing_clips=1, problem_clips=2) == (
        "attention",
        "2 couldn't save to CatDV",
        "bad",
    )


def test_failed_clips_need_you():
    assert _head(completed=11, awaiting_clips=0, failed=1) == ("attention", "1 failed", "bad")


def test_saving_is_done_but_not_green():
    assert _head(completed=10, awaiting_clips=0, failed=0, syncing_clips=3) == (
        "done",
        "Saving 3 to CatDV…",
        "accent",
    )


def test_stopped_batch_without_output():
    # A job interrupted by a restart: never "Running", never green.
    assert _head(cancelled=1, ran=1, completed=0, failed=0, awaiting_clips=0) == (
        "done",
        "Stopped",
        "",
    )


def test_all_reviewed():
    assert _head(completed=10, awaiting_clips=0, failed=0) == ("done", "All reviewed", "ok")


def test_segments_and_legend_count_each_state():
    v = batch_view(
        _row(
            ran=20,
            completed=12,
            awaiting_clips=5,
            syncing_clips=1,
            problem_clips=1,
            failed=2,
            in_flight=0,
            cancelled=6,
        )
    )
    assert [(s["key"], s["count"]) for s in v["segments"]] == [
        ("reviewed", 5),
        ("review", 5),
        ("saving", 1),
        ("problem", 1),
        ("failed", 2),
        ("stopped", 6),
    ]
    assert [s["label"] for s in v["segments"]] == [
        "5 reviewed",
        "5 to review",
        "1 saving",
        "1 couldn't save",
        "2 failed",
        "6 stopped",
    ]


def test_running_segment_and_empty_states_are_dropped():
    v = batch_view(
        _row(running_jobs=1, ran=4, completed=1, awaiting_clips=1, failed=0, in_flight=3)
    )
    assert [(s["key"], s["count"]) for s in v["segments"]] == [("review", 1), ("running", 3)]


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


def test_zero_ran_has_no_segments():
    v = batch_view(_row(ran=0, completed=0, failed=0, awaiting_clips=0))
    assert v["segments"] == []
