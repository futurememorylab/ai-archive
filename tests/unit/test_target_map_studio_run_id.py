"""target_map.expand: items inherit either annotation_id OR studio_run_id."""

import pytest

from backend.app.models.prompt import TargetMap
from backend.app.services.target_map import expand


def _tmap() -> TargetMap:
    return TargetMap.model_validate({
        "scenes": {"kind": "markers"},
        "summary_cz": {"kind": "note", "target": "pragafilm.popis.materialu"},
        "decade": {"kind": "field", "identifier": "pragafilm.dekada"},
    })


def test_expand_with_studio_run_id_sets_studio_run_id_only():
    structured = {
        "scenes": [{"in": {"secs": 1.0}, "out": {"secs": 2.0}, "name": "a"}],
        "summary_cz": "krátký",
        "decade": "30.léta",
    }
    items = expand(
        structured, _tmap(),
        studio_run_id=7, catdv_clip_id=42, clip_duration_secs=10.0,
    )
    assert items, "expected at least one item"
    for it in items:
        assert it.studio_run_id == 7
        assert it.annotation_id is None


def test_expand_with_annotation_id_sets_annotation_id_only():
    structured = {"scenes": [{"in": {"secs": 1.0}, "out": {"secs": 2.0}, "name": "a"}]}
    items = expand(
        structured, _tmap(),
        annotation_id=3, catdv_clip_id=42, clip_duration_secs=10.0,
    )
    for it in items:
        assert it.annotation_id == 3
        assert it.studio_run_id is None


def test_expand_rejects_both_owners():
    with pytest.raises(ValueError):
        expand({}, _tmap(), annotation_id=1, studio_run_id=1, catdv_clip_id=1)


def test_expand_rejects_neither_owner():
    with pytest.raises(ValueError):
        expand({}, _tmap(), catdv_clip_id=1)
