"""Tests for backend.app.archive.providers.fs.fields."""

from __future__ import annotations

from pathlib import Path

from backend.app.archive.providers.fs.fields import load_field_defs


FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "fs_archive"


def test_load_field_defs_missing_root_returns_empty(tmp_path: Path):
    assert load_field_defs(tmp_path) == []


def test_load_field_defs_from_fixture():
    defs = load_field_defs(FIXTURE_ROOT)
    by_id = {d.identifier: d for d in defs}
    assert "pragafilm.dekáda.natočení" in by_id
    assert by_id["pragafilm.dekáda.natočení"].type == "picklist"
    assert by_id["pragafilm.dekáda.natočení"].picklist_values == (
        "20.léta",
        "30.léta",
        "40.léta",
    )
    assert by_id["pragafilm.barva"].type == "bool"


def test_load_field_defs_malformed_returns_empty(tmp_path: Path, caplog):
    (tmp_path / ".archive").mkdir()
    (tmp_path / ".archive" / "fields.json").write_text("not json")
    with caplog.at_level("WARNING"):
        assert load_field_defs(tmp_path) == []


def test_load_field_defs_non_array_returns_empty(tmp_path: Path):
    (tmp_path / ".archive").mkdir()
    (tmp_path / ".archive" / "fields.json").write_text('{"a": 1}')
    assert load_field_defs(tmp_path) == []


def test_load_field_defs_unknown_keys_round_trip_into_provider_data(tmp_path: Path):
    (tmp_path / ".archive").mkdir()
    (tmp_path / ".archive" / "fields.json").write_text(
        '[{"identifier": "x", "name": "X", "type": "text", "vendor": {"a": 1}}]'
    )
    defs = load_field_defs(tmp_path)
    assert len(defs) == 1
    assert defs[0].provider_data.get("vendor") == {"a": 1}
