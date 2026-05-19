import pytest

from backend.app.archive.model import FieldDef


def test_field_def_holds_identifier_name_and_type():
    fd = FieldDef(
        identifier="pragafilm.barva",
        name="Barva",
        type="bool",
        is_multi=False,
        is_editable=True,
    )
    assert fd.identifier == "pragafilm.barva"
    assert fd.type == "bool"
    assert fd.picklist_values is None
    assert fd.provider_data == {}


def test_field_def_with_picklist_values():
    fd = FieldDef(
        identifier="pragafilm.theme",
        name="Theme",
        type="multi-picklist",
        is_multi=True,
        is_editable=True,
        picklist_values=("rodina", "škola"),
        provider_data={"raw": "anything"},
    )
    assert fd.picklist_values == ("rodina", "škola")
    assert fd.is_multi is True


def test_field_def_is_frozen():
    fd = FieldDef(
        identifier="x",
        name="x",
        type="text",
        is_multi=False,
        is_editable=True,
    )
    with pytest.raises(Exception):
        fd.identifier = "y"  # type: ignore[misc]


def test_field_def_coerces_list_picklist_to_tuple():
    fd = FieldDef(
        identifier="x",
        name="x",
        type="picklist",
        is_multi=False,
        is_editable=True,
        picklist_values=["a", "b"],  # type: ignore[arg-type]
    )
    assert fd.picklist_values == ("a", "b")
