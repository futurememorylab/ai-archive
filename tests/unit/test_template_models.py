import pytest

from backend.app.models.template import TargetMap, Template


def test_template_minimal():
    t = Template(
        name="Scene markers",
        prompt="Identify scenes",
        output_schema={"type": "object"},
        target_map={"scenes": {"kind": "markers"}},
        model="gemini-2.5-pro",
    )
    assert t.name == "Scene markers"
    assert t.target_map.fields["scenes"].kind == "markers"


def test_target_map_accepts_field_entry():
    tm = TargetMap.model_validate(
        {"decade": {"kind": "field", "identifier": "pragafilm.dekáda.natočení"}}
    )
    entry = tm.fields["decade"]
    assert entry.kind == "field"
    assert entry.identifier == "pragafilm.dekáda.natočení"


def test_target_map_accepts_note_entry_with_mode():
    tm = TargetMap.model_validate(
        {"summary": {"kind": "note", "target": "pragafilm.popis.materialu", "mode": "append"}}
    )
    entry = tm.fields["summary"]
    assert entry.kind == "note"
    assert entry.target == "pragafilm.popis.materialu"
    assert entry.mode == "append"


def test_target_map_field_requires_identifier():
    with pytest.raises(ValueError):
        TargetMap.model_validate({"x": {"kind": "field"}})


def test_target_map_note_requires_target():
    with pytest.raises(ValueError):
        TargetMap.model_validate({"x": {"kind": "note"}})


def test_target_map_note_defaults_to_append():
    tm = TargetMap.model_validate(
        {"summary": {"kind": "note", "target": "notes"}}
    )
    assert tm.fields["summary"].mode == "append"
