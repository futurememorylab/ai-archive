# tests/unit/test_version_switch_fidelity.py
"""Offline proof of the full switch path: a version's snapshot, through
_switch_ops, through build_put_payload, against a live clip that also carries a
human-added marker. The human marker must survive; our later markers must go."""
from backend.app.archive.providers.catdv.payload import build_put_payload
from backend.app.models.annotation import ClipVersion
from backend.app.services.publish_service import _switch_ops


def _ver(num, markers, state):
    return ClipVersion(
        catdv_clip_id=1,
        version_num=num,
        snapshot={"markers": markers, "fields": {}, "notes": None},
        origin="publish",
        publish_state=state,
    )


def test_switch_preserves_human_marker_and_drops_later_ours():
    v1 = _ver(1, [{"name": "A", "in": {"secs": 4.0}}], "superseded")
    v2 = _ver(
        2,
        [{"name": "A", "in": {"secs": 4.0}}, {"name": "B", "in": {"secs": 8.0}}],
        "live",
    )
    ops = _switch_ops(v1, [v2, v1])

    current = {
        "ID": 1,
        "fps": 25.0,
        "fields": {},
        "markers": [
            {"name": "A", "in": {"frm": 100, "fmt": 25.0, "secs": 4.0}},
            {"name": "B", "in": {"frm": 200, "fmt": 25.0, "secs": 8.0}},
            {"name": "HUMAN", "in": {"frm": 999, "fmt": 25.0, "secs": 39.96}},
        ],
    }
    payload = build_put_payload(current=current, ops=ops)
    assert sorted(m["name"] for m in payload["markers"]) == ["A", "HUMAN"]
