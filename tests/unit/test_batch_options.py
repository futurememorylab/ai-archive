"""Unit tests for the Batch-filter dropdown grouping (clips page)."""

from backend.app.models.job import Job
from backend.app.routes.pages.clips import _batch_options


def _job(jid, *, total=1, run_group=None, kind=None, notes=None, batch_name=None) -> Job:
    return Job(
        id=jid,
        prompt_version_id=1,
        status="running",
        total_clips=total,
        run_group=run_group,
        kind=kind,
        notes=notes,
        batch_name=batch_name,
    )


def test_per_kind_jobs_of_one_run_collapse_to_single_entry():
    # list_jobs returns DESC by id; #6 and #5 share a run group.
    jobs = [
        _job(6, total=3, run_group="abc"),
        _job(5, total=9, run_group="abc"),
    ]
    opts = _batch_options(jobs)
    assert len(opts) == 1
    assert opts[0]["value"] == "5,6"  # sorted, canonical
    assert "12" in opts[0]["label"]  # 9 + 3 clips summed


def test_ungrouped_jobs_stay_individual():
    jobs = [_job(7, total=2, kind="studio"), _job(4, total=1)]
    opts = _batch_options(jobs)
    assert [o["value"] for o in opts] == ["7", "4"]


def test_grouped_and_ungrouped_mix():
    jobs = [
        _job(8, total=1),  # single-clip annotate
        _job(6, total=3, run_group="abc"),  # bulk run, image
        _job(5, total=9, run_group="abc"),  # bulk run, video
    ]
    opts = _batch_options(jobs)
    values = [o["value"] for o in opts]
    assert values == ["8", "5,6"]


def test_named_batches_are_listed_by_name():
    # The batch reads its primary (lowest id) job's name.
    jobs = [
        _job(9, total=1, batch_name="Výlet do Tater"),
        _job(6, total=3, run_group="abc", batch_name="other kind"),
        _job(5, total=9, run_group="abc", batch_name="Tatry 1934"),
    ]
    assert [o["label"] for o in _batch_options(jobs)] == ["Výlet do Tater (1)", "Tatry 1934 (12)"]
