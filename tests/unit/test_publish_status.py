# tests/unit/test_publish_status.py
from backend.app.services.publish_status import resolve_publish_status


def test_failed_beats_everything():
    assert resolve_publish_status(has_draft=True, version_state="failed", version_num=2) == ("failed", 2)


def test_conflict_beats_publishing_and_draft():
    assert resolve_publish_status(has_draft=True, version_state="conflict", version_num=2) == ("conflict", 2)


def test_publishing_beats_draft():
    assert resolve_publish_status(has_draft=True, version_state="publishing", version_num=3) == ("publishing", 3)


def test_draft_when_no_active_version():
    assert resolve_publish_status(has_draft=True, version_state="live", version_num=1) == ("draft", 1)
    assert resolve_publish_status(has_draft=True, version_state=None, version_num=None) == ("draft", None)


def test_live_when_no_draft():
    assert resolve_publish_status(has_draft=False, version_state="live", version_num=4) == ("live", 4)
    assert resolve_publish_status(has_draft=False, version_state="superseded", version_num=4) == ("live", 4)


def test_none_when_nothing():
    assert resolve_publish_status(has_draft=False, version_state=None, version_num=None) == ("none", None)
