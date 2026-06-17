# tests/unit/test_publish_status.py
from backend.app.services.publish_status import resolve_publish_status


def test_conflict_beats_everything():
    assert resolve_publish_status(
        has_draft=True,
        conflict_write=True,
        failed_write=True,
        pending_write=True,
        live_version_num=2,
    ) == ("conflict", 2)


def test_failed_beats_publishing_and_draft():
    assert resolve_publish_status(
        has_draft=True, failed_write=True, pending_write=True, live_version_num=2
    ) == ("failed", 2)


def test_publishing_beats_draft():
    assert resolve_publish_status(has_draft=True, pending_write=True, live_version_num=3) == (
        "publishing",
        3,
    )


def test_draft_when_no_active_write():
    assert resolve_publish_status(has_draft=True, live_version_num=1) == ("draft", 1)
    assert resolve_publish_status(has_draft=True, live_version_num=None) == ("draft", None)


def test_live_when_no_draft_and_no_writes():
    assert resolve_publish_status(has_draft=False, live_version_num=4) == ("live", 4)


def test_none_when_nothing():
    assert resolve_publish_status(has_draft=False, live_version_num=None) == ("none", None)
