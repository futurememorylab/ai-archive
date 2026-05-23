"""Pydantic models for Prompt + PromptVersion."""

import pytest

from backend.app.models.prompt import (
    Prompt,
    PromptVersion,
    PromptVersionState,
    TargetMap,
)


def test_prompt_minimal():
    p = Prompt(
        name="Scenes",
        description="d",
        archived=False,
        created_at="2026-05-01T00:00:00+00:00",
        updated_at="2026-05-01T00:00:00+00:00",
    )
    assert p.name == "Scenes"
    assert p.archived is False


def test_prompt_version_minimal():
    v = PromptVersion(
        prompt_id=1,
        version_num=1,
        state="draft",
        body="Hello",
        target_map={"scenes": {"kind": "markers"}},
        output_schema={"type": "object"},
        model="gemini-2.5-pro",
        created_at="2026-05-01T00:00:00+00:00",
        updated_at="2026-05-01T00:00:00+00:00",
    )
    assert v.state == "draft"
    assert v.target_map.fields["scenes"].kind == "markers"


def test_prompt_version_state_invalid_rejected():
    with pytest.raises(ValueError):
        PromptVersion(
            prompt_id=1,
            version_num=1,
            state="bogus",
            body="x",
            target_map={},
            output_schema={},
            model="m",
            created_at="t",
            updated_at="t",
        )


def test_target_map_field_requires_identifier():
    with pytest.raises(ValueError):
        TargetMap.model_validate({"x": {"kind": "field"}})


def test_target_map_note_requires_target():
    with pytest.raises(ValueError):
        TargetMap.model_validate({"x": {"kind": "note"}})


def test_target_entry_note_defaults_to_append():
    tm = TargetMap.model_validate({"s": {"kind": "note", "target": "notes"}})
    assert tm.fields["s"].mode == "append"


def test_prompt_version_state_literal():
    # Sanity: the type alias is what we expect.
    assert PromptVersionState.__args__ == ("draft", "production", "archived")
