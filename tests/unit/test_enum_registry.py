from backend.app.enums.registry import ENUM_REGISTRY, EnumSpec


def test_generation_model_enum_is_editable_with_one_default():
    spec = ENUM_REGISTRY["gemini_generation_model"]
    assert isinstance(spec, EnumSpec)
    assert spec.editable is True
    defaults = [v for v in spec.values if v.default]
    assert len(defaults) == 1, "exactly one seeded default"
    assert defaults[0].value == "gemini-2.5-flash-lite"
    assert len(spec.values) == 8


def test_toast_level_enum_is_fixed():
    spec = ENUM_REGISTRY["toast_level"]
    assert spec.editable is False
    assert [v.value for v in spec.values] == ["info", "success", "error"]


def test_editable_enums_never_seed_two_defaults():
    for spec in ENUM_REGISTRY.values():
        if spec.editable:
            assert sum(1 for v in spec.values if v.default) <= 1


def test_clip_publish_state_matches_literal():
    from typing import get_args
    from backend.app.enums.registry import ENUM_REGISTRY
    from backend.app.models.annotation import ClipPublishState

    spec = ENUM_REGISTRY["clip_publish_state"]
    assert spec.editable is False
    assert tuple(v.value for v in spec.values) == get_args(ClipPublishState)
