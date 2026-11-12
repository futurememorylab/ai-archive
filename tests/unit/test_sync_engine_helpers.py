"""Unit tests for the helpers extracted from `SyncEngine._tick`."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from backend.app.services.sync_engine import _backoff_elapsed, _change_set_for, _group_by_clip

_NOW = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)
_OP = '{"kind": "AddMarkers", "markers": []}'


def _due(row: dict) -> bool:
    return _backoff_elapsed(row, _NOW, base_s=2.0, max_s=10.0)


def _row(attempts: int, seconds_ago: float | None) -> dict:
    attempted = None if seconds_ago is None else (_NOW - timedelta(seconds=seconds_ago))
    return {"attempts": attempts, "attempted_at": attempted.isoformat() if attempted else None}


def test_backoff_lets_fresh_and_unstamped_rows_through():
    assert _due(_row(0, None))
    assert _due(_row(3, None))


def test_backoff_doubles_per_attempt_up_to_the_cap():
    assert not _due(_row(1, 1.9))  # 2s after 1 attempt
    assert _due(_row(1, 2.0))
    assert not _due(_row(3, 7.9))  # 8s after 3 attempts
    assert _due(_row(9, 10.0))  # capped at retry_max_s


def test_group_by_clip_keeps_first_seen_order_and_row_order():
    rows = [
        {"id": 1, "provider_id": "catdv", "provider_clip_id": "b"},
        {"id": 2, "provider_id": "catdv", "provider_clip_id": "a"},
        {"id": 3, "provider_id": "catdv", "provider_clip_id": "b"},
    ]
    groups = _group_by_clip(rows)
    assert list(groups) == [("catdv", "b"), ("catdv", "a")]
    assert [r["id"] for r in groups[("catdv", "b")]] == [1, 3]


def test_change_set_uses_the_freshest_expected_etag():
    rows = [
        {"op_json": _OP, "expected_etag": "old"},
        {"op_json": _OP, "expected_etag": "new"},
    ]
    cs = _change_set_for("catdv", "42", rows)
    assert cs.clip_key == ("catdv", "42")
    assert len(cs.ops) == 2
    assert cs.expected_etag == "new"
